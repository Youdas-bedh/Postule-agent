"""Client for vLLM's OpenAI-compatible chat completions API."""
from __future__ import annotations

import logging
import time

from config import Settings, get_settings
from llm.base import LLMConfigurationError, LLMProvider, LLMResponseError, LLMUnavailableError, ProviderStatus
from llm.http_provider import RetryingHTTPClient, safe_base_url

logger = logging.getLogger(__name__)


class LocalVLLMProvider(LLMProvider):
    def __init__(self, settings: Settings | None = None, *, client: RetryingHTTPClient | None = None):
        self.settings = settings or get_settings()
        self.last_usage: dict[str, int] = {}
        if not self.settings.model_name:
            raise LLMConfigurationError("MODEL_NAME doit être défini dans .env.")
        self._client = client or RetryingHTTPClient(self.settings.vllm_base_url, self.settings.llm_timeout)

    @property
    def name(self) -> str:
        return "vllm"

    @property
    def model_name(self) -> str:
        return self.settings.model_name

    @property
    def base_url(self) -> str:
        return safe_base_url(self.settings.vllm_base_url)

    def check_connection(self) -> ProviderStatus:
        started = time.perf_counter()
        try:
            response = self._client.request_json("GET", f"{self.settings.vllm_base_url.rstrip('/')}/models")
            models = response.get("data")
            if not isinstance(models, list):
                raise LLMResponseError("Réponse /models invalide : liste 'data' absente.")
            names = [entry.get("id") for entry in models if isinstance(entry, dict)]
            available = self.model_name in names
            elapsed = time.perf_counter() - started
            message = "Serveur prêt et modèle chargé." if available else "Serveur actif, modèle configuré absent de /v1/models."
            logger.info("[LLM] provider=vllm model=%s available=%s", self.model_name, available)
            return ProviderStatus(self.name, self.model_name, self.base_url, available, message, elapsed)
        except LLMUnavailableError as exc:
            logger.warning("[LLM] vLLM indisponible (%s)", type(exc).__name__)
            return ProviderStatus(self.name, self.model_name, self.base_url, False, str(exc), time.perf_counter() - started)

    def generate(self, prompt: str, *, system_prompt: str | None = None, max_tokens: int | None = None, json_mode: bool = False) -> str:
        if not prompt.strip():
            raise LLMConfigurationError("Le prompt ne peut pas être vide.")
        messages = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            # Qwen's default thinking can consume the entire token budget and
            # leave `content` empty. Keep this provider's user-facing result
            # in the OpenAI-compatible `content` field.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self._client.request_json("POST", f"{self.settings.vllm_base_url.rstrip('/')}/chat/completions", json=payload)
        usage = response.get("usage")
        self.last_usage = {
            key: int(usage[key]) for key in ("prompt_tokens", "completion_tokens")
            if isinstance(usage, dict) and isinstance(usage.get(key), (int, float))
        }
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("Réponse de génération vLLM invalide : contenu absent.") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("vLLM a renvoyé une génération vide.")
        logger.info("[LLM] Génération réussie provider=vllm model=%s", self.model_name)
        return content.strip()

    def close(self) -> None:
        self._client.close()
