"""One interface for every model provider (Gemini, Groq, Ollama via the OpenAI-compatible API).

Also the resilience layer: retries with backoff for errors that fix themselves
(rate limits, timeouts, 5xx), fast failure for errors that don't (bad key, unknown
model), and FallbackLLM to move on to the next provider when one is down.
"""

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import openai
from openai import OpenAI

REQUEST_TIMEOUT = 60.0  # seconds per request; override with KESTREL_REQUEST_TIMEOUT
MAX_TRIES = 3           # 1 try + 2 retries, waiting ~1s then ~2s
MAX_RETRY_WAIT = 20.0   # a longer Retry-After means "come back later": hand over to the fallback
COOLDOWN = 60.0         # after a provider fails, FallbackLLM tries the others first for this long


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None
    default_model: str


PROVIDERS = {
    "gemini": Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3-flash"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "ollama": Provider("ollama", "http://localhost:11434/v1", None, "qwen3:4b"),
}


@dataclass
class CallInfo:
    """What happened on the last chat() call, for tracing."""
    provider: str | None
    model: str | None
    input_tokens: int | None = None   # None: the API didn't report usage
    output_tokens: int | None = None
    finish_reason: str | None = None
    response_model: str | None = None
    retries: int = 0
    attempts: list[str] = field(default_factory=list)  # providers tried, in order


