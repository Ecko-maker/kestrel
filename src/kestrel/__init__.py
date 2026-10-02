"""Kestrel: a personal AI agent. Step 1: talk to any model from the terminal."""

import argparse

from dotenv import load_dotenv

from kestrel.llm import LLM, PROVIDERS

SYSTEM_PROMPT = "You are Kestrel, a concise and helpful personal AI assistant."


def main() -> None:
    load_dotenv()  # reads your API keys from the .env file

    parser = argparse.ArgumentParser(prog="kestrel")
    parser.add_argument("--provider", default="gemini", choices=list(PROVIDERS))
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--list-models", action="store_true", help="show available model IDs")
    args = parser.parse_args()

    llm = LLM(args.provider, args.model)

    if args.list_models:
        print("\n".join(llm.list_models()))
        return

    print(f"Kestrel is listening ({llm.provider.name} / {llm.model}). Type 'exit' to quit.")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        user_text = input("\nyou > ").strip()
        if user_text.lower() in {"exit", "quit"}:
            break
        if not user_text:
            continue

        messages.append({"role": "user", "content": user_text})
        reply = llm.complete(messages)
        messages.append({"role": "assistant", "content": reply})  # so it remembers the chat
        print(f"\nkestrel > {reply}")
