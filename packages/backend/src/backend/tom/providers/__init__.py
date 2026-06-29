"""TOM LLM provider abstraction.

Section 5 — Ollama, OpenAI-compat, Anthropic, Google, and custom
endpoints behind a single :class:`Provider` Protocol. Runtime
selection via :class:`ProviderRegistry`.
"""

from __future__ import annotations

from backend.tom.providers.anthropic import AnthropicProvider
from backend.tom.providers.base import (
    HealthReport,
    Message,
    Provider,
    TokenChunk,
    ToolCall,
    ToolDef,
)
from backend.tom.providers.google import GoogleProvider
from backend.tom.providers.ollama import OllamaProvider
from backend.tom.providers.openai_compat import OpenAICompatProvider
from backend.tom.providers.registry import (
    FallbackChain,
    ProviderRegistry,
    keyring_slot,
)

__all__: list[str] = [
    "AnthropicProvider",
    "FallbackChain",
    "GoogleProvider",
    "HealthReport",
    "Message",
    "OllamaProvider",
    "OpenAICompatProvider",
    "Provider",
    "ProviderRegistry",
    "TokenChunk",
    "ToolCall",
    "ToolDef",
    "keyring_slot",
]
