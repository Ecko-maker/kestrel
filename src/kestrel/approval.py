"""The approval gate: every risky tool call waits for the user.

Two layers, so a new way of approving (phone, web) only has to answer one question:
    Approver      UI only. review(tool_name, args, preview) -> Decision. Swappable.
    ApprovalGate  the rules. Decides from each tool's capabilities (permissions.py), and owns
                  the edit loop, session approvals and the audit log, so no Approver can
                  weaken them.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from kestrel import egress
from kestrel.permissions import Requirement, Session, required_approval
from kestrel.tools import Tool, forbidden_message
from kestrel.tracing import redact, redact_value

AUDIT_LOG = Path("logs") / "approvals.jsonl"
MAX_EDITS = 5


@dataclass
class Decision:
    status: Literal["approved", "edited", "rejected"]
    args: dict | None = None  # for "edited": the revised arguments, to be previewed again
    reason: str = ""  # for "rejected": passed back to the model
    for_session: bool = False  # for "approved": don't ask again for this tool this session


HIGHLIGHT_CONTEXT = 40  # characters shown on each side of a matched span
MAX_DECODED_CHARS = 300


@dataclass
class Highlight:
    """Where local text sits in a network call's arguments (as sent, or decoded)."""

    text: str  # the argument text (or one of its decoded forms) the span was found in
    start: int
    end: int
    source: str  # the tool whose result held it
    chars: int

    def parts(self) -> tuple[str, str, str]:
        """(before, match, after), with the context cut to HIGHLIGHT_CONTEXT characters."""
        before = self.text[max(0, self.start - HIGHLIGHT_CONTEXT) : self.start]
        after = self.text[self.end : self.end + HIGHLIGHT_CONTEXT]
        lead = "…" if self.start > HIGHLIGHT_CONTEXT else ""
        tail = "…" if self.end + HIGHLIGHT_CONTEXT < len(self.text) else ""
        return lead + before, self.text[self.start : self.end], after + tail


@dataclass
class Notice:
    """What a card shows besides the preview (tiers v2): why it appeared, the call's arguments
    decoded (e3), where local text sits in them, and recipients the user never typed (e2).
    For the person deciding only: the audit log and traces get hashes and lengths, never this."""

    reasons: list[str] = field(default_factory=list)
    decoded: list[str] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)
    recipient_warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.reasons or self.decoded or self.highlights or self.recipient_warnings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reasons": self.reasons,
            "decoded": self.decoded,
            "highlights": [
                dict(zip(("before", "match", "after"), h.parts(), strict=True), source=h.source, chars=h.chars)
                for h in self.highlights
            ],
            "recipient_warnings": self.recipient_warnings,
        }


def _span_pattern(span: str) -> re.Pattern[str]:
    """A normalized span (letters and digits) as it may appear in the original text: any case, with
    separators between its characters."""
    return re.compile(r"[\W_]*".join(re.escape(c) for c in span.replace(" ", "")), re.IGNORECASE)


def _locate(span: str, texts: list[str]) -> tuple[str, int, int] | None:
    pattern = _span_pattern(span)
    for text in texts:
        if m := pattern.search(text):
            return text, m.start(), m.end()
    return None


def build_notice(tool: Tool, args: dict, need: Requirement, session: Session | None = None) -> Notice:
    notice = Notice(reasons=list(need.reasons))
    if "network_egress" in tool.capabilities:
        sent = egress.call_text(args)
        notice.decoded = [d[:MAX_DECODED_CHARS] for d in egress.decoded_forms(sent)]
        for m in need.matches:
            if found := _locate(m.text, [sent, *egress.decoded_forms(sent)]):
                notice.highlights.append(Highlight(*found, source=m.source, chars=m.chars))
    if "sends" in tool.capabilities and session is not None:
        said = " ".join(session.user_texts).casefold()
        for address in re.split(r"[,;\s]+", str(args.get("to", ""))):
            if address and address.casefold() not in said:
                notice.recipient_warnings.append(f"You never typed this address: {address}")
    return notice


class Approver(Protocol):
    def review(
        self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False, notice: Notice | None = None
    ) -> Decision: ...


class DenyAllApprover:
    """The default when no one is there to ask: safe by default."""

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None) -> Decision:
        return Decision("rejected", reason="no approver is connected, so risky actions are disabled")


@dataclass
class GateResult:
    args: dict | None  # the arguments to run with, or None if the call must not run
    message: str = ""  # tool result to send the model when it doesn't run
    note: str = ""  # prefix for the tool result when it does run (e.g. "the user edited this")
    decision: str = ""  # for tracing: approved / edited / rejected / forbidden / session-approved / refused
    reason: str = ""


def _summarize(args: dict, limit: int = 120) -> dict:
    """Arguments for the audit log, with long text shortened."""
    return {
        k: (v if not isinstance(v, str) or len(v) <= limit else f"{v[:limit]}... ({len(v):,} chars)")
        for k, v in args.items()
    }


