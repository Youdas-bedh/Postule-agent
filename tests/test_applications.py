import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session

from agents.application_strategy import ApplicationStrategyAgent,GeneratedApplication
from agents.cv_selector import CVSelector
from agents.email_generator import EmailGenerator
from database.database import init_db
from database.models import Application,ApplicationStatus,CVFile,CandidateProfile,Company,Contact,JobOffer
from llm.base import ProviderStatus
from services.cv_service import import_cv

@pytest.fixture
def db():
    engine=create_engine("sqlite:///:memory:");init_db(engine)
    with Session(engine) as session:
        company=Company(name="Acme AI",domain="acme.test",website="https://acme.test",company_relevance_score=75,sector="artificial intelligence",description="Builds applied AI products.")
        profile=CandidateProfile(filename="profile.md",content="# Yasmine Dupont\n\nDesired roles: AI Engineer, Data Scientist.\nSkills: Python, PyTorch, NLP, machine learning.\nExperience: 4 years in machine learning.\nAvailability: September 2026.",content_hash="profile-hash")
        session.add_all([company,profile]);session.flush()
        cv=CVFile(profile_id=profile.id,filename="yasmine_ml_cv.pdf",storage_path="/tmp/yasmine_ml_cv.pdf",extracted_text="Python PyTorch machine learning NLP Data Scientist AI Engineer")
        session.add(cv);session.flush()
        yield session,company,profile,cv

def add_job(session,company,**changes):
    values={"company_id":company.id,"title":"AI Scientist — Agentic Engineering","url":"https://jobs.acme.test/ai-scientist","description":"Build agentic AI systems with Python, PyTorch and NLP.","requirements":"Experience with machine learning.","skills":"[\"Python\", \"PyTorch\", \"NLP\"]","location":"Paris","country":"France","match_score":82,"match_profile_hash":"profile-hash"}
    values.update(changes);job=JobOffer(**values);session.add(job);session.flush();return job

def test_direct_application_prefers_matching_job_and_persists_without_email(db):
    session,company,profile,cv=db;job=add_job(session,company)
    result=ApplicationStrategyAgent().generate(session,company,profile)
    assert result.application_channel=="DIRECT_JOB_APPLICATION" and result.job_offer_id==job.id
    assert result.selected_cv==cv.id and result.status=="DRAFT_READY"
    assert result.application_url==job.url and result.application_notes=="Apply directly on the official job page"
    assert result.subject is None and result.body is None
    row=session.get(Application,result.application_id)
    assert row.status==ApplicationStatus.DRAFT_READY and row.selected_cv==cv.id and row.source_facts_json

def test_campaign_strategy_uses_verified_email_for_matching_job(db):
    session,company,profile,cv=db;job=add_job(session,company)
    contact=Contact(company_id=company.id,name="Camille Martin",role="Talent Acquisition",email="camille@acme.test",
                    source_url="https://acme.test/team",source_type="OFFICIAL_WEBSITE",confidence_score=95,
                    verification_status="VERIFIED")
    session.add(contact);session.flush()
    plan=ApplicationStrategyAgent(prefer_email=True).plan(session,company,profile)
    assert plan.application_channel=="RECRUITER_CONTACT"
    assert plan.contact_id==contact.id and plan.target_job_id==job.id and plan.cv.selected_cv==cv.id

def test_application_strategy_does_not_select_foreign_offers(db):
    session,company,profile,cv=db
    foreign=add_job(session,company,location="Palo Alto",country="United States",match_score=99)
    plan=ApplicationStrategyAgent().plan(session,company,profile)
    assert foreign.id!=plan.target_job_id and plan.status=="NO_ACTION"

def test_spontaneous_verified_recruiter_email_and_grounded_template(db):
    session,company,profile,cv=db
    add_job(session,company,match_score=45,match_profile_hash="profile-hash")
    contact=Contact(company_id=company.id,name="Alex Martin",role="Talent Acquisition",email="talent@acme.test",source_url="https://acme.test/careers",source_type="OFFICIAL_CAREERS",confidence_score=90,verification_status="VERIFIED")
    session.add(contact);session.flush()
    result=ApplicationStrategyAgent().generate(session,company,profile)
    assert result.application_type=="SPONTANEOUS_APPLICATION" and result.application_channel=="RECRUITER_CONTACT"
    assert result.subject and "AI Scientist" in result.subject and 70<=len(result.body.split())<=130
    assert "Python" in result.body and "September 2026" not in result.body
    assert result.status=="DRAFT_READY" and result.contact_id==contact.id
    assert result.source_facts and all(fact.strip() for fact in result.source_facts)

