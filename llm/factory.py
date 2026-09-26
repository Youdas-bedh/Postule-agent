"""Construct configured LLM providers without coupling the application to a backend."""
from config import Settings, get_settings
from llm.base import LLMConfigurationError, LLMProvider
from llm.ollama_provider import OllamaProvider
from llm.openai_provider import OpenAIProvider
from llm.vllm_provider import LocalVLLMProvider


def create_provider(settings: Settings | None = None) -> LLMProvider:
    cfg = settings or get_settings()
    providers: dict[str, type[LLMProvider]] = {
        "vllm": LocalVLLMProvider,
        "ollama": OllamaProvider,
        "openai": OpenAIProvider,
    }
    try:
        provider_type = providers[cfg.llm_provider]
    except KeyError as exc:
        raise LLMConfigurationError(f"LLM_PROVIDER non pris en charge : {cfg.llm_provider}") from exc
    return provider_type(cfg)
