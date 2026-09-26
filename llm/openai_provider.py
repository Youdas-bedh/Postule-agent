"""Reserved provider for a future, explicitly configured paid API backend."""
from config import Settings, get_settings
from llm.base import LLMConfigurationError, LLMProvider, ProviderStatus


class OpenAIProvider(LLMProvider):
    """Deliberately disabled in phase 2 to prevent accidental paid API calls."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self.settings.model_name

    @property
    def base_url(self) -> str:
        return "https://api.openai.com/v1"

    def _not_implemented(self):
        raise LLMConfigurationError("OpenAIProvider est réservé à une phase future ; aucun appel API payant n'est activé.")

    def check_connection(self) -> ProviderStatus:
        return ProviderStatus(self.name, self.model_name, self.base_url, False, "Fournisseur réservé à une phase future.")

    def generate(self, prompt: str, *, system_prompt: str | None = None, max_tokens: int | None = None, json_mode: bool = False) -> str:
        self._not_implemented()
