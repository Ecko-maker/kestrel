"""Retries, fail-fast errors, and provider fallback, using a fake HTTP client (no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx  # the HTTP library openai v3 is built on
import openai
import pytest

from kestrel import llm as llm_module
from kestrel.llm import LLM, FallbackLLM, LLMError, build_llm

REQUEST = httpx.Request("POST", "http://test/chat/completions")


def status_error(cls, code: int, headers: dict | None = None):
    return cls(f"HTTP {code}", response=httpx.Response(code, request=REQUEST, headers=headers or {}), body=None)


def ok_response(text: str = "hi", tool_calls=None):
    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    """Raises/returns the scripted outcomes in order and records each request."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def fake_llm(outcomes, provider="ollama") -> tuple[LLM, FakeCompletions, list[float]]:
    llm = LLM(provider)
    completions = FakeCompletions(outcomes)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    waits: list[float] = []
    llm.sleep = waits.append
    return llm, completions, waits


@pytest.mark.parametrize(
    "error",
    [
        status_error(openai.RateLimitError, 429),
        status_error(openai.InternalServerError, 503),
        openai.APITimeoutError(request=REQUEST),
        openai.APIConnectionError(request=REQUEST),
    ],
)
def test_temporary_errors_are_retried_with_backoff(error):
    llm, completions, waits = fake_llm([error, error, ok_response("finally")])
    assert llm.chat([{"role": "user", "content": "x"}])["content"] == "finally"
    assert len(completions.requests) == 3
    assert 1 <= waits[0] <= 1.5 and 2 <= waits[1] <= 2.5  # exponential, with jitter


def test_gives_up_after_max_tries():
    error = status_error(openai.RateLimitError, 429)
    llm, completions, waits = fake_llm([error] * 3)
    with pytest.raises(LLMError, match=r"rate limited.*3 tries"):
        llm.chat([])
    assert len(completions.requests) == 3 and len(waits) == 2


def test_retry_after_header_is_respected():
    error = status_error(openai.RateLimitError, 429, {"retry-after": "7"})
    llm, _, waits = fake_llm([error, ok_response()])
    llm.chat([])
    assert waits == [7.0]


def test_long_retry_after_hands_over_immediately():
    error = status_error(openai.RateLimitError, 429, {"retry-after": "3600"})
    llm, completions, waits = fake_llm([error])
    with pytest.raises(LLMError):
        llm.chat([])
    assert len(completions.requests) == 1 and waits == []


@pytest.mark.parametrize(
    "error, message",
    [
        (status_error(openai.AuthenticationError, 401), "API key rejected"),
        (status_error(openai.PermissionDeniedError, 403), "API key rejected"),
        (status_error(openai.NotFoundError, 404), "not found"),
        (status_error(openai.BadRequestError, 400), "request rejected \\(400\\)"),
    ],
)
def test_permanent_errors_fail_fast(error, message):
    llm, completions, waits = fake_llm([error, ok_response()])
    with pytest.raises(LLMError, match=message):
        llm.chat([])
    assert len(completions.requests) == 1 and waits == []


def test_empty_response_is_an_error_not_a_crash():
    llm, _, _ = fake_llm([SimpleNamespace(choices=[])])
    with pytest.raises(LLMError, match="no choices"):
        llm.chat([])


def test_missing_key_is_clear(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMError, match="missing GROQ_API_KEY"):
        LLM("groq")


def test_gemini_extras_stripped_for_other_providers_and_placeholder_added_for_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "sig"}},
                },
                {"id": "2", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ],
        }
    ]
    groq_calls = LLM("ollama")._prepare(history)[0]["tool_calls"]
    assert all("extra_content" not in tc for tc in groq_calls)

    gemini_calls = LLM("gemini")._prepare(history)[0]["tool_calls"]
    assert gemini_calls[0]["extra_content"]["google"]["thought_signature"] == "sig"  # kept
    assert gemini_calls[1]["extra_content"]["google"]["thought_signature"]  # placeholder added
    assert "extra_content" not in history[0]["tool_calls"][1]  # original history not mutated


class StubLLM:
    def __init__(self, name, outcomes):
        self.provider = SimpleNamespace(name=name)
        self.model = f"{name}-model"
        self.outcomes = list(outcomes)
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"role": "assistant", "content": outcome}


