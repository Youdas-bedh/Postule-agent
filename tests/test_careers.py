import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from database.database import init_db
from database.models import Base, Company, JobOffer
from careers.base import detect_ats
from careers.generic import career_links, parse_jobs
from scrapers.careers import CareerScraper
from agents.career_agent import CareerAgent, persist_offers
from utils.job_urls import normalize_job_url

@pytest.fixture
def db():
    engine=create_engine("sqlite:///:memory:"); init_db(engine)
    with Session(engine) as session:
        company=Company(name="Acme",domain="acme.test",website="https://acme.test/",website_url="https://acme.test/")
        session.add(company);session.commit();yield session,company

def test_career_link_and_known_path():
    links=career_links('<a href="/jobs">Join us</a><a href="/about">About</a>','https://acme.test/')
    assert links==["https://acme.test/jobs"]
    assert "/offres-emploi" in __import__('careers.generic',fromlist=['CAREER_PATHS']).CAREER_PATHS

@pytest.mark.parametrize("url,expected",[("https://boards.greenhouse.io/acme","greenhouse"),("https://jobs.lever.co/acme","lever"),("https://acme.wd3.myworkdayjobs.com/jobs","workday"),("https://jobs.smartrecruiters.com/acme","smartrecruiters"),("https://jobs.ashbyhq.com/acme","ashby"),("https://acme.com/jobs",None)])
def test_detect_ats(url,expected): assert detect_ats(url)==expected

def test_jsonld_extraction_and_keyword_filter():
    posting={"@context":"https://schema.org","@type":"JobPosting","title":"Senior ML Engineer","identifier":{"value":"abc-1"},"datePosted":"2025-02-01T00:00:00Z","description":"<p>Build machine learning services</p>","jobLocation":{"address":{"addressLocality":"Paris","addressCountry":"FR"}},"employmentType":"FULL_TIME","baseSalary":{"currency":"EUR","value":{"minValue":70000,"maxValue":90000}}}
    html='<script type="application/ld+json">'+json.dumps(posting)+'</script>'
    result=parse_jobs(html,"https://acme.test/jobs/1")
    assert len(result)==1 and result[0].title=="Senior ML Engineer"
    assert result[0].external_id=="abc-1" and result[0].location=="Paris" and result[0].salary_min==70000
    assert result[0].is_potentially_relevant and "Machine Learning" in result[0].relevance_keywords

def test_generic_html_and_malformed_page():
    html='<article class="job-card" data-job-id="42"><h2>Data Scientist</h2><a href="/jobs/42">Apply</a><p>AI team</p></article>'
    jobs=parse_jobs(html,"https://acme.test/careers")
    assert jobs[0].job_url=="https://acme.test/jobs/42" and jobs[0].is_potentially_relevant
    assert parse_jobs('<script type="application/ld+json">{broken</script>',"https://acme.test") == []

def test_job_url_normalization():
    assert normalize_job_url("http://www.acme.test/jobs/1/?utm_source=x&ref=foo#apply")=="https://acme.test/jobs/1"
    with pytest.raises(ValueError):normalize_job_url("javascript:alert(1)")

def test_additive_job_offer_migration_preserves_legacy_row():
    engine=create_engine("sqlite:///:memory:")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, domain VARCHAR(255) NOT NULL UNIQUE, website TEXT NOT NULL, location VARCHAR, sector VARCHAR, size VARCHAR, description TEXT, linkedin_url TEXT, career_url TEXT, source_url TEXT)")
        c.exec_driver_sql("INSERT INTO companies (id,name,domain,website) VALUES (1,'Legacy','legacy.test','https://legacy.test')")
        c.exec_driver_sql("CREATE TABLE job_offers (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, title VARCHAR NOT NULL, url TEXT NOT NULL UNIQUE, description TEXT, location VARCHAR, match_score FLOAT)")
        c.exec_driver_sql("INSERT INTO job_offers (id,company_id,title,url) VALUES (1,1,'Existing role','https://legacy.test/jobs/1')")
    init_db(engine)
    with engine.connect() as c:
        cols={x[1] for x in c.exec_driver_sql("PRAGMA table_info(job_offers)")}
        assert {"external_id","remote_policy","is_potentially_relevant","relevance_keywords","updated_at","match_profile_hash","match_offer_hash","matching_json","matched_at"}<=cols
        assert c.exec_driver_sql("SELECT title FROM job_offers WHERE id=1").scalar_one()=="Existing role"

