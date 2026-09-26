import json
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from agents.job_matcher import JobMatcher, JobMatchResult, parse_llm_assessment
from database.database import init_db
from database.models import CandidateProfile, Company, JobOffer

@pytest.fixture
def db():
    engine=create_engine("sqlite:///:memory:");init_db(engine)
    with Session(engine) as session:
        company=Company(name="Mistral AI",domain="mistral.test",website="https://mistral.test")
        profile=CandidateProfile(filename="profile.md",content="# Profil\nCompétences: Python, PyTorch, SQL, Machine Learning, AWS, Kubernetes, NLP.\nExpérience: 6 years en IA.\nFormation: Master's degree.\nLocation: Paris.\nPoste souhaité: ML Engineer.",content_hash="profile-v1")
        session.add_all([company,profile]);session.flush()
        yield session,profile,company

def offer(company,**kw):
    values={"company_id":company.id,"title":"Senior Machine Learning Engineer","url":"https://jobs.test/ml","description":"Build production machine learning models with Python and PyTorch.","requirements":"5+ years experience. Master's degree required.","skills":"[\"Python\", \"PyTorch\", \"SQL\", \"Machine Learning\"]","location":"Paris","country":"FR","remote_policy":"Hybrid","employment_type":"Full-time","experience_level":"senior","department":"AI Research"}
    values.update(kw);return JobOffer(**values)

def assessment(profile_quote="Compétences: Python, PyTorch, SQL, Machine Learning, AWS, Kubernetes, NLP.",job_quote="Build production machine learning models with Python and PyTorch.",**kw):
    data={"matching_skills":["Python","PyTorch","SQL","Machine Learning"],"missing_skills":[],"strengths":[{"text":"Expérience pratique en machine learning de production","profile_evidence":profile_quote,"job_evidence":job_quote}],"concerns":[],"experience_match":"match","location_match":True,"education_match":"match","role_alignment":90,"role_profile_evidence":"Poste souhaité: ML Engineer.","role_job_evidence":"Senior Machine Learning Engineer","experience_profile_evidence":"Expérience: 6 years en IA.","experience_job_evidence":"5+ years experience.","education_profile_evidence":"Master's degree.","education_job_evidence":"Master's degree required.","location_profile_evidence":"Location: Paris.","location_job_evidence":"Paris"}
    data.update(kw);return json.dumps(data)

def test_high_score_and_grounded_skills(db):
    session,profile,company=db;job=offer(company)
    provider=Mock();provider.generate.return_value=assessment()
    result=JobMatcher(provider).match(session,profile,job,company_name=company.name)
    assert result.match_score>=80 and result.experience_match=="match" and result.location_match is True
    assert {"Python","PyTorch","SQL","Machine Learning"}<=set(result.matching_skills)
    assert job.match_score==result.match_score and job.matching_json and job.matched_at

def test_medium_score_partial_coverage(db):
    session,profile,company=db
    profile.content="# Profil\nCompétences: Python, SQL.\nExpérience: 2 years.\nFormation: Bachelor degree.\nLocation: Lyon."
    profile.content_hash="medium-profile"
    job=offer(company,title="Data Engineer",description="Build batch data pipelines with Python, SQL, Airflow and Kafka.",requirements="3+ years experience. Bachelor's degree.",skills='["Python","SQL","Airflow","Kafka"]',location="Lyon",experience_level="mid")
    provider=Mock();provider.generate.return_value=assessment(profile_quote="Compétences: Python, SQL.",job_quote="Build batch data pipelines with Python, SQL, Airflow and Kafka.",matching_skills=["Python","SQL"],missing_skills=["Airflow","Kafka"],experience_match="partial",role_alignment=55,role_profile_evidence="Compétences: Python, SQL.",role_job_evidence="Data Engineer")
    result=JobMatcher(provider).match(session,profile,job)
    assert 35<=result.match_score<=75 and set(result.missing_skills)>={"Airflow","Kafka"}

def test_low_score_experience_location_education_and_missing_skills(db):
    session,profile,company=db
    profile.content="# Profile\nCompétences: Tableau.\nExpérience: 1 year.\nFormation: Bachelor degree.\nLocation: Paris."
    profile.content_hash="low-profile"
    job=offer(company,title="Senior Cloud MLOps Engineer",description="Operate Kubernetes, Terraform, AWS and MLOps platforms.",requirements="8+ years experience. PhD required.",skills='["Kubernetes","Terraform","AWS","MLOps"]',location="Marseille",country="France",experience_level="senior")
    provider=Mock();provider.generate.return_value=assessment(profile_quote="Compétences: Tableau.",job_quote="Operate Kubernetes, Terraform, AWS and MLOps platforms.",matching_skills=["Kubernetes"],missing_skills=["Kubernetes","Terraform","AWS","MLOps"],experience_match="insufficient",location_match=False,education_match="below requirement",role_alignment=10,role_profile_evidence="Compétences: Tableau.",role_job_evidence="Senior Cloud MLOps Engineer",experience_profile_evidence="Expérience: 1 year.",experience_job_evidence="8+ years experience.",education_profile_evidence="Bachelor degree.",education_job_evidence="PhD required.",location_profile_evidence="Location: Paris.",location_job_evidence="Marseille")
    result=JobMatcher(provider).match(session,profile,job)
    assert result.match_score<40 and result.experience_match=="insufficient" and result.location_match is False
    assert "PhD" in (result.education_match or "") or result.education_match=="below requirement"
    assert {"AWS","MLOps","Terraform"}<=set(result.missing_skills)
    assert any("incompatible" in x for x in result.concerns)

