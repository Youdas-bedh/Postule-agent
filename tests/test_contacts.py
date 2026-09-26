import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session

from agents.contact_discovery import ContactDiscoveryAgent
from agents.contact_verifier import extract_emails,infer_email_pattern,parse_role_assessment,rank_contacts,validate_email
from database.database import init_db
from database.models import Base,CandidateProfile,Company,Contact,JobOffer
from scrapers.contacts import ContactScraper,PublicContact

@pytest.fixture
def db():
    engine=create_engine("sqlite:///:memory:");init_db(engine)
    with Session(engine) as session:
        company=Company(name="Acme",domain="acme.test",website="https://acme.test/",website_url="https://acme.test/",career_url="https://acme.test/careers")
        session.add(company);session.commit();yield session,company

class Response:
    def __init__(self,url,html,status=200):self.url=url;self.text=html;self.status_code=status;self.headers={"content-type":"text/html"}
    def raise_for_status(self):
        if self.status_code>=400:
            import httpx
            req=httpx.Request("GET",self.url);raise httpx.HTTPStatusError("bad",request=req,response=httpx.Response(self.status_code,request=req))
class Client:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def get(self,url,**kw):
        self.calls.append(url)
        return self.pages.get(url,Response(url,"",404))

def test_email_validation_extract_and_inference_is_never_verified():
    assert extract_emails("Hiring: TALENT@acme.test, or bad@other.test.")==["talent@acme.test","bad@other.test"]
    assert validate_email("talent@acme.test","acme.test",check_mx=False).verification_status=="VERIFIED"
    assert validate_email("talent@other.test","acme.test",check_mx=False).verification_status=="UNVERIFIED"
    assert validate_email("bad..dot@acme.test","acme.test",check_mx=False).verification_status=="UNVERIFIED"
    assert validate_email("x@mailinator.com","acme.test",check_mx=False).disposable
    inferred=infer_email_pattern("Ada Lovelace","acme.test")
    assert inferred and inferred.verification_status=="UNVERIFIED" and inferred.confidence_score==30

def test_email_rejects_invalid_dot_placement():
    for address in (".leading@acme.test","trailing.@acme.test","two..dots@acme.test"):
        assert not validate_email(address,"acme.test",check_mx=False).syntax_valid

def test_public_scanner_extracts_email_and_form_and_prefers_relevant_pages(db):
    session,company=db
    home="https://acme.test/";careers="https://acme.test/careers";team="https://acme.test/team"
    client=Client({home:Response(home,'<a href="/careers">Careers</a><a href="/team">Our team</a>'),careers:Response(careers,'<a href="mailto:talent@acme.test">Recruitment</a><a href="/apply">General application</a>'),team:Response(team,'<p>Contact jobs@acme.test</p>')})
    result=ContactScraper(client=client,max_pages=3).scan(session,company)
    assert result.status=="complete" and result.pages_scanned==3
    assert {x.email for x in result.contacts} >= {"talent@acme.test","jobs@acme.test"}
    assert result.application_form_url=="https://acme.test/apply"
    assert client.calls[0]==careers

def test_scanner_restricts_linkedin_and_invalid_llm_role():
    assert parse_role_assessment('{"role":"Recruiter"}')=="Recruiter"
    assert parse_role_assessment('{broken') is None

def test_contact_priority_prefers_recruitment_channel(db):
    session,company=db
    generic=Contact(company_id=company.id,email="info@acme.test",role="Public company contact",source_url="https://acme.test",source_type="OFFICIAL_WEBSITE",confidence_score=100,verification_status="VERIFIED")
    talent=Contact(company_id=company.id,email="talent@acme.test",role="Recruitment",source_url="https://acme.test/careers",source_type="OFFICIAL_CAREERS",confidence_score=90,verification_status="VERIFIED")
    assert rank_contacts([generic,talent])[0] is talent

def test_discovery_persists_deduplicates_and_uses_seven_day_cache(db):
    session,company=db
    candidate={"email":"talent@acme.test","role":"Recruitment","source_url":"https://acme.test/careers","source_type":"OFFICIAL_CAREERS","confidence_score":90,"verification_status":"VERIFIED","email_domain_match":True,"email_mx_status":"present","email_is_disposable":False}
    scraper=Mock();scraper.scan.return_value=type("Result",(),{"contacts":[PublicContact(**candidate)],"application_form_url":"https://acme.test/apply","status":"complete","requests":2,"pages_scanned":2,"elapsed_seconds":.1,"errors":[]})()
    agent=ContactDiscoveryAgent(scraper=scraper,min_confidence=80)
    result=agent.find_contacts(session,company)
    assert result.contacts_found==1 and result.preferred_channel=="RECRUITER_CONTACT"
    contact=session.scalar(select(Contact));assert contact.email=="talent@acme.test" and contact.verified_at
    cached=agent.find_contacts(session,company)
    assert cached.cached and scraper.scan.call_count==1
    forced=agent.find_contacts(session,company,force=True)
    assert scraper.scan.call_count==2 and forced.contacts_found==1
    assert len(list(session.scalars(select(Contact))))==1

