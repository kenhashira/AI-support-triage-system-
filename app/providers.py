"""Choose which AI provider backs the app (AI_PROVIDER)."""

from typing import Mapping

from .ai_client import AnthropicTriageClient, TriageClient
from .config import ConfigError
from .gemini_client import GeminiTriageClient

PROVIDERS = ("anthropic", "gemini")
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "gemini": "gemini-3.5-flash-lite"}


def build_triage_client(config: Mapping) -> TriageClient:
    """Build the client for config['AI_PROVIDER'] (default: anthropic)."""
    provider = config.get("AI_PROVIDER", "anthropic")
    timeout = config.get("ANTHROPIC_TIMEOUT_SECONDS", 30.0)  # applies to either provider
    if provider == "anthropic":
        return AnthropicTriageClient(
            api_key=config.get("ANTHROPIC_API_KEY", ""),
            model=config.get("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"]),
            timeout=timeout,
        )
    if provider == "gemini":
        return GeminiTriageClient(
            api_key=config.get("GEMINI_API_KEY", ""),
            model=config.get("GEMINI_MODEL", DEFAULT_MODELS["gemini"]),
            timeout=timeout,
        )
    raise ConfigError(f"AI_PROVIDER must be one of {', '.join(PROVIDERS)} (got {provider!r}).")
