"""One interface for every model provider (Gemini, Groq, Ollama via the OpenAI-compatible API).

Also the resilience layer: retries with backoff for errors that fix themselves
(rate limits, timeouts, 5xx), fast failure for errors that don't (bad key, unknown
model), and FallbackLLM to move on to the next provider when one is down.
"""

import json
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import OpenAI

REQUEST_TIMEOUT = 60.0  # seconds per request; override with KESTREL_REQUEST_TIMEOUT
MAX_TRIES = 3  # 1 try + 2 retries, waiting ~1s then ~2s, when we have to guess the wait
MAX_GUIDED_TRIES = 8  # when the server says how long to wait (rate limits), follow it this many times...
RATE_LIMIT_BUDGET = 3  # ...as long as the total wait stays under 3x max_retry_wait
MAX_RETRY_WAIT = 20.0  # a longer requested wait means "come back later": hand over to the fallback
LAST_RESORT_RETRY_WAIT = 65.0  # ...unless no fallback is left: then waiting beats failing
COOLDOWN = 60.0  # after a provider fails, FallbackLLM tries the others first for this long


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None
    default_model: str


PROVIDERS = {
    # Defaults are measured, not guessed: on 2026-10-03, gemini-3.6-flash made 3/3 correct tool calls
    # at ~1.9s median on the free tier, while 3.7/3.8-flash were slow and returned 503s.
    "gemini": Provider(
        "gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3.6-flash"
    ),
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "ollama": Provider("ollama", "http://localhost:11434/v1", None, "qwen3:4b"),
    # Scripted replies for trying Kestrel with no keys (see demo.py); not an API.
    "demo": Provider("demo", "", None, "scripted"),
}


@dataclass
class CallInfo:
    """What happened on the last chat() call, for tracing."""

    provider: str | None
    model: str | None
    input_tokens: int | None = None  # None: the API didn't report usage
    output_tokens: int | None = None
    cached_tokens: int | None = None  # part of input_tokens served from the provider's cache
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
# Called with each piece of streamed text
TextCallback = Callable[[str], None]


class _StreamingRejected(Exception):
    """The provider refused a streaming request; fall back to a normal one."""


def _retry_after(error: Exception) -> float | None:
    """Seconds the server asked us to wait: a numeric Retry-After header, or Google's
    RetryInfo in the error body ({"@type": ".../google.rpc.RetryInfo", "retryDelay": "37s"}),
    which is how Gemini says it (no header)."""
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    if value is not None:
        try:
            return max(0.0, float(value))
        except ValueError:
            pass  # an HTTP date; rare
    return _retry_delay_in_body(getattr(error, "body", None))


# Groq names the limit it hit: "... on tokens per day (TPD): Limit 200000, Used ..." (or requests
# per day, RPD). Per-minute limits (TPM, RPM) free within a minute; daily ones within hours.
_DAILY_LIMIT = re.compile(r"per day|\((?:TPD|RPD)\)", re.IGNORECASE)
DAILY_LIMIT = "daily limit (429)"  # the reason in the LLMError text; the benchmark's wait-for-quota mode looks for it


def _error_text(error: Exception) -> str:
    """The message and the parsed body: the openai client puts the body in the message, but not always."""
    return f"{error} {json.dumps(getattr(error, 'body', None), default=str)}"