def test_career_form_prepares_motivation_without_submitting(db):
    session,company,profile,cv=db;company.application_form_url="https://acme.test/careers/apply"
    add_job(session,company,match_score=40,match_profile_hash="profile-hash")
    result=ApplicationStrategyAgent().generate(session,company,profile)
    assert result.application_channel=="CAREER_FORM" and result.motivation_text
    assert result.application_url==company.application_form_url and result.body is None
    assert result.status=="DRAFT_READY"

def test_verified_email_channel_and_no_unverified_contact_used(db):
    session,company,profile,cv=db
    unverified=Contact(company_id=company.id,role="Talent Acquisition",email="person@acme.test",source_url="https://acme.test/team",source_type="OFFICIAL_WEBSITE",confidence_score=95,verification_status="UNVERIFIED")
    verified=Contact(company_id=company.id,role="Public company contact",email="contact@acme.test",source_url="https://acme.test/contact",source_type="OFFICIAL_WEBSITE",confidence_score=85,verification_status="VERIFIED")
    session.add_all([unverified,verified]);session.flush()
    result=ApplicationStrategyAgent().generate(session,company,profile)
    assert result.application_channel=="VERIFIED_EMAIL" and result.contact_id==verified.id
    assert result.status=="DRAFT_READY" and result.body

def test_no_action_for_missing_profile_cv_low_relevance_or_contact(db):
    session,company,profile,cv=db
    company.company_relevance_score=10
    result=ApplicationStrategyAgent().generate(session,company,profile)
    assert result.application_channel=="NO_ACTION" and "Company relevance too low" in result.generation_reason
    session.delete(cv);session.flush();company.company_relevance_score=80;company.application_form_url="https://acme.test/apply"
    missing_cv=ApplicationStrategyAgent().generate(session,company,profile)
    assert missing_cv.application_channel=="NO_ACTION" and "Missing CV" in missing_cv.generation_reason
    no_profile=ApplicationStrategyAgent().plan(session,company,None)
    assert no_profile.application_channel=="NO_ACTION" and "Missing profile" in no_profile.generation_reason
    session.add(CVFile(profile_id=profile.id,filename="restored.pdf",storage_path="/tmp/restored.pdf",extracted_text="Python AI Engineer"));session.flush()
    company.application_form_url=None
    no_contact=ApplicationStrategyAgent().generate(session,company,profile)
    assert no_contact.application_channel=="NO_ACTION" and "No reliable contact" in no_contact.generation_reason

def test_cv_selector_chooses_domain_and_skill_relevance(db):
    session,company,profile,cv=db
    other=CVFile(profile_id=profile.id,filename="sales_resume.pdf",storage_path="/tmp/sales.pdf",extracted_text="Salesforce account executive CRM")
    session.add(other);session.flush();job=add_job(session,company)
    selection=CVSelector().select(session,profile,company,job)
    assert selection.selected_cv==cv.id and selection.cv_match_score>0
    assert "Python" in selection.cv_selection_reason

def test_invalid_llm_retries_once_then_uses_safe_template(db):
    session,company,profile,cv=db
    contact=Contact(company_id=company.id,role="Recruiter",email="recruiting@acme.test",source_url="https://acme.test/team",source_type="OFFICIAL_CAREERS",confidence_score=90,verification_status="VERIFIED")
    session.add(contact);session.flush();llm=Mock();llm.generate.side_effect=["not json","{broken"]
    generator=EmailGenerator(llm)
    draft,facts,mode,_=generator.generate(profile,company,None,contact,"RECRUITER_CONTACT")
    assert llm.generate.call_count==2 and mode=="template_invalid_or_unavailable_llm"
    assert draft.body and facts

def test_vllm_unavailable_falls_back_and_hallucinated_skill_is_rejected(db):
    session,company,profile,cv=db
    contact=Contact(company_id=company.id,role="Recruiter",email="recruiting@acme.test",source_url="https://acme.test/team",source_type="OFFICIAL_CAREERS",confidence_score=90,verification_status="VERIFIED")
    session.add(contact);session.flush();job=add_job(session,company,match_score=45,match_profile_hash="old-profile")
    class Unavailable:
        def generate(self,*args,**kwargs):raise RuntimeError("offline")
    result=ApplicationStrategyAgent().generate(session,company,profile,provider=Unavailable())
    assert result.status=="DRAFT_READY" and result.body and "template" in result.generation_reason
    payload=json.dumps({"subject":"Candidature AI Scientist","body":"Bonjour. Mon expérience concerne Kubernetes et JavaScript, et je souhaite vous rejoindre. Merci pour votre attention."})
    assert EmailGenerator._valid(payload,["Source fact"],profile.content,job) is None