def test_direct_job_application_requires_score_for_active_profile(db):
    session,company=db
    profile=CandidateProfile(filename="profile.md",content="Python",content_hash="profile-hash");session.add(profile);session.flush()
    session.add(JobOffer(company_id=company.id,title="AI Engineer",url="https://acme.test/job",location="Paris",country="France",match_score=85,match_profile_hash="profile-hash"));session.commit()
    scraper=Mock();scraper.scan.return_value=type("Result",(),{"contacts":[],"application_form_url":None,"status":"empty","requests":0,"pages_scanned":0,"elapsed_seconds":.0,"errors":[]})()
    out=ContactDiscoveryAgent(scraper=scraper).find_contacts(session,company,force=True)
    assert out.preferred_channel=="DIRECT_JOB_APPLICATION"

def test_streamlit_contacts_page_renders_and_migration_is_additive(db):
    session,company=db
    legacy=Contact(company_id=company.id,name="Existing recruiter",role="Recruiter",email="old@acme.test",source_url="https://acme.test/team",source_type="OFFICIAL_WEBSITE",confidence_score=80,verification_status="PROBABLE")
    session.add(legacy);session.commit();legacy_id=legacy.id
    session.expire_all()
    assert session.get(Contact,legacy_id).email=="old@acme.test"
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    import os,tempfile
    fd,path=tempfile.mkstemp(suffix=".db");os.close(fd)
    url=f"sqlite:///{path}";os.environ["DATABASE_URL"]=url;get_settings.cache_clear();st.cache_resource.clear()
    ui_engine=create_engine(url);init_db(ui_engine)
    with Session(ui_engine) as ui_session:
        ui_company=Company(name="UI Acme",domain="ui-acme.test",website="https://ui-acme.test",website_url="https://ui-acme.test")
        ui_session.add(ui_company);ui_session.flush()
        ui_session.add(Contact(company_id=ui_company.id,name="Recruiter",role="Talent Acquisition",email="talent@ui-acme.test",source_url="https://ui-acme.test/careers",source_type="OFFICIAL_CAREERS",confidence_score=90,verification_status="VERIFIED",email_domain_match=True,email_mx_status="present",email_is_disposable=False));ui_session.commit()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Contacts").run()
    assert not app.exception and any("Contacts" in item.value for item in app.header)
    assert any("UI Acme" in item.value for item in app.subheader)
    assert any("Source" in item.label for item in app.selectbox)
    get_settings.cache_clear();st.cache_resource.clear()

def test_legacy_contacts_table_migration_preserves_existing_rows():
    engine=create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, domain VARCHAR(255) NOT NULL UNIQUE, website TEXT NOT NULL)")
        conn.exec_driver_sql("INSERT INTO companies (id,name,domain,website) VALUES (1,'Old Co','old.test','https://old.test')")
        conn.exec_driver_sql("CREATE TABLE contacts (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name VARCHAR, role VARCHAR, email VARCHAR, source_url TEXT NOT NULL, source_type VARCHAR NOT NULL, confidence_score FLOAT NOT NULL DEFAULT 0, verification_status VARCHAR NOT NULL DEFAULT 'UNVERIFIED')")
        conn.exec_driver_sql("INSERT INTO contacts (id,company_id,name,role,email,source_url,source_type,confidence_score,verification_status) VALUES (1,1,'Recruiter','Recruiter','r@old.test','https://old.test/team','OFFICIAL_WEBSITE',70,'PROBABLE')")
    init_db(engine)
    with engine.connect() as conn:
        columns={row[1] for row in conn.exec_driver_sql("PRAGMA table_info(contacts)")}
        assert {"linkedin_url","discovered_at","verified_at","email_domain_match","email_mx_status","email_is_disposable"}<=columns
        stored=conn.exec_driver_sql("SELECT email,verification_status FROM contacts WHERE id=1").one()
        assert tuple(stored)==("r@old.test","PROBABLE")
