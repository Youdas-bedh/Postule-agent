import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from agents.application_strategy import ApplicationStrategyAgent
from agents.application_workflow import ApplicationWorkflowError,application_events,approve_draft,edit_draft,ignore_draft,reject_draft
from database.database import init_db
from database.models import Application,ApplicationEvent,ApplicationStatus,CVFile,CandidateProfile,Company,JobOffer

@pytest.fixture
def draft_db():
    engine=create_engine("sqlite:///:memory:");init_db(engine)
    with Session(engine) as session:
        company=Company(name="Mistral AI",domain="mistral.test",website="https://mistral.test",company_relevance_score=75)
        profile=CandidateProfile(filename="profile.md",content="Python, PyTorch, NLP",content_hash="p1")
        session.add_all([company,profile]);session.flush()
        cv=CVFile(profile_id=profile.id,filename="cv.pdf",storage_path="/tmp/cv.pdf",extracted_text="Python PyTorch NLP")
        job=JobOffer(company_id=company.id,title="AI Scientist",url="https://jobs.mistral.test/ai",description="Python PyTorch NLP",location="Paris",country="France",match_score=80,match_profile_hash="p1")
        session.add_all([cv,job]);session.flush()
        application=Application(company_id=company.id,profile_id=profile.id,job_offer_id=job.id,application_type="DIRECT_JOB_APPLICATION",selected_cv=cv.id,score=80,status=ApplicationStatus.DRAFT_READY,application_channel="DIRECT_JOB_APPLICATION",application_strategy="Apply directly on the official job page",application_url=job.url,cv_match_score=100,cv_selection_reason="Only CV",short_message="Review application.",generation_reason="Matching offer.",source_facts_json=json.dumps(["Offer: AI Scientist","Matching score: 80"]))
        session.add(application);session.flush()
        yield session,company,profile,cv,job,application

def test_manual_edit_persists_final_fields_and_audit_without_content(draft_db):
    session,company,profile,cv,job,application=draft_db
    edit_draft(session,application,subject="Subject",body="Edited body",short_message="Short",motivation_text="Motivation")
    session.commit();session.refresh(application)
    assert application.status==ApplicationStatus.DRAFT_READY and application.edited_by_user and application.edited_at
    assert (application.final_subject,application.final_body,application.final_short_message,application.final_motivation_text)==("Subject","Edited body","Short","Motivation")
    events=application_events(session,application.id)
    assert [event.event for event in events]==["EDITED"]
    assert "Edited body" not in (events[0].details or "")

def test_approve_preserves_content_cv_route_and_records_timestamp(draft_db):
    session,company,profile,cv,job,application=draft_db
    edit_draft(session,application,subject="Final subject",body="Final body",short_message="Final note",motivation_text="Final motivation")
    approve_draft(session,application);session.commit();session.refresh(application)
    assert application.status==ApplicationStatus.APPROVED and application.approved_at
    assert application.final_body=="Final body" and application.selected_cv==cv.id
    assert application.application_channel=="DIRECT_JOB_APPLICATION" and application.job_offer_id==job.id and application.application_url==job.url
    assert [x.event for x in application_events(session,application.id)]==["EDITED","APPROVED"]
    with pytest.raises(ApplicationWorkflowError):edit_draft(session,application,subject="changed",body=None,short_message=None,motivation_text=None)

@pytest.mark.parametrize("decision,status,event,stamp",[(reject_draft,ApplicationStatus.REJECTED,"REJECTED","rejected_at"),(ignore_draft,ApplicationStatus.IGNORED,"IGNORED","ignored_at")])
def test_reject_and_ignore_are_terminal_and_audited(draft_db,decision,status,event,stamp):
    session,company,profile,cv,job,application=draft_db
    decision(session,application,"Not pursuing this opportunity")
    session.commit();session.refresh(application)
    assert application.status==status and getattr(application,stamp) and application.decision_reason
    assert application_events(session,application.id)[0].event==event
    with pytest.raises(ApplicationWorkflowError):approve_draft(session,application)

