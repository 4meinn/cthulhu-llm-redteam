from __future__ import annotations

from ..config import EndpointConfig
from .base import LLMClient, LLMError, Message
from .mock import MockAttackerModel, MockClient, MockJudge
from .ollama import OllamaClient, OpenAICompatClient

__all__ = [
    "LLMClient",
    "LLMError",
    "Message",
    "MockAttackerModel",
    "MockClient",
    "MockJudge",
    "OllamaClient",
    "OpenAICompatClient",
    "build_client",
]


def build_client(
    cfg: EndpointConfig, canary: str = "CANARY", role: str = "target"
) -> LLMClient:
    """Create the client a config section asks for. role picks the mock stand-in."""
    if cfg.provider == "mock":
        if role == "judge":
            return MockJudge(canary=canary)
        if role == "attacker":
            return MockAttackerModel(canary=canary)
        return MockClient(canary=canary)
    if cfg.provider == "ollama":
        return OllamaClient(cfg.base_url, cfg.model, cfg.timeout)
    return OpenAICompatClient(cfg.base_url, cfg.model, cfg.api_key, cfg.timeout)