def _try_again_in(error: Exception) -> float | None:
    """Groq's "Please try again in 1h2m3.5s" in the error message, in seconds."""
    match = re.search(r"try again in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", _error_text(error))
    if not match:
        return None
    unit = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(n) * unit[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", match.group(1)))


def _retry_delay_in_body(body: Any) -> float | None:
    """Find a google.rpc.RetryInfo retryDelay ("37s", "1.5s") anywhere in a parsed error body."""
    if isinstance(body, list):
        found = (_retry_delay_in_body(item) for item in body)
        return next((d for d in found if d is not None), None)
    if not isinstance(body, dict):
        return None
    if str(body.get("@type", "")).endswith("google.rpc.RetryInfo"):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", str(body.get("retryDelay", "")))
        return float(match.group(1)) if match else None
    found = (_retry_delay_in_body(v) for v in body.values() if isinstance(v, (dict, list)))
    return next((d for d in found if d is not None), None)


def _cached(usage: Any) -> int | None:
    """Cached prompt tokens, if the provider reports them (Groq does; they don't count toward
    its rate limits)."""
    details = getattr(usage, "prompt_tokens_details", None)
    value = getattr(details, "cached_tokens", None)
    return int(value) if isinstance(value, int) else None


def _short(error: Exception) -> str:
    text = " ".join(str(getattr(error, "message", error)).split())
    return text if len(text) <= 200 else text[:197] + "..."


class LLM:
    def __init__(
        self,
        provider: str,
        model: str | None = None,
        on_retry: RetryCallback | None = None,
        max_retry_wait: float = MAX_RETRY_WAIT,
    ):
        self.max_retry_wait = max_retry_wait  # longest server-requested wait worth sitting out
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
        # <PROVIDER>_BASE_URL overrides the address, e.g. Ollama on the Docker host:
        # OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
        base_url = os.getenv(f"{provider.upper()}_BASE_URL") or self.provider.base_url
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
        self.on_retry = on_retry
        self.sleep = time.sleep  # swapped out in tests
        self.last_provider: str | None = None
        self.last_call: CallInfo | None = None
        self._streaming_ok = True

    def _request(self, **kwargs):
        """One API call with retries for temporary failures and clear errors for permanent ones."""
        name = self.provider.name
        waited = 0.0
        for attempt in range(1, MAX_GUIDED_TRIES + 1):
            try:
                return self.client.chat.completions.create(model=self.model, **kwargs)
            # Permanent: retrying can't help, so fail fast with a message that says what to fix.
            except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
                raise LLMError(
                    name, f"API key rejected ({e.status_code}). Check {self.provider.key_env} in .env."
                ) from e
            except openai.NotFoundError as e:
                hint = (
                    f"Run: ollama pull {self.model}  (or set OLLAMA_MODEL)"
                    if name == "ollama"
                    else f"See: kestrel --provider {name} --list-models"
                )
                raise LLMError(name, f"model '{self.model}' not found. {hint}") from e
            # Temporary: rate limit, timeout, connection trouble, server error.
            except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
                reason = {
                    openai.RateLimitError: "rate limited (429)",
                    openai.APITimeoutError: "timed out",
                    openai.APIConnectionError: "unreachable",
                }.get(type(e), f"server error ({getattr(e, 'status_code', '5xx')})")
                wait = _retry_after(e)
                if isinstance(e, openai.RateLimitError) and _DAILY_LIMIT.search(_error_text(e)):
                    reason = DAILY_LIMIT  # same handling; only the label (and a wait from the text) differ
                    wait = wait if wait is not None else _try_again_in(e)
                if wait is not None:
                    # The server told us how long to wait (e.g. Groq's tokens-per-minute limit):
                    # follow it, within a total budget; a long wait means hand over to the fallback.
                    if wait > self.max_retry_wait or waited + wait > self.max_retry_wait * RATE_LIMIT_BUDGET:
                        raise LLMError(name, f"{reason}, server asks to wait {wait:.0f}s") from e
                    if attempt == MAX_GUIDED_TRIES:
                        raise LLMError(name, f"{reason}, gave up after {attempt} tries") from e
                else:
                    if attempt >= MAX_TRIES:  # we'd only be guessing: give up after a few tries
                        raise LLMError(name, f"{reason}, gave up after {attempt} tries") from e
                    wait = 2 ** (attempt - 1) + random.uniform(0, 0.5)  # 1s, 2s, plus jitter
                waited += wait
                if self.on_retry:
                    self.on_retry(name, reason, wait)
                if self.last_call:
                    self.last_call.retries += 1
                self.sleep(wait)
            # Any other HTTP error (400 bad request, 413 too large, ...) won't fix itself either.
            except openai.APIStatusError as e:
                if e.status_code == 400 and "valid api key" in str(e).lower():  # Gemini says 400, not 401
                    raise LLMError(
                        name, f"API key rejected ({e.status_code}). Check {self.provider.key_env} in .env."
                    ) from e
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

    supports_streaming = True

    def chat(self, messages: list[dict], tools: list[dict] | None = None, on_text: TextCallback | None = None) -> dict:
        """Send the conversation plus tool schemas; return the assistant message as a dict
        with "content" (text or None) and, if the model wants tools, "tool_calls".
        With on_text, the reply is streamed and on_text gets each piece of text as it arrives."""
        kwargs = {"tools": tools} if tools else {}
        self.last_call = info = CallInfo(self.provider.name, self.model, attempts=[self.provider.name])
        prepared = self._prepare(messages)
        out: dict
        if on_text is not None and self._streaming_ok:
            try:
                out = self._chat_streaming(prepared, kwargs, on_text, info)
            except _StreamingRejected:
                self._streaming_ok = False  # this provider/model won't stream: stop trying
            else:
                if out["content"] or out.get("tool_calls"):
                    self.last_provider = self.provider.name
                    return out
                # An empty streamed reply: some servers (seen with Ollama) occasionally swallow a
                # tool call while streaming. Ask again without streaming rather than return nothing.
                info.retries += 1
        response = self._request(messages=prepared, **kwargs)
        if not getattr(response, "choices", None):
            raise LLMError(self.provider.name, "returned no choices (empty or blocked response)")
        usage = getattr(response, "usage", None)
        info.input_tokens = getattr(usage, "prompt_tokens", None)
        info.output_tokens = getattr(usage, "completion_tokens", None)
        info.cached_tokens = _cached(usage)
        info.finish_reason = getattr(response.choices[0], "finish_reason", None)
        info.response_model = getattr(response, "model", None)
        msg = response.choices[0].message
        out = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            # model_dump keeps provider extras (e.g. Gemini's thought signatures).
            out["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in msg.tool_calls]
        self.last_provider = self.provider.name
        if on_text is not None and out["content"]:
            on_text(out["content"])  # couldn't stream: deliver the text in one piece
        return out

    def _chat_streaming(self, prepared: list[dict], kwargs: dict, on_text: TextCallback, info: CallInfo) -> dict:
        """Stream a reply: pass text to on_text as it arrives, and rebuild tool calls, which
        arrive in fragments (id and name first, then the JSON arguments piece by piece)."""
        try:
            stream = self._request(messages=prepared, stream=True, stream_options={"include_usage": True}, **kwargs)
        except LLMError as e:
            if isinstance(e.__cause__, openai.BadRequestError):  # e.g. streaming or usage not supported
                raise _StreamingRejected() from e
            raise
        content: list[str] = []
        calls: dict[int, dict] = {}
        try:
            for chunk in stream:
                if usage := getattr(chunk, "usage", None):
                    info.input_tokens = getattr(usage, "prompt_tokens", None)
                    info.output_tokens = getattr(usage, "completion_tokens", None)
                    info.cached_tokens = _cached(usage)
                if getattr(chunk, "model", None):
                    info.response_model = chunk.model
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    info.finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    content.append(delta.content)
                    on_text(delta.content)
                for fragment in delta.tool_calls or []:
                    part = fragment.model_dump(exclude_none=True)
                    slot = calls.setdefault(
                        part.get("index", len(calls)),
                        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    slot["id"] = part.get("id") or slot["id"]
                    fn = part.get("function") or {}
                    if fn.get("name") and not slot["function"]["name"]:
                        slot["function"]["name"] = fn["name"]
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                    for key, value in part.items():  # provider extras, e.g. Gemini's thought signature
                        if key not in ("index", "id", "type", "function"):
                            slot[key] = value
        except openai.APIError as e:
            raise LLMError(self.provider.name, f"stream broke off: {_short(e)}") from e

        out: dict = {"role": "assistant", "content": "".join(content) or None}
        if calls:
            out["tool_calls"] = [c | {"id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items())]
        return out

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except openai.APIError as e:
            raise LLMError(self.provider.name, f"could not list models: {_short(e)}") from e


# Called when a provider fails and the next one is tried: (error message)
FallbackCallback = Callable[[str], None]


class ChatBackend(Protocol):
    """What FallbackLLM needs from each provider: LLM, or DemoLLM."""

    provider: Any
    model: str
    last_call: CallInfo | None

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, on_text: TextCallback | None = None
    ) -> dict: ...

    def list_models(self) -> list[str]: ...


class FallbackLLM:
    """Same chat() interface as LLM, but tries a chain of providers in order.

    A provider that just failed is put on a short cooldown so the next requests go
    straight to one that works, instead of paying its retry delays every time.
    """

    def __init__(self, llms: list[ChatBackend], on_fallback: FallbackCallback | None = None):
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

    def _ordered(self) -> list[ChatBackend]:
        now = self.clock()
        up = [l for l in self.llms if self._down_until.get(l.provider.name, 0) <= now]
        return up + [l for l in self.llms if l not in up]  # cooling-down ones as a last resort

    def complete(self, messages: list[dict]) -> str:
        return self.chat(messages)["content"] or ""

    supports_streaming = True

    def chat(self, messages: list[dict], tools: list[dict] | None = None, on_text: TextCallback | None = None) -> dict:
        errors = []
        chain = self._ordered()
        self.last_call = summary = CallInfo(None, None)
        for i, llm in enumerate(chain):
            try:
                reply = llm.chat(messages, tools, on_text) if on_text else llm.chat(messages, tools)
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
            summary.cached_tokens = info.cached_tokens
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
    llms: list[ChatBackend] = []
    skipped: list[str] = []
    for i, name in enumerate(names):
        if name == "demo":
            from kestrel.demo import DemoLLM

            llms.append(DemoLLM())
            continue
        try:
            last = i == len(names) - 1  # nothing to fall back to: sit out longer rate-limit waits
            wait_limit = LAST_RESORT_RETRY_WAIT if last else MAX_RETRY_WAIT
            llms.append(LLM(name, model if i == 0 else None, on_retry=on_retry, max_retry_wait=wait_limit))
        except LLMError as e:
            skipped.append(str(e))
    if not llms:
        raise LLMError("setup", "no provider is usable: " + " | ".join(skipped))
    return FallbackLLM(llms, on_fallback=on_fallback), skipped
