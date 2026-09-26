"""Small manual LLM health and generation page."""
import logging
import time

import streamlit as st
from pydantic import ValidationError

from config import get_settings
from llm.base import LLMError
from llm.factory import create_provider

logger = logging.getLogger(__name__)


def render_llm_status() -> None:
    st.header("LLM Status")
    try:
        settings = get_settings()
        provider = create_provider(settings)
    except (LLMError, ValidationError, ValueError) as exc:
        logger.warning("[LLM] Configuration invalide (%s)", type(exc).__name__)
        st.error(f"Configuration LLM invalide : {exc}")
        return

    left, right = st.columns(2)
    left.metric("Provider", provider.name)
    right.metric("Modèle", provider.model_name or "Non configuré")
    st.caption(f"URL : {provider.base_url}")

    if provider.name == "openai":
        st.info("Le fournisseur OpenAI est réservé à une phase future. Aucun appel payant ne sera effectué.")
        return

    if st.button("Vérifier la disponibilité", key="llm_check"):
        try:
            status = provider.check_connection()
            st.session_state["llm_last_status"] = status
        except LLMError as exc:
            st.error(f"Vérification échouée : {exc}")

    status = st.session_state.get("llm_last_status")
    if status is not None:
        if status.available:
            st.success(status.message)
        else:
            st.error(status.message)
        if status.latency_seconds is not None:
            st.caption(f"Temps de vérification : {status.latency_seconds:.2f} s")

    if st.button("Tester une génération", type="primary", key="llm_generate"):
        started = time.perf_counter()
        try:
            answer = provider.generate(
                "Réponds en une phrase courte : à quoi sert un modèle de langage local ?",
                system_prompt="Tu réponds clairement en français.",
            )
        except LLMError as exc:
            st.error(f"Génération impossible : {exc}")
        else:
            st.success(f"Génération réussie en {time.perf_counter() - started:.2f} s")
            st.write(answer)
        finally:
            provider.close()
    else:
        provider.close()
