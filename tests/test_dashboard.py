from datetime import datetime, timezone
from io import BytesIO, StringIO
import csv

from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from database.database import init_db
from database.models import (Application, ApplicationEvent, ApplicationStatus, CandidateProfile,
                             Company, Contact, JobOffer)
from services.dashboard_service import (APPLICATION_FIELDS, csv_bytes, dashboard_data,
    filter_dashboard_rows, xlsx_bytes)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return Session(engine)


def seeded_session():
    session = make_session()
    company = Company(name="Mistral AI", domain="mistral.ai", website="https://mistral.ai",
        website_url="https://mistral.ai", sector="AI", location="Paris", career_url="https://jobs.example",
        preferred_application_channel="CAREER_FORM", analysis_json='{}', analysis_updated_at=datetime.now(timezone.utc),
        company_relevance_score=92)
    profile = CandidateProfile(filename="profile.md", content="", content_hash="hash-profile")
    session.add_all([company, profile]); session.flush()
    job = JobOffer(company_id=company.id, title="AI Engineer", url="https://jobs.example/ai", location="Paris",
        match_score=88, matching_json='{}', match_profile_hash="p", match_offer_hash="j",
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    contact = Contact(company_id=company.id, name="A Contact", role="Recruiter", email="person@example.org",
        source_url="https://mistral.ai/contact", source_type="official", confidence_score=95,
        verification_status="VERIFIED")
    session.add_all([job, contact]); session.flush()
    application = Application(company_id=company.id, profile_id=profile.id, job_offer_id=job.id,
        contact_id=contact.id, application_type="DIRECT", status=ApplicationStatus.DRAFT_READY,
        application_channel="CAREER_FORM", body="SECRET_APPLICATION_BODY", created_at=datetime.now(timezone.utc))
    session.add(application); session.flush()
    session.add(ApplicationEvent(application_id=application.id, event="GENERATED", details="ok"))
    session.commit()
    return session


def test_dashboard_aggregates_and_main_rows():
    session = seeded_session()
    data = dashboard_data(session)
    assert data["counts"]["companies_discovered"] == 1
    assert data["counts"]["companies_analyzed"] == 1
    assert data["counts"]["career_pages_found"] == 1
    assert data["counts"]["offers_extracted"] == 1
    assert data["counts"]["offers_with_matching"] == 1
    assert data["counts"]["contacts_verified"] == 1
    assert data["counts"]["applications_draft_ready"] == 1
    assert data["dashboard"][0]["job_title"] == "AI Engineer"
    assert data["events"][0]["event"] == "GENERATED"


def test_dashboard_filters_company_score_status_location_channel_verification_and_date():
    row = {"company":"Mistral AI", "job_match_score":88, "application_status":"DRAFT_READY",
        "location":"Paris", "application_channel":"CAREER_FORM", "contact_verification":"VERIFIED",
        "application_date":datetime(2026, 1, 2)}
    assert filter_dashboard_rows([row], companies=["Mistral AI"], min_score=80,
        statuses=["DRAFT_READY"], location="paris", channels=["CAREER_FORM"],
        contact_verifications=["VERIFIED"], start_date=datetime(2026,1,1).date()) == [row]
    assert not filter_dashboard_rows([row], min_score=90)
    assert not filter_dashboard_rows([row], statuses=["APPROVED"])
    assert not filter_dashboard_rows([row], location="Lyon")


def test_csv_exports_are_allowlisted_and_valid_for_empty_data():
    session = seeded_session(); data = dashboard_data(session)
    content = csv_bytes(data["applications"], "applications").decode("utf-8-sig")
    reader = csv.DictReader(StringIO(content))
    assert next(reader)["company"] == "Mistral AI"
    assert "SECRET_APPLICATION_BODY" not in content
    assert "api_key" not in content.casefold()
    empty = csv_bytes([], "contacts").decode("utf-8-sig")
    assert next(csv.reader(StringIO(empty))) == ["company", "contact_name", "contact_role", "contact_email", "contact_confidence", "verification_status", "source_type", "source_url", "discovered_at", "verified_at"]
    assert "SECRET_APPLICATION_BODY" not in csv_bytes(data["companies"], "companies").decode("utf-8-sig")


def test_xlsx_sheets_headers_content_and_empty_state(tmp_path):
    session = seeded_session(); data = dashboard_data(session)
    path = tmp_path / "phase9.xlsx"
    path.write_bytes(xlsx_bytes(data))
    workbook = load_workbook(path, read_only=True, data_only=True)
    assert workbook.sheetnames == ["Companies", "Jobs", "Contacts", "Applications", "Summary"]
    sheet = workbook["Applications"]
    assert list(next(sheet.iter_rows(values_only=True))) == APPLICATION_FIELDS
    assert list(sheet.iter_rows(min_row=2, values_only=True))[0][0] == "Mistral AI"
    assert "SECRET_APPLICATION_BODY" not in str(path.read_bytes())
    empty = load_workbook(BytesIO(xlsx_bytes({"counts": {}, "companies": [], "job_offers": [], "contacts": [], "applications": []})), read_only=True)
    assert empty.sheetnames == workbook.sheetnames
    assert list(next(empty["Applications"].iter_rows(values_only=True))) == APPLICATION_FIELDS


def test_streamlit_dashboard_renders_with_empty_database(monkeypatch, tmp_path):
    import streamlit as st
    from config import get_settings
    database = tmp_path / "dashboard.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear(); st.cache_resource.clear()
    at = AppTest.from_file(__file__.replace("tests/test_dashboard.py", "app.py"), default_timeout=15).run()
    at.radio[0].set_value("Dashboard").run()
    assert not at.exception
    assert "Dashboard" in list(at.radio[0].options)
    assert any(m.label == "Entreprises découvertes" and str(m.value) == "0" for m in at.metric)
    get_settings.cache_clear(); st.cache_resource.clear()
