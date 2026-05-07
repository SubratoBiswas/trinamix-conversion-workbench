"""Pluggable AI mapping providers."""
from app.ai.base import MappingSuggestion, MappingProvider  # noqa: F401
from app.ai.rule_based import RuleBasedMapper  # noqa: F401
from app.config import settings


def get_mapping_provider() -> "MappingProvider":
    """Return the configured AI mapping provider.

    Falls back to RuleBasedMapper when AI_PROVIDER is "none" or no API key is set.
    Anthropic / OpenAI providers wrap the LLM call but reuse rule-based features
    (column similarity, sample patterns) as the prompt baseline.
    """
    provider = (settings.AI_PROVIDER or "none").lower()
    if provider == "anthropic" and settings.ANTHROPIC_API_KEY:
        from app.ai.llm_provider import AnthropicMapper
        return AnthropicMapper(api_key=settings.ANTHROPIC_API_KEY, model=settings.ANTHROPIC_MODEL)
    if provider == "openai" and settings.OPENAI_API_KEY:
        from app.ai.llm_provider import OpenAIMapper
        return OpenAIMapper(api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL)
    return RuleBasedMapper()
