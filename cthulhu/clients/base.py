"""Client interface shared by every backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypedDict


class Message(TypedDict):
    role: str
    content: str


class LLMError(RuntimeError):
    """Raised when a backend call fails."""


class LLMClient(ABC):
    """Minimal chat interface: a message list in, the reply text out."""

    @abstractmethod
    def chat(self, messages: list[Message]) -> str:
        ...
