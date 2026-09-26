#!/usr/bin/env python3
"""Smoke-test the configured provider; exits nonzero when it is unavailable."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from llm.base import LLMError  # noqa: E402
from llm.factory import create_provider  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    provider = create_provider(settings)
    print(f"Provider : {provider.name}")
    print(f"Modèle   : {provider.model_name or '(non configuré)'}")
    print(f"URL      : {provider.base_url}")
    try:
        status = provider.check_connection()
        if not status.available:
            print(f"Échec de connexion : {status.message}", file=sys.stderr)
            return 1
        print(f"Disponibilité : oui ({status.latency_seconds:.2f} s)")
        start = time.perf_counter()
        answer = provider.generate("Réponds en un seul mot : bonjour.", system_prompt="Tu es un assistant concis.")
        elapsed = time.perf_counter() - start
        print(f"Réponse : {answer}")
        print(f"Temps de génération : {elapsed:.2f} s")
        return 0
    except LLMError as exc:
        print(f"Échec du test LLM : {exc}", file=sys.stderr)
        return 1
    finally:
        provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
