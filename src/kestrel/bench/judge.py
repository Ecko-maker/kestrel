"""The LLM judge: grades open-ended answers against a task's rubric.

Deterministic checks cover what can be checked exactly (tools, files, numbers). The judge only
grades what can't, like whether a summary is faithful. It returns 0, 0.5 or 1 with a reason, and
it is calibrated against human labels (see calibrate.py) before its scores are trusted.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

JUDGE_PROMPT = """You grade a personal AI assistant's answer against a rubric. Be strict and literal.

Score:
  1   the answer fully satisfies the rubric
  0.5 partly: right idea but something the rubric asks for is missing or slightly wrong
  0   it fails the rubric, is wrong, or invents facts not supported by the tool results

Judge only against the rubric and the evidence shown. The tool results are the ground truth;
an answer that contradicts them is wrong. Ignore style unless the rubric mentions it.

Reply with JSON only: {"score": 0 | 0.5 | 1, "reason": "<one sentence>"}"""

# Bump on ANY change to what the judge sees: JUDGE_PROMPT, build_request, or the tool-result length in
# its log (runner.MAX_RESULT_CHARS_IN_LOG); a test pins all three. Every verdict records the version that
# produced it, so scores from different judges are never mixed silently. History (evals/CHANGELOG.md):
#   v1  2026-10-04  first prompt; tool results cut to 600 characters (results without a version are v1)
#   v2  2026-10-05  same prompt; tool results cut to 2,000 characters
JUDGE_VERSION = "v2"


def prompt_sha() -> str:
    return hashlib.sha256(JUDGE_PROMPT.encode("utf-8")).hexdigest()[:12]


class ChatLLM(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict: ...


@dataclass
class Verdict:
    score: float | None  # None: the judge failed to give a usable answer
    reason: str
    tokens: int = 0  # judge input + output tokens, so budgets include grading
    cached_tokens: int = 0


def build_request(prompts: list[str], rubric: str, tool_log: list[str], answer: str) -> list[dict]:
    conversation = "\n".join(f"USER: {p}" for p in prompts)
    tools = "\n".join(tool_log) if tool_log else "(no tools were called)"
    return [
        {"role": "system", "content": JUDGE_PROMPT},
        {
            "role": "user",
            "content": (
                f"## Conversation\n{conversation}\n\n## Tool calls and results\n{tools}\n\n"
                f"## Final answer\n{answer}\n\n## Rubric\n{rubric}"
            ),
        },
    ]


def parse_verdict(text: str) -> Verdict:
    """The first JSON object in the reply; tolerant of code fences and extra prose."""
    match = re.search(r"\{.*?\}", text or "", re.DOTALL)
    if not match:
        return Verdict(None, f"judge reply had no JSON: {(text or '')[:120]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Verdict(None, f"judge reply was not valid JSON: {match.group(0)[:120]!r}")
    score = data.get("score")
    if score not in (0, 0.5, 1):
        return Verdict(None, f"judge gave an invalid score {score!r}")
    return Verdict(float(score), str(data.get("reason", ""))[:300])


class Judge:
    version = JUDGE_VERSION

    def __init__(self, llm: ChatLLM, name: str = "judge"):
        self.llm = llm
        self.name = name

    def grade(self, prompts: list[str], rubric: str, tool_log: list[str], answer: str) -> Verdict:
        request = build_request(prompts, rubric, tool_log, answer)
        try:
            reply = self.llm.chat(request)
        except Exception as e:  # a judge outage must not crash the run; the task is marked ungraded
            return Verdict(None, f"judge error: {type(e).__name__}: {e}")
        text = reply.get("content") or ""
        verdict = parse_verdict(text)
        info = getattr(self.llm, "last_call", None)
        used_in, used_out = getattr(info, "input_tokens", None), getattr(info, "output_tokens", None)
        if used_in is None or used_out is None:  # not reported: estimate (~4 characters per token)
            used_in = sum(len(m["content"]) for m in request) // 4
            used_out = len(text) // 4
        verdict.tokens = used_in + used_out
        verdict.cached_tokens = getattr(info, "cached_tokens", None) or 0
        return verdict
