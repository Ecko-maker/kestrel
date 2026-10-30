"""Router v1 (Phase 4, design decision 28): the student answers first, the teacher takes over when it must.

Off by default. It exists only when the provider list is `routed` (KESTREL_PROVIDERS=routed or
--provider routed); every other configuration builds the usual FallbackLLM, and the agent runs exactly
as before. Settings (names in .env.example):

    KESTREL_ROUTER_STUDENT / _STUDENT_MODEL   the student's provider and model (default ollama)
    KESTREL_ROUTER_TEACHER / _TEACHER_MODEL   the teacher's (default groq)
    KESTREL_ROUTER_LINEAGE                    the student's lineage.json (required: its tool fingerprint)
    KESTREL_ROUTER_STUDENT_CTX                the student's context in tokens (default 16384, = num_ctx)
    KESTREL_ROUTER_CTX_MARGIN                 pre-route above this share of it (default 0.8)
    KESTREL_ROUTER_MAX_STUDENT_HEALTH         a routed bench run stops above this share of turns with
                                              student-health escalations, after 10 tasks (default 0.10)

Per turn (one user message):
- Pre-route rules, before the first model call: the tools Kestrel sends differ from the student's training
  fingerprint (#76), or the estimated prompt exceeds CTX_MARGIN x STUDENT_CTX (estimate_tokens is
  approximate, #11, and Ollama silently cuts an over-long prompt). The whole turn goes to the teacher.
- Escalation, checked on each student reply before the agent sees it. Quality: an invalid tool call
  (unknown tool or arguments the registry rejects, checked with ToolRegistry.prepare, which runs nothing)
  or an empty reply. Student health: the student's provider failed or timed out. The student's reply is
  discarded, never added to the transcript, and the teacher answers the same step. The agent's step limit
  is the third quality signal: Agent calls escalate("max_steps") and gives the teacher a fresh budget.
- Never on a refusal or a rejected approval: those reach the agent as ordinary replies and tool results.
- Once escalated, the rest of the turn stays with the teacher; the next turn starts with the student.

The student's reply is buffered (not streamed) until it passes the checks, so text from a discarded reply
is never shown. Everything that already ran stays in the transcript and never runs again: the router only
chooses who writes the next model reply; the agent and its approval gate run every tool call once.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kestrel.agent import estimate_tokens
from kestrel.distill import train_format
from kestrel.llm import CallInfo, ChatBackend, LLMError, Provider, TextCallback

ROUTED = "routed"
PRE_ROUTE, QUALITY, STUDENT_HEALTH = "pre_route", "quality", "student_health"
ROUTED_PROVIDER = Provider(ROUTED, "", None, "student -> teacher")

Validator = Callable[[str, Any], Any]  # ToolRegistry.prepare: raises ToolCallError, runs nothing


class RouterSetupError(LLMError):
    def __init__(self, message: str):
        super().__init__(ROUTED, message)


@dataclass(frozen=True)
class RouterConfig:
    student: str = "ollama"
    student_model: str | None = None
    teacher: str = "groq"
    teacher_model: str | None = None
    lineage: str | None = None
    student_ctx: int = 16384
    ctx_margin: float = 0.8
    max_student_health: float = 0.10

    @classmethod
    def from_env(cls) -> RouterConfig:
        env = os.environ.get
        return cls(
            student=env("KESTREL_ROUTER_STUDENT", "ollama"),
            student_model=env("KESTREL_ROUTER_STUDENT_MODEL") or None,
            teacher=env("KESTREL_ROUTER_TEACHER", "groq"),
            teacher_model=env("KESTREL_ROUTER_TEACHER_MODEL") or None,
            lineage=env("KESTREL_ROUTER_LINEAGE") or None,
            student_ctx=int(env("KESTREL_ROUTER_STUDENT_CTX", "16384")),
            ctx_margin=float(env("KESTREL_ROUTER_CTX_MARGIN", "0.8")),
            max_student_health=float(env("KESTREL_ROUTER_MAX_STUDENT_HEALTH", "0.10")),
        )

    def label(self) -> str:
        """The routed system's model name, resolved like LLM does (argument, <PROVIDER>_MODEL, default)."""
        from kestrel.llm import PROVIDERS

        def resolve(provider: str, model: str | None) -> str:
            default = PROVIDERS[provider].default_model if provider in PROVIDERS else "?"
            return model or os.getenv(f"{provider.upper()}_MODEL") or default

        return f"{resolve(self.student, self.student_model)} -> {resolve(self.teacher, self.teacher_model)}"

    def describe(self) -> dict[str, Any]:
        """For the agent fingerprint of a routed run: the settings, and the student's trained tool set by its
        hash, not the lineage file's path (a path is local, and moving the file changes nothing that matters)."""
        settings = asdict(self)
        lineage = settings.pop("lineage")
        try:
            settings["trained_tools_sha256"] = load_trained_tools(lineage)["sha256"]
        except RouterSetupError:
            settings["trained_tools_sha256"] = None  # the run itself refuses to start without it
        return settings