def rejection_message(tool_name: str, reason: str) -> str:
    because = f" Their reason: {reason!r}." if reason else " They gave no reason; ask them what they'd prefer."
    return (
        f"The user REJECTED this {tool_name} call, so it did not run.{because} "
        f"Do not repeat the same call. If their reason asks for changes, call {tool_name} again "
        f"with those changes (they will see and approve the new version); don't just show a draft. "
        f"If they don't want it done at all, stop and say so."
    )


class ApprovalGate:
    def __init__(self, approver: Approver | None = None, log_path: Path = AUDIT_LOG, max_edits: int = MAX_EDITS):
        self.approver = approver or DenyAllApprover()
        self.log_path = log_path
        self.max_edits = max_edits
        self.session_approved: set[str] = set()

    def requirement(self, tool: Tool, args: dict, session: Session | None = None) -> Requirement:
        """What this call needs now, from the tool's capabilities and the conversation so far
        (permissions.required_approval)."""
        return required_approval(tool.capabilities, tool.external, session, args)

    def check(
        self, tool: Tool, args: dict, need: Requirement | None = None, session: Session | None = None
    ) -> GateResult:
        """Decide whether a validated tool call may run, asking the user if it needs approval.
        `need`: the requirement if the caller already computed it (it is the same pure function).
        `session`: the conversation, for the card's notice (why it appeared, recipients never typed).
        An escalated call (a network call after local data was read) always gets its own card:
        no earlier "yes for this session" covers it, and none can be given for it."""
        need = need or self.requirement(tool, args, session)
        if need.level == "safe":
            return GateResult(args, decision="safe")
        if need.level == "forbidden":
            self.record(tool.name, args, "forbidden")
            return GateResult(None, forbidden_message(tool, args), decision="forbidden")
        why = need.escalated_by  # recorded with every decision on this call: hashes and lengths only
        spans = [m.text for m in need.matches]  # masked in the logged arguments
        allow_session = tool.allow_session and not why
        if tool.name in self.session_approved and allow_session:
            self.record(tool.name, args, "session-approved")
            return GateResult(args, decision="session-approved")

        original, edited = dict(args), False
        for _ in range(self.max_edits + 1):
            try:
                preview = tool.preview(args) if tool.preview else json.dumps(args, indent=2, ensure_ascii=False)
            except Exception as e:  # e.g. a path outside the workspace: refuse without bothering the user
                return GateResult(None, f"Error: {type(e).__name__}: {e}", decision="refused", reason=str(e))

            shown = need if args == original else self.requirement(tool, args, session)  # an edit: show it fresh
            notice = build_notice(tool, args, shown, session)
            decision = self.approver.review(
                tool.name, dict(args), preview, allow_session=allow_session, notice=notice or None
            )

            if decision.status == "edited":
                revised = decision.args if decision.args is not None else args
                if problem := tool.check_args(revised):
                    self.record(
                        tool.name, revised, "rejected", f"invalid edit: {problem}", escalated_by=why, hide=spans
                    )
                    return GateResult(
                        None,
                        rejection_message(tool.name, f"their edit was invalid ({problem})"),
                        decision="rejected",
                        reason=f"invalid edit: {problem}",
                    )
                args, edited = dict(revised), edited or revised != original
                continue  # show the new preview and ask again

            if decision.status == "rejected":
                self.record(tool.name, args, "rejected", decision.reason, escalated_by=why, hide=spans)
                return GateResult(
                    None, rejection_message(tool.name, decision.reason), decision="rejected", reason=decision.reason
                )

            status = "edited" if edited and args != original else "approved"
            if decision.for_session and allow_session:  # never for e.g. send_message
                self.session_approved.add(tool.name)
            self.record(
                tool.name, args, status, session=decision.for_session and allow_session, escalated_by=why, hide=spans
            )
            note = ""
            if status == "edited":
                ran_with = json.dumps(_summarize(args))
                note = f"Note: the user edited this call before approving it. It ran with: {ran_with}\n"
            return GateResult(args, note=note, decision=status)

        self.record(tool.name, args, "rejected", "too many edits", escalated_by=why, hide=spans)
        return GateResult(
            None,
            rejection_message(tool.name, "too many edits without a decision"),
            decision="rejected",
            reason="too many edits",
        )

    def record(
        self,
        tool_name: str,
        args: dict,
        decision: str,
        reason: str = "",
        session: bool = False,
        escalated_by: list[str] | None = None,
        hide: list[str] | None = None,
    ) -> None:
        """One line in the audit log. `hide`: spans of local text the content check found in the
        arguments; they are logged as their length only, so the log never becomes a copy of them."""
        for span in hide or ():
            pattern, mask = _span_pattern(span), f"[local text, {len(span.replace(' ', ''))} characters]"
            args = {k: pattern.sub(mask, v) if isinstance(v, str) else v for k, v in args.items()}
        entry: dict[str, object] = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": tool_name,
            "args": redact_value(_summarize(args)),  # same key redaction as traces
            "decision": decision,
            "reason": redact(reason),
        }
        if session:
            entry["session"] = True
        if escalated_by:  # why a card appeared: "taint:<tool>", "content:<hash>:<length>"; never the text
            entry["escalated_by"] = list(escalated_by)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- Terminal UI --------------------------------------------------------------

