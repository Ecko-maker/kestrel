"""One interface for every model provider (Gemini, Groq, Ollama via the OpenAI-compatible API)."""

import os
from dataclasses import dataclass

from openai import OpenAI


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None
    default_model: str


PROVIDERS = {
    "gemini": Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3-flash"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "ollama": Provider("ollama", "http://localhost:11434/v1", None, "qwen3:4b"),
}


class LLM:
    def __init__(self, provider: str, model: str | None = None):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDERS)}")
        self.provider = PROVIDERS[provider]
        api_key = "ollama"
        if self.provider.key_env:
            api_key = os.getenv(self.provider.key_env, "")
            if not api_key:
                raise RuntimeError(f"Missing {self.provider.key_env}. Add it to your .env file.")
        env_model = os.getenv(f"{provider.upper()}_MODEL")
        self.model = model or env_model or self.provider.default_model
        self.client = OpenAI(base_url=self.provider.base_url, api_key=api_key)

    def complete(self, messages: list[dict]) -> str:
        response = self.client.chat.completions.create(model=self.model, messages=messages)
        return response.choices[0].message.content or ""

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list())
