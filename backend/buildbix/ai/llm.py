"""Provider-agnostic LLM interface. The AI key lives in config/secrets, never in code."""
from abc import ABC, abstractmethod

import httpx

from ..core.config import settings


class AINotConfigured(RuntimeError):
    pass


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, messages: list[dict], max_tokens: int = 1000) -> str: ...


class NullClient(LLMClient):
    def complete(self, system, messages, max_tokens=1000):
        raise AINotConfigured("AI is not configured. Set BUILDBIX_AI_API_KEY on the server.")


class AnthropicClient(LLMClient):
    def complete(self, system, messages, max_tokens=1000):
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                       headers={"x-api-key": settings.ai_api_key, "anthropic-version": "2023-06-01"},
                       json={"model": settings.ai_model, "max_tokens": max_tokens, "system": system, "messages": messages})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def get_llm() -> LLMClient:
    return AnthropicClient() if settings.ai_api_key else NullClient()
