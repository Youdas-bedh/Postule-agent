"""Shared bounded-retry HTTP behavior for local providers."""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from llm.base import LLMResponseError, LLMUnavailableError

logger = logging.getLogger(__name__)


def safe_base_url(value: str) -> str:
    """Avoid accidentally displaying credentials from a configured URL."""
    parts = urlsplit(value)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


class RetryingHTTPClient:
    def __init__(self, base_url: str, timeout: float, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "postule-agent/phase2"})

    def request_json(self, method: str, url: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.request(method, url, json=json, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        time.sleep(0.25 * (2**attempt))
                        continue
                response.raise_for_status()
                try:
                    body = response.json()
                except (requests.exceptions.JSONDecodeError, ValueError) as exc:
                    raise LLMResponseError("Le serveur LLM a renvoyé un JSON invalide.") from exc
                if not isinstance(body, dict):
                    raise LLMResponseError("Le serveur LLM a renvoyé une réponse JSON inattendue.")
                return body
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt < 2:
                    logger.warning("[LLM] Requête réseau échouée, nouvel essai %d/2 (%s)", attempt + 1, type(exc).__name__)
                    time.sleep(0.25 * (2**attempt))
                    continue
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "inconnu"
                raise LLMUnavailableError(f"Le serveur LLM a répondu HTTP {status}.") from exc
        raise LLMUnavailableError(
            f"Impossible de joindre le serveur LLM à {safe_base_url(self.base_url)} "
            f"après 3 essais ({type(last_error).__name__ if last_error else 'erreur réseau'})."
        ) from last_error

    def close(self) -> None:
        self.session.close()