def test_dedupe_external_id_then_url_then_title_and_update(db):
    session,company=db
    first=parse_jobs('<script type="application/ld+json">'+json.dumps({"@type":"JobPosting","title":"ML Engineer","identifier":{"value":"id1"},"url":"https://acme.test/jobs/1","description":"Machine Learning"})+'</script>',"https://acme.test/jobs/1")[0]
    assert persist_offers(session,company,[first])==(1,0);session.commit()
    row=session.scalar(select(JobOffer)); original_discovered=row.discovered_at; original_updated=row.updated_at
    changed=first.model_copy(update={"description":"Updated machine learning role"})
    assert persist_offers(session,company,[changed])==(0,1);session.commit()
    assert session.scalar(select(JobOffer.id))==row.id and row.discovered_at==original_discovered and row.updated_at>=original_updated
    duplicate=changed.model_copy(update={"external_id":"different-id","job_url":"https://acme.test/jobs/1?utm_campaign=x"})
    assert persist_offers(session,company,[duplicate])==(0,0)

class FakeResponse:
    def __init__(self,url,text,status=200,headers=None): self.url=url;self.text=text;self.status_code=status;self.headers=headers or {"content-type":"text/html"}
    def raise_for_status(self):
        if self.status_code>=400:
            import httpx
            request=httpx.Request("GET",self.url);response=httpx.Response(self.status_code,request=request)
            raise httpx.HTTPStatusError("bad status",request=request,response=response)
    def json(self):return json.loads(self.text)
class FakeClient:
    def __init__(self,mapping):self.mapping=mapping;self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append(url)
        if url not in self.mapping: raise AssertionError("unexpected URL: "+url)
        return self.mapping[url]

def test_career_scraper_detects_page_and_handles_empty_or_errors():
    home='https://acme.test/'; careers='https://acme.test/careers'
    client=FakeClient({home:FakeResponse(home,'<a href="/careers">Careers</a>'),careers:FakeResponse(careers,'<main><h1>Open positions</h1><p>See current jobs</p></main>')})
    result=CareerScraper(client=client).scan(home)
    assert result.status=="empty" and result.career_url==careers and result.requests==2
    bad=FakeClient({home:FakeResponse(home,"blocked",403)})
    assert CareerScraper(client=bad).scan(home).status=="forbidden"

def test_db_persistence_cache(db):
    session,company=db
    offer=parse_jobs('<article class="job-card"><h2>Software Engineer</h2><a href="/jobs/2">View</a></article>',"https://acme.test/careers")[0]
    class Stub:
        def scan(self,*args):return SimpleNamespace(career_url="https://acme.test/careers",ats_type=None,jobs=[offer],status="complete",error=None,requests=1,elapsed_seconds=.1)
    agent=CareerAgent(scraper=Stub())
    assert len(agent.scan_company(session,company,force=True).jobs)==1
    assert session.scalar(select(JobOffer)).title=="Software Engineer"
    class Never:
        def scan(self,*args):raise AssertionError("cache should prevent network")
    cached=CareerAgent(scraper=Never()).scan_company(session,company)
    assert cached.status=="cached" and len(cached.jobs)==1

def test_career_jobs_streamlit_page_renders(monkeypatch,tmp_path):
    import streamlit as st
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path/'careers-ui.db'}")
    get_settings.cache_clear();st.cache_resource.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
    app.sidebar.radio[0].set_value("Career & Jobs").run()
    assert not app.exception
    assert any("Career & Jobs" in x.value for x in app.header)
    get_settings.cache_clear();st.cache_resource.clear()

@pytest.mark.integration
def test_public_career_page_internet_optional():
    """Optional integration check; excluded from normal pytest runs."""
    result=CareerScraper(timeout=20).scan("https://mistral.ai/")
    assert result.career_url and result.requests > 0