RED, GREEN, CYAN, YELLOW, BOLD, DIM, REVERSE, RESET = (
    "\033[31m",
    "\033[32m",
    "\033[36m",
    "\033[33m",
    "\033[1m",
    "\033[2m",
    "\033[7m",
    "\033[0m",
)


def colorize(preview: str) -> str:
    """Color diff lines: + green, - red, @@ cyan. Other previews (e.g. a message) stay plain,
    so a body line starting with "-" isn't mistaken for a deletion."""
    if not preview.startswith(("--- ", "New file:")):
        return preview
    out = []
    for line in preview.splitlines():
        if line.startswith(("+++", "---")):
            out.append(f"{BOLD}{line}{RESET}")
        elif line.startswith("+"):
            out.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out.append(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            out.append(f"{CYAN}{line}{RESET}")
        else:
            out.append(line)
    return "\n".join(out)


def default_editor() -> list[str]:
    command = os.getenv("VISUAL") or os.getenv("EDITOR") or ("notepad" if sys.platform == "win32" else "nano")
    return shlex.split(command, posix=sys.platform != "win32")


class TerminalApprover:
    def __init__(self, input_fn: Callable[[str], str] = input, print_fn: Callable[..., None] = print):
        self.input = input_fn
        self.print = print_fn

    def _ask(self, prompt: str) -> str | None:
        try:
            return self.input(prompt)
        except EOFError:
            return None  # no one to answer: treated as a rejection

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None) -> Decision:
        self.print(f"\n{YELLOW}{BOLD}== Approval needed: {tool_name} =={RESET}")
        if notice:
            self.show_notice(notice)
        self.print(colorize(preview))
        options = "[a]pprove  [e]dit  [r]eject" + ("  [s]ession-approve" if allow_session else "")
        while True:
            choice = self._ask(f"{YELLOW}{options} > {RESET}")
            if choice is None:
                return Decision("rejected", reason="no answer (input closed)")
            choice = choice.strip().lower()
            if choice in ("a", "approve"):
                return Decision("approved")
            if choice in ("s", "session"):
                if allow_session:
                    return Decision("approved", for_session=True)
                self.print(f"{RED}Session approval isn't allowed for {tool_name}; approve each one.{RESET}")
                continue
            if choice in ("r", "reject"):
                reason = self._ask("Reason (optional, Kestrel will see it): ")
                return Decision("rejected", reason=(reason or "").strip())
            if choice in ("e", "edit"):
                if (revised := self._edit(args)) is not None:
                    return Decision("edited", args=revised)
                continue
            self.print("Please type a, e, r" + (", or s" if allow_session else "") + ".")

    def show_notice(self, notice: Notice) -> None:
        for reason in notice.reasons:
            self.print(f"{YELLOW}Why: {reason}{RESET}")
        for h in notice.highlights:
            before, match, after = h.parts()
            label = f"{YELLOW}Local text ({h.chars} characters from {h.source}):{RESET}"
            self.print(f"{label} {before}{REVERSE}{match}{RESET}{after}")
        for d in notice.decoded:
            self.print(f"{CYAN}Decoded:{RESET} {d}")
        for warning in notice.recipient_warnings:
            self.print(f"{RED}{BOLD}{warning}{RESET}")

    def _edit(self, args: dict) -> dict | None:
        fields = [k for k, v in args.items() if isinstance(v, str)]
        if not fields:
            self.print("Nothing editable here.")
            return None
        default = max(fields, key=lambda k: len(args[k]))  # usually content/body
        field = (self._ask(f"Field to edit {fields} [{default}]: ") or "").strip() or default
        if field not in fields:
            self.print(f"Unknown field '{field}'.")
            return None
        how = (self._ask("[Enter] open in your editor, [t] retype here: ") or "").strip().lower()
        new_value = self._retype(field) if how == "t" else self._open_editor(args[field])
        return None if new_value is None else {**args, field: new_value}

    def _retype(self, field: str) -> str | None:
        self.print(f"Type the new {field}. Finish with a line containing only a dot (.)")
        lines = []
        while (line := self._ask("")) is not None and line.strip() != ".":
            lines.append(line)
        return "\n".join(lines) if lines else None

    def _open_editor(self, text: str) -> str | None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            subprocess.run([*default_editor(), path], check=True)  # waits until the editor closes
            return Path(path).read_text(encoding="utf-8").rstrip("\n")
        except (OSError, subprocess.CalledProcessError) as e:
            self.print(f"Couldn't open an editor ({e}); use [t] to retype instead.")
            return None
        finally:
            Path(path).unlink(missing_ok=True)
