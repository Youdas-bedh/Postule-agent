"""One-button, France-only campaign interface with a visible safe preview."""
from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings
from database.models import CandidateProfile
from database.repositories import list_profiles
from llm.factory import create_provider
from services.campaign_service import run_campaign


def render_campaign(factory: sessionmaker[Session]) -> None:
    st.header("Candidatures en France")
    st.caption("Recherche d'entreprises réelles, offres françaises, contacts publics vérifiés, matching, puis message personnalisé avec le CV adapté.")
    cfg = get_settings()
    if cfg.email_dry_run or not cfg.auto_send_enabled or not cfg.gmail_enabled:
        st.info("Mode aperçu actif : le bouton prépare les messages et contrôle les pièces jointes. Aucun email ne sera envoyé avec la configuration actuelle.")
    else:
        st.warning("Envoi automatique activé : les messages éligibles seront envoyés par Gmail pendant cette campagne.")

    with factory() as session:
        profiles = list_profiles(session)
        profiles = [(p.id, p.filename) for p in profiles]
        cvs = {pid: session.query(CandidateProfile).filter_by(id=pid).first() for pid, _ in profiles}
    if not profiles:
        st.warning("Importe d'abord ton profil candidat dans « Profil candidat ».")
        return
    profile_ids = [p[0] for p in profiles]
    current = st.session_state.get("profile_id")
    selected_index = profile_ids.index(current) if current in profile_ids else 0
    profile_id = st.selectbox("Profil actif", profile_ids, index=selected_index,
                              format_func=lambda pid: f"{dict(profiles)[pid]} (#{pid})")
    st.session_state["profile_id"] = profile_id
    with factory() as session:
        has_cv = session.query(CandidateProfile).filter_by(id=profile_id).first() is not None
        from database.models import CVFile
        has_cv = has_cv and session.query(CVFile).filter_by(profile_id=profile_id).first() is not None

    query = st.text_input("Domaine / métiers recherchés", value="Ingénieur IA, Machine Learning Engineer, Data Scientist, NLP, LLM et Data Engineering — CDI en France")
    maximum = st.slider("Entreprises maximum pour cette campagne", min_value=1, max_value=min(10, cfg.daily_email_limit), value=min(5, cfg.daily_email_limit))
    left, center, right = st.columns([1, 2, 1])
    with center:
        launch = st.button("🚀 Lancer les candidatures", type="primary", use_container_width=True,
                           disabled=not (has_cv and query.strip()))
    if not has_cv:
        st.warning("Importe un CV PDF dans Applications avant de lancer la campagne.")
    if launch:
        provider = None
        try:
            provider = create_provider(cfg)
            with st.status("Campagne en cours : recherche des entreprises, vérification des sites, scan des offres et contacts, matching et rédaction…", expanded=True) as status:
                health = provider.check_connection()
                if not health.available:
                    st.error(f"LLM local indisponible : {health.message}")
                    st.stop()
                result = run_campaign(factory, profile_id=profile_id, query=query, max_companies=maximum,
                                      provider=provider, settings=cfg)
                status.update(label="Campagne terminée", state="complete")
            st.session_state["campaign_last_result"] = result
        except Exception as exc:
            st.error(f"Campagne interrompue : {type(exc).__name__}. Vérifie le LLM local et l'accès aux sites publics.")
        finally:
            if provider:
                provider.close()

    result = st.session_state.get("campaign_last_result")
    if result:
        st.subheader("Résultats et aperçu des messages")
        st.caption(f"{result.companies_found} entreprises vérifiées · {result.jobs_scanned} offres examinées · {result.contacts_scanned} contacts recensés")
        for item in result.items:
            with st.container(border=True):
                st.markdown(f"### {item.company}")
                if item.website:
                    st.markdown(f"Site officiel : [{item.website}]({item.website})")
                if item.job:
                    st.markdown(f"**Offre :** [{item.job}]({item.job_url}) · **Matching :** {item.score}/100")
                if item.contact:
                    st.write(f"**Contact vérifié :** {item.contact} · {item.email or 'email absent'}")
                if item.cv:
                    st.write(f"**CV joint prévu :** {item.cv}")
                if item.subject:
                    st.write("**Objet :**", item.subject)
                if item.body:
                    st.markdown("**Message :**")
                    st.text(item.body)
                st.write(f"**Statut :** {item.status}")
                if item.reason:
                    st.caption(item.reason)