def test_llm_cannot_invent_candidate_skills(db):
    session,profile,company=db
    profile.content="Candidate has customer support experience only.";profile.content_hash="no-skills"
    job=offer(company)
    provider=Mock();provider.generate.return_value=assessment(profile_quote="Candidate has customer support experience only.",matching_skills=["Python","PyTorch"],missing_skills=["Python","PyTorch","SQL"])
    result=JobMatcher(provider).match(session,profile,job)
    assert result.matching_skills==[]
    assert {"Python","PyTorch","SQL"}<=set(result.missing_skills)

def test_invalid_llm_output_uses_deterministic_fallback(db):
    session,profile,company=db;job=offer(company)
    with pytest.raises(ValueError,match="Sortie LLM"):parse_llm_assessment("not-json")
    provider=Mock();provider.generate.return_value="not-json"
    result=JobMatcher(provider).match(session,profile,job)
    assert result.analysis_mode=="deterministic" and 0<=result.match_score<=100
    assert provider.generate.call_count==2

def test_matching_rejects_foreign_or_unknown_job_location(db):
    session,profile,company=db
    job=offer(company,location="Palo Alto",country=None)
    with pytest.raises(ValueError,match="localisées en France"):
        JobMatcher().match(session,profile,job)

def test_cache_and_invalidation(db):
    session,profile,company=db;job=offer(company)
    provider=Mock();provider.generate.return_value=assessment()
    matcher=JobMatcher(provider)
    first=matcher.match(session,profile,job);session.commit()
    cached=matcher.match(session,profile,job)
    assert cached.cached and provider.generate.call_count==1
    job.description="Different job contents requiring backend experience."
    changed=matcher.match(session,profile,job)
    assert not changed.cached and provider.generate.call_count==2
    profile.content_hash="profile-v2"
    again=matcher.match(session,profile,job)
    assert not again.cached and provider.generate.call_count==3

def test_persistence_survives_reload(db):
    session,profile,company=db;job=offer(company);session.add(job);session.flush()
    provider=Mock();provider.generate.return_value=assessment()
    result=JobMatcher(provider).match(session,profile,job);session.commit();job_id=job.id
    reloaded=session.get(JobOffer,job_id)
    payload=JobMatchResult.model_validate_json(reloaded.matching_json)
    assert reloaded.match_score==result.match_score and payload.match_score==result.match_score and reloaded.match_profile_hash==profile.content_hash

def test_streamlit_job_matching_page_without_profile(monkeypatch,tmp_path):
    import streamlit as st
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path/'matching-ui.db'}")
    get_settings.cache_clear();st.cache_resource.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Job Matching").run()
    assert not app.exception
    assert any("Importe un profil" in x.value for x in app.warning)
    get_settings.cache_clear();st.cache_resource.clear()

def test_streamlit_run_matching_action(monkeypatch,tmp_path):
    import streamlit as st
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    from llm.base import ProviderStatus
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path/'matching-run.db'}")
    get_settings.cache_clear();st.cache_resource.clear()
    engine=create_engine(f"sqlite:///{tmp_path/'matching-run.db'}");init_db(engine)
    with Session(engine) as session:
        company=Company(name="Acme",domain="acme.test",website="https://acme.test")
        profile=CandidateProfile(filename="profile.md",content="Python and machine learning.\nPostes recherchés: AI Engineer.",content_hash="ui-profile")
        session.add_all([company,profile]);session.flush()
        session.add(JobOffer(company_id=company.id,title="AI Engineer",url="https://acme.test/job",description="Build machine learning services in Python.",skills='["Python", "Machine Learning"]',location="Paris"));session.commit()
    llm_result={"matching_skills":["Python","Machine Learning"],"missing_skills":[],"strengths":[],"concerns":[],"experience_match":"unknown","location_match":None,"education_match":None,"role_alignment":90,"role_profile_evidence":"AI Engineer","role_job_evidence":"AI Engineer"}
    provider=Mock();provider.check_connection.return_value=ProviderStatus("vllm","test/model","http://localhost/v1",True,"ready");provider.generate.return_value=json.dumps(llm_result)
    monkeypatch.setattr("ui.job_matching.create_provider",lambda _:provider)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Job Matching").run()
    assert not app.exception
    app.button[0].click().run()
    assert not app.exception and provider.generate.call_count==1
    with Session(engine) as session:
        stored=session.scalar(select(JobOffer))
        assert stored.match_score is not None and stored.matching_json
    get_settings.cache_clear();st.cache_resource.clear()