def load_trained_tools(lineage: str | None) -> dict[str, Any]:
    """The tool fingerprint the student was trained with, from its lineage.json."""
    if not lineage:
        raise RouterSetupError("KESTREL_ROUTER_LINEAGE is not set: the router needs the student's lineage.json")
    path = Path(lineage)
    if not path.is_file():
        raise RouterSetupError(f"no lineage.json at {path}")
    tools = json.loads(path.read_text(encoding="utf-8")).get("tool_schemas")
    if not tools:
        raise RouterSetupError(f"{path} has no tool_schemas: retrain with the current distill/train.py")
    return dict(tools)


@dataclass
class RoutedCallInfo(CallInfo):
    """CallInfo plus the routing decision, which the agent records on the llm_call span."""

    route: str | None = None  # "student" / "teacher"
    route_reason: str | None = None  # why the teacher answered (None: the student did)
    route_category: str | None = None  # pre_route / quality / student_health
    discarded_tokens: int = 0  # tokens of a student reply thrown away at this step


@dataclass
class Turn:
    route: str = "student"
    reason: str | None = None
    category: str | None = None
    student_steps: int = 0
    teacher_steps: int = 0
    discarded: list[str] = field(default_factory=list)  # reasons of discarded student replies

    @property
    def escalated(self) -> bool:
        return self.route == "teacher"