def test_fallback_moves_to_next_provider_and_reports_it():
    a = StubLLM("gemini", [LLMError("gemini", "rate limited")])
    b = StubLLM("groq", ["from groq"])
    notices = []
    fb = FallbackLLM([a, b], on_fallback=notices.append)
    assert fb.chat([])["content"] == "from groq"
    assert fb.last_provider == "groq"
    assert "trying groq" in notices[0]


def test_failed_provider_cools_down_then_is_retried():
    a = StubLLM("gemini", [LLMError("gemini", "down"), "gemini is back"])
    b = StubLLM("groq", ["groq 1", "groq 2"])
    fb = FallbackLLM([a, b])
    now = [0.0]
    fb.clock = lambda: now[0]

    assert fb.chat([])["content"] == "groq 1"
    assert fb.chat([])["content"] == "groq 2"  # gemini skipped while cooling down
    assert a.calls == 1
    now[0] += llm_module.COOLDOWN + 1
    assert fb.chat([])["content"] == "gemini is back"


def test_all_providers_failing_raises_one_summary_error():
    fb = FallbackLLM([StubLLM("gemini", [LLMError("gemini", "429")]), StubLLM("groq", [LLMError("groq", "503")])])
    with pytest.raises(LLMError, match="gemini: 429 \\| groq: 503"):
        fb.chat([])


def test_build_llm_skips_providers_without_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    fb, skipped = build_llm(["gemini", "ollama"])
    assert [l.provider.name for l in fb.llms] == ["ollama"]
    assert "GEMINI_API_KEY" in skipped[0]


def test_build_llm_with_nothing_usable_fails_clearly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMError, match="no provider is usable"):
        build_llm(["gemini", "groq"])


class FakeChunk(SimpleNamespace):
    pass


def stream_of(*texts, usage=(10, 5)):
    chunks = [
        FakeChunk(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=t, tool_calls=None), finish_reason=None)],
            usage=None,
            model="m",
        )
        for t in texts
    ]
    chunks.append(
        FakeChunk(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None), finish_reason="stop")],
            usage=None,
            model="m",
        )
    )
    chunks.append(
        FakeChunk(choices=[], usage=SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]), model="m")
    )
    return iter(chunks)


def test_streaming_passes_text_pieces_and_reads_usage():
    llm, completions, _ = fake_llm([stream_of("Hel", "lo", "!")])
    pieces = []
    out = llm.chat([{"role": "user", "content": "hi"}], on_text=pieces.append)
    assert pieces == ["Hel", "lo", "!"] and out["content"] == "Hello!"
    assert completions.requests[0]["stream"] is True
    assert (llm.last_call.input_tokens, llm.last_call.output_tokens, llm.last_call.finish_reason) == (10, 5, "stop")


def test_empty_streamed_reply_is_retried_without_streaming():
    llm, completions, _ = fake_llm([stream_of(), ok_response("from the plain request")])
    pieces = []
    out = llm.chat([{"role": "user", "content": "hi"}], on_text=pieces.append)
    assert out["content"] == "from the plain request" and pieces == ["from the plain request"]
    assert completions.requests[0].get("stream") is True and "stream" not in completions.requests[1]
    assert llm.last_call.retries == 1


def test_provider_that_rejects_streaming_falls_back_and_stops_trying():
    llm, completions, _ = fake_llm([status_error(openai.BadRequestError, 400), ok_response("a"), ok_response("b")])
    assert llm.chat([], on_text=lambda t: None)["content"] == "a"
    assert llm.chat([], on_text=lambda t: None)["content"] == "b"
    assert [r.get("stream") for r in completions.requests] == [True, None, None]


# Recorded from a real Gemini free-tier 429 (2026-10-03): no Retry-After header, the wait is in
# the body as google.rpc.RetryInfo, and the body is a JSON list.
GEMINI_429 = json.loads((Path(__file__).parent / "fixtures" / "gemini_429_body.json").read_text(encoding="utf-8"))


def gemini_rate_limit():
    response = httpx.Response(429, request=REQUEST)
    return openai.RateLimitError("HTTP 429", response=response, body=GEMINI_429)


def test_gemini_retry_delay_is_read_from_the_body():
    llm, _, waits = fake_llm([gemini_rate_limit(), ok_response("after waiting")])
    llm.max_retry_wait = 65
    assert llm.chat([])["content"] == "after waiting"
    assert waits == [37.0]  # what the server asked, not our 1s guess


def test_long_gemini_delay_hands_over_to_the_fallback_at_once():
    llm, completions, waits = fake_llm([gemini_rate_limit()])  # default limit: 20s
    with pytest.raises(LLMError, match=r"rate limited \(429\), server asks to wait 37s"):
        llm.chat([])
    assert len(completions.requests) == 1 and waits == []


