import logging
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from database.database import build_engine, init_db, session_factory
from ui.candidate import render_candidate
from ui.llm_status import render_llm_status
from ui.company_discovery import render_company_discovery
from ui.career_jobs import render_career_jobs
from ui.job_matching import render_job_matching
from ui.contacts import render_contacts
from ui.applications import render_applications
from ui.dashboard import render_dashboard
from ui.auto_send import render_auto_send, render_gmail
from ui.campaign import render_campaign

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
st.set_page_config(page_title="Postule Agent", page_icon="📄", layout="wide")

@st.cache_resource
def resources() -> sessionmaker[Session]:
    engine = build_engine()
    init_db(engine)
    return session_factory(engine)

st.title("Postule Agent")
st.caption("Pipeline de candidature · données locales des phases 1–9")
page = st.sidebar.radio("Navigation", ["Profil candidat", "Dashboard", "Campagne", "État du projet", "LLM Status", "Company Discovery", "Career & Jobs", "Job Matching", "Contacts", "Applications", "Auto Send", "Gmail"])
try:
    factory = resources()
except (SQLAlchemyError, ValueError, OSError) as exc:
    logging.getLogger(__name__).error("[DATABASE] Initialisation impossible (%s)", type(exc).__name__)
    st.error("Impossible d'initialiser la base. Vérifie DATABASE_URL et les droits du dossier de stockage.")
    st.stop()
if page == "Dashboard":
    render_dashboard(factory)
elif page == "Campagne":
    render_campaign(factory)
elif page == "Profil candidat":
    render_candidate(factory)
elif page == "État du projet":
    st.header("État du projet")
    st.success("Architecture, base SQLite et import du profil disponibles.")
    st.write("Les étapes suivantes ajouteront le LLM local, la recherche d'entreprises, l'analyse, les candidatures et leur validation humaine.")
    st.info("Aucune fonction d'envoi n'est implémentée dans cette version.")
elif page == "LLM Status":
    render_llm_status()
elif page == "Company Discovery":
    render_company_discovery(factory)
elif page == "Career & Jobs":
    render_career_jobs(factory)
elif page == "Job Matching":
    render_job_matching(factory)
elif page == "Auto Send":
    render_auto_send(factory)
elif page == "Gmail":
    render_gmail(factory)
elif page == "Contacts":
    render_contacts(factory)
else:
    render_applications(factory)