def test_cache_and_regenerate_update_same_application_row(db):
    session,company,profile,cv=db;job=add_job(session,company)
    agent=ApplicationStrategyAgent();first=agent.generate(session,company,profile);session.commit()
    cached=agent.generate(session,company,profile)
    assert cached.cached and cached.application_id==first.application_id
    regenerated=agent.generate(session,company,profile,force=True)
    assert not regenerated.cached and regenerated.application_id==first.application_id
    assert len(list(session.scalars(select(Application))))==1
    job.description="Changed job facts";changed=agent.generate(session,company,profile)
    assert not changed.cached and len(list(session.scalars(select(Application))))==1

def test_application_additive_migration_preserves_existing_row():
    engine=create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, domain VARCHAR NOT NULL, website TEXT NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE job_offers (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, title VARCHAR NOT NULL, url TEXT NOT NULL UNIQUE, match_score FLOAT)")
        conn.exec_driver_sql("CREATE TABLE contacts (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name VARCHAR, role VARCHAR, email VARCHAR, source_url TEXT NOT NULL, source_type VARCHAR NOT NULL, confidence_score FLOAT NOT NULL DEFAULT 0, verification_status VARCHAR NOT NULL DEFAULT 'UNVERIFIED')")
        conn.exec_driver_sql("CREATE TABLE applications (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, profile_id INTEGER NOT NULL, contact_id INTEGER, job_offer_id INTEGER, application_type VARCHAR NOT NULL, selected_cv INTEGER, score FLOAT, status VARCHAR NOT NULL, created_at DATETIME, approved_at DATETIME, sent_at DATETIME, followup_date DATETIME)")
        conn.exec_driver_sql("INSERT INTO companies VALUES (1,'Old Co','old.test','https://old.test')")
        conn.exec_driver_sql("INSERT INTO applications (id,company_id,profile_id,application_type,status) VALUES (1,1,5,'SPONTANEOUS','DISCOVERED')")
    init_db(engine)
    with engine.connect() as conn:
        cols={row[1] for row in conn.exec_driver_sql("PRAGMA table_info(applications)")}
        assert {"application_channel","subject","body","short_message","motivation_text","generation_reason","source_facts_json","cv_match_score","cache_hash","approved_at","rejected_at","ignored_at","edited_at","edited_by_user","decision_reason","final_subject","final_body","final_short_message","final_motivation_text"}<=cols
        assert conn.exec_driver_sql("SELECT application_type,status FROM applications WHERE id=1").one()==("SPONTANEOUS","DISCOVERED")

def test_cv_import_keeps_original_bytes_and_extracts_only_for_scoring(monkeypatch,tmp_path,db):
    session,company,profile,cv=db
    import services.cv_service as cv_service
    monkeypatch.setattr(cv_service,"ROOT",tmp_path)
    original=b"# CV\nPython, PyTorch, NLP\n"
    imported=import_cv(session,profile,"../Yasmine_CV.md",original)
    stored=Path(imported.storage_path)
    assert stored.read_bytes()==original and imported.filename=="Yasmine_CV.md"
    assert "PyTorch" in imported.extracted_text and cv.filename!="Yasmine_CV.md"

def test_applications_streamlit_page_renders_no_action_reason(monkeypatch,tmp_path):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    from database.database import init_db
    engine=create_engine(f"sqlite:///{tmp_path/'applications-ui.db'}");init_db(engine)
    with Session(engine) as session:
        profile=CandidateProfile(filename="profile.md",content="Python",content_hash="ui")
        company=Company(name="UI Company",domain="ui.test",website="https://ui.test",company_relevance_score=60,application_form_url="https://ui.test/apply")
        session.add_all([profile,company]);session.commit()
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path/'applications-ui.db'}");get_settings.cache_clear();st.cache_resource.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Applications").run()
    assert not app.exception and any("Applications" in item.value for item in app.header)
    assert any("Missing CV" in item.value for item in app.warning)
    get_settings.cache_clear();st.cache_resource.clear()

def test_campaign_page_is_available_without_starting_external_work(monkeypatch,tmp_path):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    from database.database import init_db
    engine=create_engine(f"sqlite:///{tmp_path/'campaign-ui.db'}");init_db(engine)
    with Session(engine) as session:
        profile=CandidateProfile(filename="profile.md",content="Python and machine learning",content_hash="campaign-ui")
        session.add(profile);session.commit()
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path/'campaign-ui.db'}");get_settings.cache_clear();st.cache_resource.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Campagne").run()
    assert not app.exception
    assert any("Lancer les candidatures" in item.label for item in app.button)
    assert any("Aucun email" in item.value for item in app.info)
    get_settings.cache_clear();st.cache_resource.clear()