def test_last_provider_in_the_chain_waits_longer(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    fb, _ = build_llm(["groq", "ollama"])
    assert [b.max_retry_wait for b in fb.llms] == [llm_module.MAX_RETRY_WAIT, llm_module.LAST_RESORT_RETRY_WAIT]


def groq_tpm_limit():
    """Shape of a real Groq free-tier 429 (2026-10-03): tokens-per-minute limit, Retry-After header."""
    response = httpx.Response(429, request=REQUEST, headers={"retry-after": "6", "x-ratelimit-reset-tokens": "49.3s"})
    body = {
        "error": {
            "message": "Rate limit reached for model `openai/gpt-oss-120b` ... on tokens per minute (TPM): "
            "Limit 8000, Used 6577, Requested 2112. Please try again in 5.1675s.",
            "type": "tokens",
            "code": "rate_limit_exceeded",
        }
    }
    return openai.RateLimitError("HTTP 429", response=response, body=body)


def test_server_guided_rate_limits_are_followed_beyond_three_tries():
    llm, completions, waits = fake_llm([groq_tpm_limit()] * 4 + [ok_response("got through")])
    assert llm.chat([])["content"] == "got through"
    assert waits == [6.0] * 4 and len(completions.requests) == 5


def test_server_guided_waits_stop_after_max_guided_tries():
    llm, _, waits = fake_llm([groq_tpm_limit()] * 20)  # never clears
    with pytest.raises(LLMError, match="gave up after 8 tries"):
        llm.chat([])
    assert waits == [6.0] * 7  # 7 waits between 8 tries, 42s in total


def test_server_guided_waits_stop_at_the_time_budget():
    response = httpx.Response(429, request=REQUEST, headers={"retry-after": "15"})
    slow = openai.RateLimitError("HTTP 429", response=response, body=None)
    llm, _, waits = fake_llm([slow] * 20)
    with pytest.raises(LLMError, match="server asks to wait 15s"):
        llm.chat([])
    assert waits == [15.0] * 4  # 60s = 3 x the 20s limit; a 5th wait would exceed it


def test_unguided_errors_still_stop_after_three_tries():
    llm, completions, waits = fake_llm([status_error(openai.InternalServerError, 503)] * 5)
    with pytest.raises(LLMError, match="gave up after 3 tries"):
        llm.chat([])
    assert len(completions.requests) == 3 and len(waits) == 2  # ~1s, ~2s: guessed, so few


def test_gemini_bad_key_400_reads_as_a_key_problem(monkeypatch):
    """Recorded 2026-10-03: Gemini answers an invalid key with 400 INVALID_ARGUMENT, not 401."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = [{"error": {"code": 400, "message": "Please pass a valid API key", "status": "INVALID_ARGUMENT"}}]
    error = openai.BadRequestError(
        "Error code: 400 - " + json.dumps(body), response=httpx.Response(400, request=REQUEST), body=body
    )
    llm, _, waits = fake_llm([error], provider="gemini")
    with pytest.raises(LLMError, match=r"API key rejected \(400\)\. Check GEMINI_API_KEY in \.env\."):
        llm.chat([])
    assert waits == []  # permanent: no retries


MIXED = json.loads((Path(__file__).parent / "fixtures" / "mixed_provider_history.json").read_text(encoding="utf-8"))


def _tool_calls(messages):
    return [tc for m in messages if m.get("tool_calls") for tc in m["tool_calls"]]


def test_recorded_mixed_conversation_is_prepared_for_each_provider(monkeypatch):
    """Replays a live Gemini -> Groq -> Gemini conversation (2026-10-04) that Gemini accepted:
    Groq must not see Gemini's signatures; Gemini must get its own signature back unchanged and
    the documented placeholder on the call Groq made."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    history = MIXED["history"]

    to_groq = _tool_calls(LLM("groq")._prepare(history))
    assert [sorted(tc) for tc in to_groq] == MIXED["sent_to_groq_keys"]

    to_gemini = _tool_calls(LLM("gemini")._prepare(history))
    assert [sorted(tc) for tc in to_gemini] == MIXED["sent_to_gemini_keys"]
    signatures = [tc["extra_content"]["google"]["thought_signature"] for tc in to_gemini]
    assert signatures == ["<recorded-gemini-signature-1>", "skip_thought_signature_validator"]
    assert "extra_content" not in history[5]["tool_calls"][0]  # the stored history isn't modified
