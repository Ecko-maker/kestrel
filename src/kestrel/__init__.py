"""Kestrel: a personal AI agent. Step 6: tools from any MCP server."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from kestrel.agent import MAX_CONTEXT_TOKENS, Agent
from kestrel.approval import ApprovalGate, TerminalApprover
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
    except (json.JSONDecodeError, AttributeError):
        shown = args_json
    return f"{name}({shown})"


def make_printer(debug: bool):
    def on_tool_step(name: str, args_json: str, result: str) -> None:
        one_line = " ".join(result.split())
        if len(one_line) > 100:
            one_line = one_line[:97] + "..."
        dim(f"[tool] {format_call(name, args_json)} -> {one_line}")
        if debug:
            dim(f"[debug] call: {json.dumps({'name': name, 'arguments': args_json})}")
            dim(f"[debug] result: {json.dumps(result, ensure_ascii=False)}")

    return on_tool_step


def provider_chain(cli_provider: str | None) -> list[str]:
    """--provider pins one provider; otherwise KESTREL_PROVIDERS (e.g. "gemini,groq,ollama")."""
    if cli_provider:
        return [cli_provider]
    names = [n.strip().lower() for n in os.getenv("KESTREL_PROVIDERS", "gemini").split(",") if n.strip()]
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
            print(f"Wrote {written} trace(s) to {out}" + (f" ({skipped} skipped: no stored text or errored)" if skipped else ""))


def main() -> None:
    load_dotenv()  # reads your API keys from the .env file
    sys.stdout.reconfigure(errors="replace")  # odd characters in files/web results can't crash printing

    parser = argparse.ArgumentParser(prog="kestrel", description="Chat with Kestrel, or inspect its traces.")
    parser.add_argument("--provider", choices=list(PROVIDERS),
                        help="use only this provider (default: the KESTREL_PROVIDERS chain from .env)")
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
    p = sub.add_parser("export", help="export traces as chat-format JSONL")
    p.add_argument("--rated", choices=["good", "bad", "any"], default="good")
    p.add_argument("--out", default="data/traces.jsonl")
    args = parser.parse_args()

    tracer = Tracer()
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
    manager = MCPManager.from_config(config, on_problem=lambda server, msg: print(f"{YELLOW}[mcp] {server} {msg}{RESET}"))
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
        on_tool_step=make_printer(args.debug),
        max_context_tokens=int(os.getenv("KESTREL_MAX_CONTEXT_TOKENS", MAX_CONTEXT_TOKENS)),
        gate=ApprovalGate(TerminalApprover()),
        tracer=tracer,
    )
    chain = " -> ".join(f"{l.provider.name} / {l.model}" for l in llm.llms)
    print(f"Kestrel is listening ({chain}). Type 'exit' to quit; rate answers with /good or /bad [note].")
    if not tracer.record_content:
        dim("[trace] KESTREL_TRACE_CONTENT=off: recording timings and token counts only")
    last_trace: str | None = None

    while True:
        try:
            user_text = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):  # input ran out, Ctrl+Z, or Ctrl+C
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
        print(f"\nkestrel > {result.text}")
        details = [f"trace {result.trace_id[:8]}", f"{result.steps} step(s)", f"{result.tokens:,} tokens",
                   f"{(result.duration_ms or 0) / 1000:.1f}s"]
        primary = llm.provider.name
        if result.stop_reason != "answered":
            details.insert(1, result.stop_reason)
        if result.providers not in ([], [primary]):
            details.append(f"answered by {', '.join(result.providers)}")
        dim(f"[{' | '.join(details)}]")
