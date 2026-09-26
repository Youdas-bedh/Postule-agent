import logging
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from database.repositories import list_profiles, save_profile
from services.profile_service import read_profile

logger = logging.getLogger(__name__)

def render_candidate(factory: sessionmaker[Session]) -> None:
    st.header("Profil candidat")
    st.write("Importe ton profil Markdown : expériences, compétences, formations, projets, langues, localisation, disponibilité et postes recherchés.")
    upload = st.file_uploader("Profil Markdown (UTF-8)", type=["md", "markdown"])
    if upload is not None:
        try:
            filename, content = read_profile(upload.name, upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.text_area("Aperçu du contenu", content, height=280, disabled=True)
            if st.button("Enregistrer le profil", type="primary"):
                try:
                    with factory.begin() as session:
                        profile, created = save_profile(session, filename, content)
                    st.session_state["profile_id"] = profile.id
                    st.session_state["profile_picker"] = profile.id
                    if created:
                        st.success("Profil enregistré.")
                        logger.info("[PROFILE] Profil importé")
                    else:
                        st.info("Ce profil est déjà enregistré.")
                except SQLAlchemyError as exc:
                    logger.error("[DATABASE] Échec de l'import (%s)", type(exc).__name__)
                    st.error("Impossible d'enregistrer le profil. Consulte les journaux du serveur.")
    try:
        with factory() as session:
            profiles = list_profiles(session)
    except SQLAlchemyError as exc:
        logger.error("[DATABASE] Lecture impossible (%s)", type(exc).__name__)
        st.error("Impossible de lire les profils. Vérifie l'accès à la base SQLite.")
        return
    if not profiles:
        st.info("Aucun profil enregistré pour le moment.")
        return
    by_id = {profile.id: profile for profile in profiles}
    if st.session_state.get("profile_picker") not in by_id:
        active_id = st.session_state.get("profile_id")
        st.session_state["profile_picker"] = active_id if active_id in by_id else profiles[0].id
    chosen_id = st.selectbox(
        "Profils enregistrés", list(by_id), key="profile_picker",
        format_func=lambda profile_id: (
            f"#{profile_id} — {by_id[profile_id].filename} — "
            f"{by_id[profile_id].created_at:%d/%m/%Y %H:%M} UTC"
        ),
    )
    chosen = by_id[chosen_id]
    st.session_state["profile_id"] = chosen.id
    st.caption("Profil actif pour cette session. Les imports précédents restent conservés.")
    st.text_area("Profil enregistré", chosen.content, height=300, disabled=True)
