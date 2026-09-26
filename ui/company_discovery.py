"""Streamlit workflow for company discovery and evidence-backed analysis."""
from __future__ import annotations
import json
import logging
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from agents.company_analysis import CompanyAnalysisAgent
from agents.company_discovery import CompanyDiscoveryAgent
from agents.career_agent import CareerAgent
from agents.contact_discovery import ContactDiscoveryAgent
from config import get_settings
from database.models import Company
from discovery.base import CompanyCandidate
from discovery.llm_company_source import LLMCompanySource
from llm.base import LLMError
from llm.factory import create_provider
from utils.geography import is_france_job

logger = logging.getLogger(__name__)


def _items(value: str) -> list[str]:
    return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


def _analyze(factory: sessionmaker[Session], company_id: int, *, force: bool = False, criteria: dict | None = None) -> str | None:
    with factory() as session:
        company = session.get(Company, company_id)
        if not company:
            return "Entreprise introuvable en base."
        provider = create_provider(get_settings())
        try:
            result = CompanyAnalysisAgent(provider).analyze(session, company, force=force, criteria=criteria)
            st.session_state.setdefault("company_discovery_results", {})[company_id] = result
            return None
        except (LLMError, ValueError, RuntimeError) as exc:
            logger.warning("[ANALYSIS] Company %s failed (%s)", company_id, type(exc).__name__)
            return str(exc)
        finally:
            provider.close()


def _render_company(company: Company, factory: sessionmaker[Session], criteria: dict | None = None) -> None:
    fresh = st.session_state.get("company_discovery_results", {}).get(company.id)
    if fresh is None and company.analysis_json:
        try:
            fresh = json.loads(company.analysis_json)
        except ValueError:
            fresh = None
    score = (fresh or {}).get("company_relevance_score", company.company_relevance_score)
    relevance = (fresh or {}).get("ai_data_relevance") or "Non analysée"
    st.write(f"**{company.name}** · {company.website_url or company.website}")
    status = "À analyser" if not fresh else ("Analysée · preuves limitées" if not (fresh or {}).get("evidence") else "Analysée")
    st.caption(f"{company.location or 'Localisation inconnue'} · {company.sector or 'Secteur inconnu'} · {company.company_type or 'Type inconnu'} · Pertinence IA/Data : {relevance} · Score : {score if score is not None else '—'}/100 · {status}")
    left, right = st.columns([1, 3])
    with left:
        if st.button("View analysis", key=f"view_company_{company.id}"):
            st.session_state[f"show_company_{company.id}"] = not st.session_state.get(f"show_company_{company.id}", False)
        if st.button("Force re-analysis", key=f"reanalyze_company_{company.id}"):
            error = _analyze(factory, company.id, force=True, criteria=criteria)
            if error: st.error(f"Analyse impossible : {error}")
            else: st.success("Analyse actualisée.")
    if st.session_state.get(f"show_company_{company.id}") and fresh:
        with st.expander(f"Analyse — {company.name}", expanded=True):
            st.write(fresh.get("summary") or "Résumé non disponible.")
            st.write("**Activités :**", ", ".join(fresh.get("main_activities", [])) or "—")
            st.write("**Technologies :**", ", ".join(fresh.get("technologies", [])) or "—")
            st.write("**Pertinence IA/Data :**", fresh.get("ai_data_relevance") or "—")
            st.write("**Score et raisons :**", score, "; ".join(fresh.get("relevance_reasons", [])))
            st.write("**Sources et éléments probants :**")
            for evidence in fresh.get("evidence", []):
                st.markdown(f"- [{evidence.get('claim','Source')}]({evidence.get('url','')}) — “{evidence.get('excerpt','')}”")