def test_generation_audit_and_approved_regeneration_guard(draft_db):
    session,company,profile,cv,job,application=draft_db
    agent=ApplicationStrategyAgent()
    first=agent.generate(session,company,profile,force=True);session.commit()
    row=session.get(Application,first.application_id)
    assert [x.event for x in application_events(session,row.id)]==["REGENERATED"]
    approve_draft(session,row);session.commit()
    with pytest.raises(ApplicationWorkflowError,match="confirmation explicite"):
        agent.generate(session,company,profile,force=True)
    second=agent.generate(session,company,profile,force=True,confirm_approved=True);session.commit()
    assert row.status==ApplicationStatus.APPROVED and second.status=="DRAFT_READY" and second.application_id!=row.id
    assert session.scalar(select(Application).where(Application.status==ApplicationStatus.SENT)) is None

def test_preexisting_draft_gets_safe_generated_audit_backfill():
    engine=create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, domain VARCHAR NOT NULL, website TEXT NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE candidate_profiles (id INTEGER PRIMARY KEY, filename VARCHAR NOT NULL, content TEXT NOT NULL, content_hash VARCHAR NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE applications (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, profile_id INTEGER NOT NULL, application_type VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at DATETIME)")
        connection.exec_driver_sql("CREATE TABLE job_offers (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, title VARCHAR NOT NULL, url TEXT NOT NULL UNIQUE, match_score FLOAT)")
        connection.exec_driver_sql("CREATE TABLE contacts (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, source_url TEXT NOT NULL, source_type VARCHAR NOT NULL, confidence_score FLOAT NOT NULL DEFAULT 0, verification_status VARCHAR NOT NULL DEFAULT 'UNVERIFIED')")
        connection.exec_driver_sql("INSERT INTO companies VALUES (1,'Old Co','old.test','https://old.test')")
        connection.exec_driver_sql("INSERT INTO candidate_profiles VALUES (1,'p.md','profile','hash')")
        connection.exec_driver_sql("INSERT INTO applications VALUES (1,1,1,'SPONTANEOUS_APPLICATION','DRAFT_READY','2025-01-01')")
    init_db(engine)
    with Session(engine) as session:
        event=session.scalar(select(ApplicationEvent).where(ApplicationEvent.application_id==1))
        assert event.event=="GENERATED" and "Pre-existing" in event.details

def test_streamlit_edit_and_approve_interaction(monkeypatch,tmp_path):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    from database.database import init_db
    db_path=tmp_path/"approval-ui.db";engine=create_engine(f"sqlite:///{db_path}");init_db(engine)
    with Session(engine) as session:
        profile=CandidateProfile(filename="profile.md",content="Python",content_hash="ui-profile")
        company=Company(name="Approval Co",domain="approval.test",website="https://approval.test")
        session.add_all([profile,company]);session.flush()
        cv=CVFile(profile_id=profile.id,filename="cv.pdf",storage_path="/tmp/cv.pdf",extracted_text="Python")
        job=JobOffer(company_id=company.id,title="Backend Engineer",url="https://approval.test/job",match_score=80,match_profile_hash="ui-profile")
        session.add_all([cv,job]);session.flush()
        application=Application(company_id=company.id,profile_id=profile.id,job_offer_id=job.id,application_type="DIRECT_JOB_APPLICATION",selected_cv=cv.id,score=80,status=ApplicationStatus.DRAFT_READY,application_channel="DIRECT_JOB_APPLICATION",application_url=job.url,application_strategy="Apply directly",cv_match_score=90,subject="Draft subject",short_message="Draft note",source_facts_json='["Python is in the profile"]')
        session.add(application);session.commit();ident=application.id
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{db_path}");get_settings.cache_clear();st.cache_resource.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Applications").run()
    assert not app.exception and any("Preview final application" in item.label for item in app.expander)
    assert any("CV sélectionné" in item.value and "cv.pdf" in item.value for item in app.markdown)
    next(button for button in app.button if button.label=="EDIT").click().run()
    next(widget for widget in app.text_input if widget.label=="Subject").set_value("Human edited subject").run()
    next(button for button in app.button if button.label=="Save edits").click().run()
    with Session(engine) as session:
        row=session.get(Application,ident);assert row.subject=="Human edited subject" and row.edited_by_user
    next(button for button in app.button if button.label=="APPROVE").click().run()
    with Session(engine) as session:
        row=session.get(Application,ident);assert row.status==ApplicationStatus.APPROVED and row.approved_at and row.final_subject=="Human edited subject"
    get_settings.cache_clear();st.cache_resource.clear()
