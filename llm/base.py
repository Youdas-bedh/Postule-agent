"""Stable provider interface and typed results for text generation."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Base class for configuration, availability, network and response errors."""


class LLMConfigurationError(LLMError):
    """Provider settings cannot be used."""


class LLMUnavailableError(LLMError):
    """Provider server is not available or does not serve the configured model."""


class LLMResponseError(LLMError):
    """Provider returned a malformed or empty generation."""


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    model: str
    base_url: str
    available: bool
    message: str
    latency_seconds: float | None = None


class LLMProvider(ABC):
    """Provider-neutral interface; callers need not know the backend."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def base_url(self) -> str: ...

    @abstractmethod
    def check_connection(self) -> ProviderStatus: ...

    @abstractmethod
    def generate(self, prompt: str, *, system_prompt: str | None = None, max_tokens: int | None = None, json_mode: bool = False) -> str: ...

    def close(self) -> None:
        """Release provider resources, if any."""
