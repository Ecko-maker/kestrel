"""Kestrel: a personal AI agent. Step 3: a robust agent loop with retries and fallback."""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from kestrel.agent import MAX_CONTEXT_TOKENS, Agent
from kestrel.llm import PROVIDERS, LLMError, build_llm

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


def main() -> None:
    load_dotenv()  # reads your API keys from the .env file

    parser = argparse.ArgumentParser(prog="kestrel")
    parser.add_argument("--provider", choices=list(PROVIDERS),
                        help="use only this provider (default: the KESTREL_PROVIDERS chain from .env)")
    parser.add_argument("--model", help="override the first provider's default model")
    parser.add_argument("--list-models", action="store_true", help="show available model IDs")
    parser.add_argument("--debug", action="store_true", help="print full JSON of each tool call and result")
    args = parser.parse_args()

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

    agent = Agent(
        llm,
        on_tool_step=make_printer(args.debug),
        max_context_tokens=int(os.getenv("KESTREL_MAX_CONTEXT_TOKENS", MAX_CONTEXT_TOKENS)),
    )
    chain = " -> ".join(f"{l.provider.name} / {l.model}" for l in llm.llms)
    print(f"Kestrel is listening ({chain}). Type 'exit' to quit.")

    while True:
        try:
            user_text = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):  # input ran out, Ctrl+Z, or Ctrl+C
            break
        if user_text.lower() in {"exit", "quit"}:
            break
        if not user_text:
            continue

        checkpoint = len(agent.messages)
        try:
            result = agent.run(user_text)
        except KeyboardInterrupt:  # Ctrl+C mid-turn: drop the half-finished turn, keep chatting
            del agent.messages[checkpoint:]
            dim("\n[interrupted]")
            continue
        print(f"\nkestrel > {result.text}")
        primary = llm.provider.name
        if result.stop_reason != "answered" or result.providers not in ([], [primary]):
            dim(f"[{result.stop_reason}; {result.steps} step(s); answered by {', '.join(result.providers) or 'none'}]")
