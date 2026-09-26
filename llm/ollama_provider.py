"""Ollama provider using its native local chat API."""
from __future__ import annotations

import logging
import time

from config import Settings, get_settings
from llm.base import LLMConfigurationError, LLMProvider, LLMResponseError, LLMUnavailableError, ProviderStatus
from llm.http_provider import RetryingHTTPClient, safe_base_url

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    def __init__(self, settings: Settings | None = None, *, client: RetryingHTTPClient | None = None):
        self.settings = settings or get_settings()
        self.last_usage: dict[str, int] = {}
        if not self.settings.model_name:
            raise LLMConfigurationError("MODEL_NAME doit être défini dans .env.")
        self._client = client or RetryingHTTPClient(self.settings.ollama_base_url, self.settings.llm_timeout)

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self.settings.model_name

    @property
    def base_url(self) -> str:
        return safe_base_url(self.settings.ollama_base_url)

    def check_connection(self) -> ProviderStatus:
        started = time.perf_counter()
        try:
            response = self._client.request_json("GET", f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
            models = response.get("models")
            if not isinstance(models, list):
                raise LLMResponseError("Réponse Ollama invalide : liste 'models' absente.")
            names = [entry.get("name") for entry in models if isinstance(entry, dict)]
            available = self.model_name in names
            elapsed = time.perf_counter() - started
            message = "Serveur prêt et modèle chargé." if available else "Ollama actif, modèle configuré absent de /api/tags."
            logger.info("[LLM] provider=ollama model=%s available=%s", self.model_name, available)
            return ProviderStatus(self.name, self.model_name, self.base_url, available, message, elapsed)
        except LLMUnavailableError as exc:
            logger.warning("[LLM] Ollama indisponible (%s)", type(exc).__name__)
            return ProviderStatus(self.name, self.model_name, self.base_url, False, str(exc), time.perf_counter() - started)

    def generate(self, prompt: str, *, system_prompt: str | None = None, max_tokens: int | None = None, json_mode: bool = False) -> str:
        if not prompt.strip():
            raise LLMConfigurationError("Le prompt ne peut pas être vide.")
        messages = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model_name, "messages": messages, "stream": False,
                   "options": {"temperature": self.settings.llm_temperature, "num_predict": max_tokens or self.settings.llm_max_tokens}}
        if json_mode:
            payload["format"] = "json"
        response = self._client.request_json("POST", f"{self.settings.ollama_base_url.rstrip('/')}/api/chat", json=payload)
        self.last_usage = {
            "prompt_tokens": int(response["prompt_eval_count"]) if isinstance(response.get("prompt_eval_count"), int) else 0,
            "completion_tokens": int(response["eval_count"]) if isinstance(response.get("eval_count"), int) else 0,
        }
        try:
            content = response["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMResponseError("Réponse de génération Ollama invalide : contenu absent.") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("Ollama a renvoyé une génération vide.")
        logger.info("[LLM] Génération réussie provider=ollama model=%s", self.model_name)
        return content.strip()

    def close(self) -> None:
        self._client.close()