def render_company_discovery(factory: sessionmaker[Session]) -> None:
    st.header("Company Discovery")
    st.caption("Le LLM propose des entreprises ; leurs sites sont vérifiés avant de scanner les offres en France et les contacts publics. Aucun email n'est envoyé depuis cette page.")
    settings = get_settings()
    has_discovery_source = bool(settings.company_search_api_url or settings.company_public_lists_path)
    if not has_discovery_source:
        st.warning(
            "Aucune source de recherche classique n'est configurée. Utilise la recherche guidée par le LLM local ci-dessous ; chaque nom et site officiel est vérifié avant d'être enregistré."
        )
    with st.form("llm_company_discovery_form"):
        st.subheader("Recherche guidée par le LLM local")
        query = st.text_area(
            "Domaine et métiers recherchés",
            "Intelligence artificielle, Machine Learning, Data Science, NLP, LLM et Data Engineering ; postes d'ingénieur IA, Machine Learning Engineer ou Data Scientist en France",
        )
        llm_maximum = st.number_input("Nombre maximum d'entreprises", min_value=1, max_value=20, value=8)
        find_with_llm = st.form_submit_button("Trouver des entreprises et chercher leurs contacts", type="primary")
    if find_with_llm:
        provider = None
        try:
            with st.status("Le LLM local propose des entreprises nommées…", expanded=False) as status:
                provider = create_provider(settings)
                provider_status = provider.check_connection()
                if not provider_status.available:
                    st.error(f"LLM local indisponible : {provider_status.message}")
                    st.stop()
                candidates = LLMCompanySource(provider).search(query, int(llm_maximum))
                status.update(label=f"{len(candidates)} entreprises candidates avec site trouvé · vérification des domaines…", state="complete")
                with factory() as session:
                    companies = CompanyDiscoveryAgent(sources=[]).discover(
                        session,
                        job_titles=_items(query),
                        locations=["France"],
                        company_types=[],
                        sectors=[query[:255]],
                        max_companies=int(llm_maximum),
                        manual_candidates=candidates,
                    )
                    company_ids = [company.id for company in companies]
                status.update(label=f"{len(company_ids)} entreprises identifiées et vérifiées · scan des offres et contacts…", state="running")
                scanned = []
                for company_id in company_ids:
                    try:
                        with factory() as session:
                            company = session.get(Company, company_id)
                            jobs = CareerAgent().scan_company(session, company) if company else None
                            company_name = company.name if company else f"Entreprise #{company_id}"
                        with factory() as session:
                            company = session.get(Company, company_id)
                            contacts = ContactDiscoveryAgent().find_contacts(session, company) if company else None
                        scanned.append({
                            "entreprise": company_name,
                            "offres trouvées": len(jobs.jobs) if jobs else 0,
                            "offres France": sum(1 for job in jobs.jobs if is_france_job(job.country, job.location)) if jobs else 0,
                            "contacts publics": contacts.contacts_found if contacts else 0,
                            "contacts vérifiés": contacts.verified_contacts if contacts else 0,
                        })
                    except Exception as exc:
                        logger.warning("[CAMPAIGN] Company pipeline failed id=%s (%s)", company_id, type(exc).__name__)
                        scanned.append({"entreprise": f"Entreprise #{company_id}", "erreur": type(exc).__name__})
                status.update(label="Recherche et scans terminés", state="complete")
                if scanned:
                    st.dataframe(scanned, width="stretch", hide_index=True)
                if not companies:
                    st.info("Aucun nom n'a pu être confirmé par un site officiel. Les noms sans domaine fiable ont été écartés.")
        except Exception as exc:
            logger.warning("[CAMPAIGN] LLM-first discovery failed (%s)", type(exc).__name__)
            st.error(f"Recherche impossible ({type(exc).__name__}). Vérifie la disponibilité du LLM local et l'accès web du pod.")
        finally:
            if provider:
                provider.close()
        st.rerun()
    with st.form("company_discovery_form"):
        titles = st.text_area("Job titles", "AI Engineer, Machine Learning Engineer, Data Scientist")
        locations = st.text_input("Locations", "France")
        types = st.text_input("Company types", "startup, scale-up, ESN, grand groupe")
        sectors = st.text_input("Sectors", "AI, software, data")
        maximum = st.number_input("Max companies", min_value=1, max_value=20, value=5)
        find = st.form_submit_button("Find companies", type="primary", disabled=not has_discovery_source)
    criteria = {"job_titles": _items(titles), "locations": _items(locations), "company_types": _items(types), "sectors": _items(sectors)}
    if find:
        agent = CompanyDiscoveryAgent()
        with st.spinner("Recherche de sources et vérification des sites officiels…"):
            with factory() as session:
                companies = agent.discover(session, **criteria, max_companies=int(maximum))
                ids = [company.id for company in companies]
            st.caption(f"Recherche et vérification terminées en {agent.last_elapsed_seconds:.2f} s.")
            if not companies:
                st.info("Aucune entreprise vérifiée. Configure une API de recherche ou ajoute des sociétés manuellement ci-dessous.")
            for index, company_id in enumerate(ids, 1):
                status = st.status(f"Analyse de l'entreprise {index}/{len(ids)}…", expanded=False)
                error = _analyze(factory, company_id, criteria=criteria)
                if error:
                    status.update(label=f"Site enregistré ; analyse indisponible : {error[:160]}", state="error")
                else:
                    status.update(label=f"Entreprise {index}/{len(ids)} analysée", state="complete")
        st.rerun()

    st.subheader("Add company manually")
    with st.form("company_manual_form"):
        name = st.text_input("company_name")
        website = st.text_input("website_url", placeholder="https://example.com")
        manual = st.form_submit_button("Analyze company")
    if manual:
        candidate = CompanyCandidate(company_name=name, website_url=website, source_type="manual", source_url=website)
        with factory() as session:
            companies = CompanyDiscoveryAgent(sources=[]).discover(session, job_titles=[], locations=[], company_types=[], sectors=[], max_companies=1, manual_candidates=[candidate])
            company_id = companies[0].id if companies else None
        if company_id is None:
            st.error("Site non accessible, URL tierce, ou domaine officiel non vérifiable. Vérifie l'adresse publique du site.")
        else:
            error = _analyze(factory, company_id, force=False, criteria=criteria)
            if error: st.error(f"Entreprise enregistrée, analyse impossible : {error}")
            else: st.success("Entreprise analysée et enregistrée.")

    st.subheader("Companies")
    with factory() as session:
        companies = list(session.scalars(select(Company).order_by(Company.discovered_at.desc().nullslast(), Company.id.desc()).limit(100)))
        detached = [(company.id, company.name, company.domain, company.website_url or company.website, company.location, company.sector, company.company_type, company.company_relevance_score, company.analysis_json) for company in companies]
    for row in detached:
        company = Company(id=row[0], name=row[1], domain=row[2], website=row[3], location=row[4], sector=row[5], company_type=row[6], company_relevance_score=row[7], analysis_json=row[8])
        # The lightweight detached view needs only display attributes.
        company.website_url = row[3]
        _render_company(company, factory, criteria)
