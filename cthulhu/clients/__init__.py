from __future__ import annotations

from ..config import EndpointConfig
from .base import LLMClient, LLMError, Message
from .mock import MockClient, MockJudge
from .ollama import OllamaClient, OpenAICompatClient

__all__ = [
    "LLMClient",
    "LLMError",
    "Message",
    "MockClient",
    "MockJudge",
    "OllamaClient",
    "OpenAICompatClient",
    "build_client",
]


def build_client(
    cfg: EndpointConfig, canary: str = "CANARY", role: str = "target"
) -> LLMClient:
    """Create the client a config section asks for."""
    if cfg.provider == "mock":
        return MockJudge(canary=canary) if role == "judge" else MockClient(canary=canary)
    if cfg.provider == "ollama":
        return OllamaClient(cfg.base_url, cfg.model, cfg.timeout)
    return OpenAICompatClient(cfg.base_url, cfg.model, cfg.api_key, cfg.timeout)
