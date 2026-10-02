"""One interface for every model provider.

Gemini, Groq, and Ollama all speak the OpenAI-compatible API, so a single
client works for all three; only the address, key, and model name change.
Switching providers is a config change, never a code change.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # pull API keys and model overrides from .env into the environment


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None  # name of the environment variable holding the API key
    default_model: str


PROVIDERS = {
    "gemini": Provider(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        key_env="GEMINI_API_KEY",
        default_model="gemini-3-flash",
    ),
    "groq": Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        default_model="openai/gpt-oss-120b",
    ),
    "ollama": Provider(
        name="ollama",
        base_url="http://localhost:11434/v1",
        key_env=None,  # runs on your laptop, no key needed
        default_model="qwen3:4b",
    ),
}


class LLM:
    def __init__(self, provider: str, model: str | None = None):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDERS)}")
        self.provider = PROVIDERS[provider]

        api_key = "ollama"  # Ollama ignores the key, but the client requires one
        if self.provider.key_env:
            api_key = os.getenv(self.provider.key_env, "")
            if not api_key:
                raise RuntimeError(
                    f"Missing {self.provider.key_env}. Add it to your .env file."
                )

        # A model set in .env (e.g. GEMINI_MODEL) overrides the default above.
        env_model = os.getenv(f"{provider.upper()}_MODEL")
        self.model = model or env_model or self.provider.default_model
        self.client = OpenAI(base_url=self.provider.base_url, api_key=api_key)

    def complete(self, messages: list[dict]) -> str:
        """Send the conversation, return the model's reply text."""
        response = self.client.chat.completions.create(model=self.model, messages=messages)
        return response.choices[0].message.content or ""

    def list_models(self) -> list[str]:
        """The exact model IDs this provider offers your key right now."""
        return sorted(m.id for m in self.client.models.list())
