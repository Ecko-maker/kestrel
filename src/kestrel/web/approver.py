"""WebApprover: the approval gate's questions, answered from the browser.

It implements the same Approver interface as TerminalApprover, so the Agent and the
ApprovalGate don't change. The agent runs on a worker thread; review() sends an
approval_required event to the browser and blocks that thread until the browser
answers. No answer within the timeout, a closed browser, or anything other than an
explicit approve/edit counts as a rejection.
"""

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from kestrel.approval import Decision

APPROVAL_TIMEOUT = 300.0  # 5 minutes

# emit(event_type, **data): sends one event to the browser
Emit = Callable[..., None]


def preview_kind(tool_name: str, preview: str) -> str:
    """How the browser should render the preview."""
    if preview.startswith(("--- ", "New file:")):
        return "diff"
    if tool_name == "send_message":
        return "message"
    if preview.startswith("External MCP tool"):
        return "external"
    return "text"


def editable_field(args: dict) -> str | None:
    """The argument worth editing: the longest text one (content, body, ...)."""
    fields = [k for k, v in args.items() if isinstance(v, str)]
    return max(fields, key=lambda k: len(args[k])) if fields else None


class WebApprover:
    def __init__(self, timeout: float = APPROVAL_TIMEOUT):
        self.timeout = timeout
        self.emit: Emit = lambda event_type, **data: None  # bound to the agent's emit once it exists
        self._pending: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._closed = False

    def review(self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False) -> Decision:
        approval_id = uuid.uuid4().hex[:12]
        answer: Future = Future()
        with self._lock:
            if self._closed:
                return Decision("rejected", reason="the browser disconnected")
            self._pending[approval_id] = answer

        self.emit(
            "approval_required",
            approval_id=approval_id,
            tool=tool_name,
            args=args,
            preview=preview,
            kind=preview_kind(tool_name, preview),
            editable_field=editable_field(args),
            allow_session=allow_session,
            timeout_s=self.timeout,
        )
        try:
            response = answer.result(self.timeout)
        except FutureTimeout:
            response = {"decision": "reject", "reason": f"no answer within {self.timeout / 60:g} minutes"}
        finally:
            with self._lock:
                self._pending.pop(approval_id, None)

        decision = self._to_decision(response, allow_session)
        self.emit(
            "approval_resolved",
            approval_id=approval_id,
            tool=tool_name,
            decision=decision.status,
            reason=decision.reason,
            for_session=decision.for_session,
        )
        return decision

    def resolve(self, approval_id: str, response: dict) -> bool:
        """Called with the browser's answer. False if there's no such pending approval."""
        with self._lock:
            answer = self._pending.get(approval_id)
        if answer is None or answer.done():
            return False
        answer.set_result(response)
        return True

    def close(self, reason: str = "the browser disconnected") -> None:
        """Reject everything that's waiting, and anything asked from now on."""
        with self._lock:
            self._closed = True
            pending = list(self._pending.values())
        for answer in pending:
            if not answer.done():
                answer.set_result({"decision": "reject", "reason": reason})

    @staticmethod
    def _to_decision(response: dict, allow_session: bool) -> Decision:
        choice = response.get("decision")
        if choice == "approve":
            return Decision("approved", for_session=bool(response.get("for_session")) and allow_session)
        if choice == "edit" and isinstance(response.get("args"), dict):
            return Decision("edited", args=response["args"])
        # "reject", or anything unexpected: nothing runs without an explicit approval
        return Decision("rejected", reason=str(response.get("reason") or "")[:500])