class LLMError(Exception):
    """A provider call failed in a way the caller should handle (not a bug in Kestrel)."""

    def __init__(self, provider: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider = provider


# Called before each retry wait: (provider, reason, seconds)
RetryCallback = Callable[[str, str, float], None]


def _retry_after(error: Exception) -> float | None:
    """Seconds the server asked us to wait, if it sent a numeric Retry-After header."""
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None  # an HTTP date; rare, so fall back to normal backoff


def _short(error: Exception) -> str:
    text = " ".join(str(getattr(error, "message", error)).split())
    return text if len(text) <= 200 else text[:197] + "..."


class LLM:
    def __init__(self, provider: str, model: str | None = None, on_retry: RetryCallback | None = None):
        if provider not in PROVIDERS:
            raise LLMError(provider, f"unknown provider. Choose from: {', '.join(PROVIDERS)}")
        self.provider = PROVIDERS[provider]
        api_key = "ollama"
        if self.provider.key_env:
            api_key = os.getenv(self.provider.key_env, "")
            if not api_key:
                raise LLMError(provider, f"missing {self.provider.key_env}. Add it to your .env file.")
        env_model = os.getenv(f"{provider.upper()}_MODEL")
        self.model = model or env_model or self.provider.default_model
        timeout = float(os.getenv("KESTREL_REQUEST_TIMEOUT", REQUEST_TIMEOUT))
        # max_retries=0: the SDK would otherwise retry silently; we do it ourselves, visibly.
        self.client = OpenAI(base_url=self.provider.base_url, api_key=api_key, timeout=timeout, max_retries=0)
        self.on_retry = on_retry
        self.sleep = time.sleep  # swapped out in tests
        self.last_provider: str | None = None
        self.last_call: CallInfo | None = None

    def _request(self, **kwargs):
        """One API call with retries for temporary failures and clear errors for permanent ones."""
        name = self.provider.name
        for attempt in range(1, MAX_TRIES + 1):
            try:
                return self.client.chat.completions.create(model=self.model, **kwargs)
            # Permanent: retrying can't help, so fail fast with a message that says what to fix.
            except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
                raise LLMError(name, f"API key rejected ({e.status_code}). Check {self.provider.key_env} in .env.") from e
            except openai.NotFoundError as e:
                raise LLMError(name, f"model '{self.model}' not found. See: kestrel --provider {name} --list-models") from e
            # Temporary: rate limit, timeout, connection trouble, server error.
            except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
                reason = {
                    openai.RateLimitError: "rate limited (429)",
                    openai.APITimeoutError: "timed out",
                    openai.APIConnectionError: "unreachable",
                }.get(type(e), f"server error ({getattr(e, 'status_code', '5xx')})")
                wait = _retry_after(e)
                if attempt == MAX_TRIES or (wait is not None and wait > MAX_RETRY_WAIT):
                    raise LLMError(name, f"{reason}, gave up after {attempt} tries") from e
                if wait is None:
                    wait = 2 ** (attempt - 1) + random.uniform(0, 0.5)  # 1s, 2s, plus jitter
                if self.on_retry:
                    self.on_retry(name, reason, wait)
                if self.last_call:
                    self.last_call.retries += 1
                self.sleep(wait)
            # Any other HTTP error (400 bad request, 413 too large, ...) won't fix itself either.
            except openai.APIStatusError as e:
                raise LLMError(name, f"request rejected ({e.status_code}): {_short(e)}") from e

    def _prepare(self, messages: list[dict]) -> list[dict]:
        """Adapt history for this provider. Tool calls carry provider extras (Gemini's
        thought signatures); other providers may reject those, and Gemini 3 rejects tool
        calls *without* one, e.g. calls made by Groq before a fallback switch."""
        prepared = []
        for m in messages:
            if m.get("tool_calls"):
                calls = []
                for tc in m["tool_calls"]:
                    tc = {k: v for k, v in tc.items() if k in ("id", "type", "function", "extra_content")}
                    if self.provider.name != "gemini":
                        tc.pop("extra_content", None)
                    elif "extra_content" not in tc:
                        # Google's documented placeholder for calls that didn't come from Gemini.
                        tc["extra_content"] = {"google": {"thought_signature": "skip_thought_signature_validator"}}
                    calls.append(tc)
                m = {**m, "tool_calls": calls}
            prepared.append(m)
        return prepared

    def complete(self, messages: list[dict]) -> str:
        return self.chat(messages)["content"] or ""

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Send the conversation plus tool schemas; return the assistant message as a dict
        with "content" (text or None) and, if the model wants tools, "tool_calls"."""
        kwargs = {"tools": tools} if tools else {}
        self.last_call = info = CallInfo(self.provider.name, self.model, attempts=[self.provider.name])
        response = self._request(messages=self._prepare(messages), **kwargs)
        if not getattr(response, "choices", None):
            raise LLMError(self.provider.name, "returned no choices (empty or blocked response)")
        usage = getattr(response, "usage", None)
        info.input_tokens = getattr(usage, "prompt_tokens", None)
        info.output_tokens = getattr(usage, "completion_tokens", None)
        info.finish_reason = getattr(response.choices[0], "finish_reason", None)
        info.response_model = getattr(response, "model", None)
        msg = response.choices[0].message
        out: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            # model_dump keeps provider extras (e.g. Gemini's thought signatures).
            out["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in msg.tool_calls]
        self.last_provider = self.provider.name
        return out

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except openai.APIError as e:
            raise LLMError(self.provider.name, f"could not list models: {_short(e)}") from e


# Called when a provider fails and the next one is tried: (error message)
FallbackCallback = Callable[[str], None]


class FallbackLLM:
    """Same chat() interface as LLM, but tries a chain of providers in order.

    A provider that just failed is put on a short cooldown so the next requests go
    straight to one that works, instead of paying its retry delays every time.
    """

    def __init__(self, llms: list[LLM], on_fallback: FallbackCallback | None = None):
        if not llms:
            raise ValueError("FallbackLLM needs at least one provider")
        self.llms = llms
        self.on_fallback = on_fallback
        self.clock = time.monotonic  # swapped out in tests
        self._down_until: dict[str, float] = {}
        self.last_provider: str | None = None
        self.last_call: CallInfo | None = None

    @property
    def provider(self) -> Provider:
        return self.llms[0].provider

    @property
    def model(self) -> str:
        return self.llms[0].model

    def _ordered(self) -> list[LLM]:
        now = self.clock()
        up = [l for l in self.llms if self._down_until.get(l.provider.name, 0) <= now]
        return up + [l for l in self.llms if l not in up]  # cooling-down ones as a last resort

    def complete(self, messages: list[dict]) -> str:
        return self.chat(messages)["content"] or ""

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        errors = []
        chain = self._ordered()
        self.last_call = summary = CallInfo(None, None)
        for i, llm in enumerate(chain):
            try:
                reply = llm.chat(messages, tools)
            except LLMError as e:
                summary.attempts.append(llm.provider.name)
                summary.retries += getattr(getattr(llm, "last_call", None), "retries", 0)
                errors.append(str(e))
                self._down_until[llm.provider.name] = self.clock() + COOLDOWN
                if self.on_fallback and i + 1 < len(chain):
                    self.on_fallback(f"{e} -> trying {chain[i + 1].provider.name}")
                continue
            self._down_until.pop(llm.provider.name, None)
            self.last_provider = llm.provider.name
            info = getattr(llm, "last_call", None) or CallInfo(llm.provider.name, getattr(llm, "model", None))
            summary.attempts.append(llm.provider.name)
            summary.retries += info.retries
            summary.provider, summary.model = llm.provider.name, info.model
            summary.input_tokens, summary.output_tokens = info.input_tokens, info.output_tokens
            summary.finish_reason, summary.response_model = info.finish_reason, info.response_model
            return reply
        raise LLMError("all providers", " | ".join(errors))

    def list_models(self) -> list[str]:
        return self.llms[0].list_models()


def build_llm(
    names: list[str],
    model: str | None = None,
    on_retry: RetryCallback | None = None,
    on_fallback: FallbackCallback | None = None,
) -> tuple[FallbackLLM, list[str]]:
    """Build the provider chain. `model` applies to the first provider only. Providers that
    can't be set up (e.g. missing key) are skipped; their reasons are returned as warnings."""
    llms, skipped = [], []
    for i, name in enumerate(names):
        try:
            llms.append(LLM(name, model if i == 0 else None, on_retry=on_retry))
        except LLMError as e:
            skipped.append(str(e))
    if not llms:
        raise LLMError("setup", "no provider is usable: " + " | ".join(skipped))
    return FallbackLLM(llms, on_fallback=on_fallback), skipped
