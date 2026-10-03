"""Kestrel: a personal AI agent. Step 7: a web console."""

import argparse
import io
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from kestrel.agent import MAX_CONTEXT_TOKENS, Agent
from kestrel.approval import ApprovalGate, TerminalApprover
from kestrel.demo import DEMO_NOTICE, DEMO_PROMPTS
from kestrel.llm import PROVIDERS, LLMError, build_llm
from kestrel.mcp_client import MCPManager, load_config
from kestrel.tools import registry
from kestrel.tracing import Tracer

DIM, YELLOW, RESET = "\033[2m", "\033[33m", "\033[0m"


def dim(text: str) -> None:
    print(f"{DIM}{text}{RESET}")


def format_call(name: str, args_json: str) -> str:
    """get_current_time(timezone="Asia/Tokyo") style, for display."""
    try:
        args = json.loads(args_json or "{}")
        shown = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    except json.JSONDecodeError, AttributeError:
        shown = args_json
    return f"{name}({shown})"


class TerminalEvents:
    """Shows agent events in the terminal: tool steps dimly, the answer streamed as it arrives."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.open_line = False  # mid-way through printing streamed text
        self.streamed: dict[int, str] = {}  # step -> text streamed in that step
        self.args: dict[str, str] = {}  # call_id -> arguments, for the [tool] line

    def _end_line(self) -> None:
        if self.open_line:
            print()
            self.open_line = False

    def __call__(self, event: dict) -> None:
        kind = event["type"]
        if kind == "step_started" and event["step"] == 1:
            self.streamed.clear()
        elif kind == "text_delta":
            if not self.open_line:
                print("\nkestrel > ", end="")
                self.open_line = True
            print(event["text"], end="", flush=True)
            self.streamed[event["step"]] = self.streamed.get(event["step"], "") + event["text"]
        elif kind == "tool_call":
            self._end_line()  # text before a tool call was the model thinking aloud
            self.args[event["call_id"]] = event["arguments"]
        elif kind == "tool_result":
            args = self.args.get(event["call_id"], "{}")
            one_line = " ".join(event["result"].split())
            if len(one_line) > 100:
                one_line = one_line[:97] + "..."
            dim(f"[tool] {format_call(event['name'], args)} -> {one_line}")
            if self.debug:
                dim(f"[debug] call: {json.dumps({'name': event['name'], 'arguments': args})}")
                dim(f"[debug] result: {json.dumps(event['result'], ensure_ascii=False)}")
        elif kind == "answer":
            self._end_line()
            if event["text"] not in self.streamed.values():  # e.g. max_steps or error messages
                print(f"\nkestrel > {event['text']}")


def provider_chain(cli_provider: str | None) -> list[str]:
    """--provider pins one provider; otherwise KESTREL_PROVIDERS (e.g. "gemini,groq,ollama")."""
    if cli_provider:
        return [cli_provider]
    names = [n.strip().lower() for n in (os.getenv("KESTREL_PROVIDERS") or "gemini").split(",") if n.strip()]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        sys.exit(f"KESTREL_PROVIDERS has unknown provider(s) {unknown}. Choose from: {', '.join(PROVIDERS)}")
    return names


def parse_feedback(text: str) -> tuple[str, str] | None:
    """'/good' or '/bad  too long' -> ("bad", "too long"); anything else -> None."""
    command, _, note = text.strip().partition(" ")
    if command.lower() in ("/good", "/bad"):
        return command[1:].lower(), note.strip()
    return None


def run_report(args: argparse.Namespace, tracer: Tracer) -> None:
    from kestrel import trace_report
    from kestrel.tools import registry

    with tracer.connect() as conn:
        if args.command == "traces":
            trace_report.print_traces(conn, args.n)
        elif args.command == "trace":
            try:
                trace_id = tracer.find_trace_id(args.id)
            except LookupError as e:
                sys.exit(str(e))
            trace_report.print_trace(conn, trace_id)
        elif args.command == "stats":
            trace_report.print_stats(conn)
        elif args.command == "export":
            out = Path(args.out)
            written, skipped = trace_report.export(conn, args.rated, out, registry.schemas())
            print(
                f"Wrote {written} trace(s) to {out}"
                + (f" ({skipped} skipped: no stored text, errored, or demo)" if skipped else "")
            )


def main() -> None:
    load_dotenv()  # reads your API keys from the .env file
    if isinstance(sys.stdout, io.TextIOWrapper):  # odd characters in files/web results can't crash printing
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(prog="kestrel", description="Chat with Kestrel, or inspect its traces.")
    parser.add_argument(
        "--provider",
        choices=list(PROVIDERS),
        help="use only this provider (default: the KESTREL_PROVIDERS chain from .env)",
    )
    parser.add_argument("--model", help="override the first provider's default model")
    parser.add_argument("--list-models", action="store_true", help="show available model IDs")
    parser.add_argument("--debug", action="store_true", help="print full JSON of each tool call and result")
    parser.add_argument("--no-mcp", action="store_true", help="don't start the MCP servers in kestrel.mcp.json")
    sub = parser.add_subparsers(dest="command", metavar="{traces,trace,stats,export}")
    p = sub.add_parser("traces", help="list recent traces")
    p.add_argument("-n", type=int, default=20, help="how many (default 20)")
    p = sub.add_parser("trace", help="show one trace as a tree")
    p.add_argument("id", help="trace id or its first few characters")
    sub.add_parser("stats", help="latency, tokens, cost, tools, error and fallback rates, ratings")
    p = sub.add_parser("web", help="open the web console (chat, approvals, traces, stats)")
    p.add_argument("--port", type=int, default=int(os.getenv("KESTREL_PORT", "8765")))
    p.add_argument("--build", action="store_true", help="build the frontend first (needs Node.js)")
    p = sub.add_parser("export", help="export traces as chat-format JSONL")
    p.add_argument("--rated", choices=["good", "bad", "any"], default="good")
    p.add_argument("--out", default="data/traces.jsonl")
    args = parser.parse_args()
    if os.getenv("KESTREL_MCP", "on").strip().lower() in ("off", "0", "false", "no"):
        args.no_mcp = True

    tracer = Tracer()
    if args.command == "web":
        run_web(args, tracer)
        return
    if args.command:
        run_report(args, tracer)
        return

    try:
        llm, skipped = build_llm(
            provider_chain(args.provider),
            args.model,
            on_retry=lambda p, reason, wait: dim(f"[retry] {p} {reason}; waiting {wait:.1f}s"),
            on_fallback=lambda msg: print(f"{YELLOW}[fallback] {msg}{RESET}"),
        )
        for warning in skipped:
            dim(f"[skip] {warning}")
        if args.list_models:
            print("\n".join(llm.list_models()))
            return
    except LLMError as e:
        sys.exit(f"Kestrel can't start: {e}")

    mcp = None if args.no_mcp else start_mcp()
    try:
        chat(llm, tracer, args)
    finally:
        if mcp:
            mcp.close()


def run_web(args: argparse.Namespace, tracer: Tracer) -> None:
    import uvicorn

    from kestrel.web import WebConfig, build_frontend, create_app

    if args.build:
        build_frontend()
    llm, problem = None, None
    try:
        llm, skipped = build_llm(
            provider_chain(args.provider),
            args.model,
            on_retry=lambda p, reason, wait: dim(f"[retry] {p} {reason}; waiting {wait:.1f}s"),
            on_fallback=lambda msg: print(f"{YELLOW}[fallback] {msg}{RESET}"),
        )
        for warning in skipped:
            dim(f"[skip] {warning}")
    except LLMError as e:
        problem = str(e)
        print(f"{YELLOW}Chat is disabled: {problem}. Traces and stats still work.{RESET}")
    mcp = None if args.no_mcp else start_mcp()

    def make_agent(on_event, approver):
        if llm is None:
            raise RuntimeError(problem)
        return Agent(
            llm,
            on_event=on_event,
            gate=ApprovalGate(approver),
            tracer=tracer,
            max_context_tokens=int(os.getenv("KESTREL_MAX_CONTEXT_TOKENS", MAX_CONTEXT_TOKENS)),
        )

    demo = llm is not None and any(l.provider.name == "demo" for l in llm.llms)
    info = {
        "demo": demo,
        "demo_notice": DEMO_NOTICE if demo else None,
        "demo_prompts": DEMO_PROMPTS if demo else [],
        "chat_available": llm is not None,
        "problem": problem,
        "models": [{"provider": l.provider.name, "model": l.model} for l in llm.llms] if llm else [],
        "tools": [{"name": t.name, "risk": t.risk, "server": t.server} for t in registry.tools.values()],
    }
    extra_hosts = tuple(h.strip() for h in os.getenv("KESTREL_ALLOWED_HOSTS", "").split(",") if h.strip())
    config = WebConfig(
        port=args.port, host=os.getenv("KESTREL_HOST", "127.0.0.1"), session_info=info, extra_hosts=extra_hosts
    )
    if token := os.getenv("KESTREL_TOKEN"):  # a fixed token, e.g. so a Docker container keeps the same link
        config.token = token
    app = create_app(make_agent, tracer, config)
    print(f"Kestrel console: {config.url()}", flush=True)
    if demo:
        print(f"{YELLOW}{DEMO_NOTICE}{RESET}", flush=True)
    dim("Only this machine can connect; the token in the link is your key. Ctrl+C to stop.")
    try:
        uvicorn.run(app, host=config.host, port=args.port, log_level="warning")
    finally:
        if mcp:
            mcp.close()


def start_mcp() -> MCPManager | None:
    """Connect to the servers in kestrel.mcp.json and add their tools. Never fatal."""
    try:
        config = load_config()
    except ValueError as e:
        print(f"{YELLOW}[mcp] {e}; continuing without MCP{RESET}")
        return None
    if not config or not config.servers:
        return None
    dim(f"[mcp] starting {', '.join(s.name for s in config.servers)}...")
    manager = MCPManager.from_config(
        config, on_problem=lambda server, msg: print(f"{YELLOW}[mcp] {server} {msg}{RESET}")
    )
    manager.start()
    added = manager.register_tools(registry, config.safe_tools)
    for name in sorted(config.safe_tools - {t.name for t in added}):
        print(f"{YELLOW}[mcp] safe_tools lists '{name}', but no connected server has that tool{RESET}")
    if added:
        dim("[mcp] " + ", ".join(f"{t.name} ({t.risk})" for t in added))
    return manager


def chat(llm, tracer: Tracer, args: argparse.Namespace) -> None:
    agent = Agent(
        llm,
        on_event=TerminalEvents(args.debug),
        max_context_tokens=int(os.getenv("KESTREL_MAX_CONTEXT_TOKENS", MAX_CONTEXT_TOKENS)),
        gate=ApprovalGate(TerminalApprover()),
        tracer=tracer,
    )
    chain = " -> ".join(f"{l.provider.name} / {l.model}" for l in llm.llms)
    print(f"Kestrel is listening ({chain}). Type 'exit' to quit; rate answers with /good or /bad [note].")
    if any(l.provider.name == "demo" for l in llm.llms):
        print(f"{YELLOW}{DEMO_NOTICE} Try: {DEMO_PROMPTS[3]!r}{RESET}")
    if not tracer.record_content:
        dim("[trace] KESTREL_TRACE_CONTENT=off: recording timings and token counts only")
    last_trace: str | None = None

    while True:
        try:
            user_text = input("\nyou > ").strip()
        except EOFError, KeyboardInterrupt:  # input ran out, Ctrl+Z, or Ctrl+C
            break
        if user_text.lower() in {"exit", "quit"}:
            break
        if not user_text:
            continue
        if user_text.startswith("/"):
            feedback = parse_feedback(user_text)
            if feedback is None:
                print("Commands: /good [note], /bad [note], exit")
            elif last_trace is None:
                print("Nothing to rate yet.")
            else:
                tracer.rate(last_trace, *feedback)
                dim(f"[trace {last_trace[:8]} rated {feedback[0]}]")
            continue

        checkpoint = len(agent.messages)
        try:
            result = agent.run(user_text)
        except KeyboardInterrupt:  # Ctrl+C mid-turn: drop the half-finished turn, keep chatting
            del agent.messages[checkpoint:]
            dim("\n[interrupted]")
            continue
        last_trace = result.trace_id
        details = [
            f"trace {(result.trace_id or '?')[:8]}",
            f"{result.steps} step(s)",
            f"{result.tokens:,} tokens",
            f"{(result.duration_ms or 0) / 1000:.1f}s",
        ]
        primary = llm.provider.name
        if result.stop_reason != "answered":
            details.insert(1, result.stop_reason)
        if result.providers not in ([], [primary]):
            details.append(f"answered by {', '.join(result.providers)}")
        dim(f"[{' | '.join(details)}]")