class RoutedLLM:
    """Same chat() interface as FallbackLLM. Not a fallback chain: a decision per turn and per step."""

    provider = ROUTED_PROVIDER
    supports_streaming = True

    def __init__(self, student: ChatBackend, teacher: ChatBackend, trained_tools: dict[str, Any], config: RouterConfig):
        self.student, self.teacher = student, teacher
        self.trained_tools = trained_tools
        self.config = config
        self.model = f"{student.model} -> {teacher.model}"
        self.last_provider: str | None = None
        self.last_call: CallInfo | None = None  # a RoutedCallInfo after each call
        self._validate: Validator | None = None
        self._turn: Turn | None = None
        self._finished: list[Turn] = []

    # The interface the CLI and bench use on a FallbackLLM -------------------------------------------

    @property
    def llms(self) -> list[ChatBackend]:
        return [self]

    def list_models(self) -> list[str]:
        return self.student.list_models()

    def complete(self, messages: list[dict]) -> str:
        return self.chat(messages)["content"] or ""

    # Called by the agent ---------------------------------------------------------------------------

    def begin_turn(self, validate: Validator | None = None) -> None:
        """A new user message: back to the student; pre-route rules are checked on the first call."""
        if self._turn is not None:
            self._finished.append(self._turn)
        self._turn = None
        self._validate = validate

    def escalate(self, reason: str) -> bool:
        """The agent's step limit was hit (reason "max_steps"). True if the teacher now takes over the turn
        (and should get a fresh budget); False if it already had it."""
        if self._turn is None or self._turn.escalated:
            return False
        self._switch(QUALITY, reason)
        return True

    def take_turns(self) -> list[Turn]:
        """The turns since the last call (for bench results), the current one included."""
        turns = self._finished + ([self._turn] if self._turn else [])
        self._finished, self._turn = [], None
        return turns

    # Routing -----------------------------------------------------------------------------------------

    def pre_route(self, messages: list[dict], tools: list[dict] | None) -> str | None:
        if problems := train_format.compare_tools(self.trained_tools, train_format.tools_fingerprint(tools or [])):
            more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            return f"tool_set: {problems[0]}{more}"
        estimate = estimate_tokens(messages) + len(json.dumps(tools or [])) // 4
        limit = int(self.config.ctx_margin * self.config.student_ctx)
        if estimate > limit:
            return f"context: ~{estimate} tokens > {self.config.ctx_margin:.0%} of {self.config.student_ctx}"
        return None

    def check(self, reply: dict) -> str | None:
        """A quality problem with a student reply, or None."""
        calls = reply.get("tool_calls") or []
        if not calls and not (reply.get("content") or "").strip():
            return "empty_reply"
        for call in calls:
            fn = call.get("function") or {}
            if self._validate is not None:
                try:
                    self._validate(fn.get("name", ""), fn.get("arguments"))
                except Exception as e:  # ToolCallError (and anything a broken call raises while parsing)
                    return f"invalid_tool_call: {e}"
        return None

    def _switch(self, category: str, reason: str) -> None:
        assert self._turn is not None
        self._turn.route, self._turn.category, self._turn.reason = "teacher", category, reason

    def chat(self, messages: list[dict], tools: list[dict] | None = None, on_text: TextCallback | None = None) -> dict:
        if self._turn is None:
            self._turn = Turn()
            if rule := self.pre_route(messages, tools):
                self._switch(PRE_ROUTE, rule)
        turn = self._turn
        self.last_provider = ROUTED  # every turn is the routed system's, for the bench's provider check
        if turn.escalated:
            return self._teacher(messages, tools, on_text, discarded_tokens=0)
        try:
            reply = self.student.chat(messages, tools)  # buffered: shown only once it passes the checks
        except LLMError as e:
            kind = "student_timeout" if "timed out" in str(e).lower() else "student_error"
            turn.discarded.append(kind)
            self._switch(STUDENT_HEALTH, f"{kind}: {e}")
            return self._teacher(messages, tools, on_text, discarded_tokens=0)
        info = getattr(self.student, "last_call", None)
        if problem := self.check(reply):
            turn.discarded.append(problem.split(":")[0])
            self._switch(QUALITY, problem)
            spent = (getattr(info, "input_tokens", 0) or 0) + (getattr(info, "output_tokens", 0) or 0)
            return self._teacher(messages, tools, on_text, discarded_tokens=spent)
        turn.student_steps += 1
        self.last_call = self._info(info, self.student, "student", None, None, 0)
        if on_text and reply.get("content"):
            on_text(reply["content"])  # the buffered text, delivered in one piece
        return reply

    def _teacher(
        self, messages: list[dict], tools: list[dict] | None, on_text: TextCallback | None, discarded_tokens: int
    ) -> dict:
        turn = self._turn
        assert turn is not None
        reply = self.teacher.chat(messages, tools, on_text) if on_text else self.teacher.chat(messages, tools)
        turn.teacher_steps += 1
        info = getattr(self.teacher, "last_call", None)
        self.last_call = self._info(info, self.teacher, "teacher", turn.reason, turn.category, discarded_tokens)
        return reply

    @staticmethod
    def _info(
        info: CallInfo | None,
        backend: ChatBackend,
        route: str,
        reason: str | None,
        category: str | None,
        discarded: int,
    ) -> RoutedCallInfo:
        base = info or CallInfo(getattr(backend.provider, "name", None), getattr(backend, "model", None))
        fields = {k: getattr(base, k) for k in CallInfo.__dataclass_fields__}
        return RoutedCallInfo(
            **fields, route=route, route_reason=reason, route_category=category, discarded_tokens=discarded
        )


def summarize_turns(turns: list[Turn], steps_per_turn: list[int], max_steps: int) -> dict[str, Any]:
    """A task's routing record for bench results."""
    reasons: dict[str, int] = {}
    categories: dict[str, int] = {}
    for t in turns:
        if t.escalated and t.reason:
            key = t.reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
            categories[t.category or "?"] = categories.get(t.category or "?", 0) + 1
    return {
        "turns": len(turns),
        "escalated_turns": sum(t.escalated for t in turns),
        "reasons": reasons,
        "categories": categories,
        "steps_per_turn": steps_per_turn,
        "over_max_steps": any(s > max_steps for s in steps_per_turn),
    }


def build_router(model: str | None, build: Callable[[list[str], str | None], Any]) -> RoutedLLM:
    """The routed system from the environment. `build` makes each side's provider chain (build_llm)."""
    config = RouterConfig.from_env()
    trained = load_trained_tools(config.lineage)
    student, _ = build([config.student], config.student_model or model)
    teacher, _ = build([config.teacher], config.teacher_model)
    return RoutedLLM(student, teacher, trained, config)
