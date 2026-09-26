from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.policy import default

import pytest
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.send_validator import validate_send
from config import Settings
from database.models import Application, ApplicationEvent, ApplicationStatus, Base, CandidateProfile, Company, Contact, CVFile
from services.gmail_service import GmailService
from services.send_queue import run_outreach


@pytest.fixture
def send_db(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    pdf = tmp_path / "cv.pdf"
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as stream: writer.write(stream)
    profile = CandidateProfile(filename="profile.md", content="Diplômé d'un Master en Intelligence Artificielle à Sorbonne Université. Python, NLP, PyTorch.", content_hash="p"*64)
    company = Company(name="Société Test", domain="test.fr", website="https://test.fr", location="France", company_relevance_score=90)
    session.add_all([profile, company]); session.flush()
    contact = Contact(company_id=company.id, name="Marie Dupont", role="Recruiter", email="marie@test.fr", source_url="https://test.fr/contact", source_type="manual", confidence_score=95, verification_status="VERIFIED")
    cv = CVFile(profile_id=profile.id, filename="cv.pdf", storage_path=str(pdf), extracted_text="CV")
    session.add_all([contact, cv]); session.flush()
    app = Application(company_id=company.id, profile_id=profile.id, contact_id=contact.id, selected_cv=cv.id,
                      application_type="SPONTANEOUS_APPLICATION", status=ApplicationStatus.DRAFT_READY,
                      application_channel="RECRUITER_CONTACT",
                      subject="Candidature en IA", body="Bonjour, diplômé en IA, je recherche un CDI.",
                      final_subject="Candidature en IA", final_body="Bonjour, diplômé en IA, je recherche un CDI.", source_facts_json="[]")
    session.add(app); session.flush()
    session.commit()
    yield session, app, contact, cv, pdf
    session.close(); engine.dispose()


def cfg(**kw):
    return Settings(_env_file=None, email_dry_run=True, auto_send_enabled=False, gmail_enabled=False, **kw)


def test_verified_contact_and_confidence_required(send_db):
    session, app, contact, *_ = send_db
    assert validate_send(session, app, cfg()).can_send
    contact.verification_status = "PROBABLE"
    result = validate_send(session, app, cfg())
    assert not result.can_send and any("VERIFIED" in reason for reason in result.blocking_reasons)
    contact.verification_status = "VERIFIED"; contact.confidence_score = 10
    assert any("Confiance" in reason for reason in validate_send(session, app, cfg()).blocking_reasons)


def test_dry_run_queue_never_calls_gmail(send_db):
    session, app, *_ = send_db
    class NeverGmail:
        def send(self, **kwargs): raise AssertionError("Gmail must not be called in dry-run")
    results = run_outreach(session, settings=cfg(), gmail=NeverGmail())
    assert results[0]["dry_run_status"] == "WOULD_SEND"
    assert app.send_status == "WOULD_SEND"
    assert app.status == ApplicationStatus.READY_TO_SEND
    assert {e.event for e in session.scalars(__import__('sqlalchemy').select(ApplicationEvent).where(ApplicationEvent.application_id==app.id))} >= {"READY_TO_SEND", "WOULD_SEND"}


def test_mocked_queue_send_persists_success_and_failure(send_db):
    session, app, *_ = send_db
    settings = Settings(_env_file=None, email_dry_run=False, auto_send_enabled=True, gmail_enabled=True)
    class Success:
        def send(self, **kwargs): return {"id": "mock-message", "threadId": "mock-thread"}
    assert run_outreach(session, settings=settings, gmail=Success()) == []
    assert app.status == ApplicationStatus.DRAFT_READY
    app.status = ApplicationStatus.APPROVED
    result = run_outreach(session, settings=settings, gmail=Success())[0]
    assert result["dry_run_status"] == "SENT"
    assert app.status == ApplicationStatus.SENT and app.gmail_message_id == "mock-message" and app.cv_sent
    session.flush()
    assert "SENT" in {e.event for e in session.scalars(__import__('sqlalchemy').select(ApplicationEvent).where(ApplicationEvent.application_id==app.id))}

    session.rollback()
    app.status = ApplicationStatus.APPROVED
    class Broken:
        def send(self, **kwargs): raise OSError("network detail should stay private")
    result = run_outreach(session, settings=settings, gmail=Broken())[0]
    assert result["dry_run_status"] == "SEND_FAILED"
    assert app.status == ApplicationStatus.SEND_FAILED and app.send_error == "OSError"
    assert run_outreach(session, settings=settings, gmail=Broken()) == []


def test_duplicate_fingerprint_blocks(send_db):
    session, app, contact, cv, *_ = send_db
    from agents.send_validator import fingerprint_for
    app.send_fingerprint = fingerprint_for(app, contact, cv)
    second = Application(company_id=app.company_id, profile_id=app.profile_id, contact_id=app.contact_id,
                         selected_cv=app.selected_cv, job_offer_id=None, application_type=app.application_type,
                         status=ApplicationStatus.DRAFT_READY, subject="S", body="B")
    session.add(second); session.flush()
    assert any("Doublon" in reason for reason in validate_send(session, second, cfg()).blocking_reasons)


def test_interdicts_student_claims_and_invented_numbers(send_db):
    session, app, *_ = send_db
    app.final_body = "Je termine mon Master et j'ai 5 ans d'expérience."
    reasons = validate_send(session, app, cfg()).blocking_reasons
    assert any("formulation interdite" in reason for reason in reasons)
    assert any("chiffres absents" in reason for reason in reasons)


def test_daily_limit_and_interval_block(send_db):
    session, app, *_ = send_db
    previous = Application(company_id=app.company_id, profile_id=app.profile_id, application_type="SPONTANEOUS_APPLICATION",
                           status=ApplicationStatus.SENT, sent_at=datetime.now(timezone.utc))
    session.add(previous); session.flush()
    reasons = validate_send(session, app, cfg(daily_email_limit=1), now=datetime.now(timezone.utc)).blocking_reasons
    assert any("quotidienne" in reason for reason in reasons)
    reasons = validate_send(session, app, cfg(min_send_interval_seconds=3600), now=datetime.now(timezone.utc)).blocking_reasons
    assert any("Intervalle" in reason for reason in reasons)


def test_gmail_mime_has_pdf_attachment(send_db):
    _, _, _, _, pdf = send_db
    payload = GmailService.build_message("to@example.com", "Subject", "Body", pdf)
    raw = base64.urlsafe_b64decode(payload["raw"] + "==")
    message = message_from_bytes(raw, policy=default)
    assert message["To"] == "to@example.com"
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_content_type() == "application/pdf"


def test_mocked_gmail_send_and_error(send_db):
    _, _, _, _, pdf = send_db
    class Execute:
        def execute(self): return {"id": "msg-1", "threadId": "thread-1"}
    class Messages:
        def send(self, **kwargs): self.kwargs = kwargs; return Execute()
    class Users:
        def __init__(self): self.messages_obj = Messages()
        def messages(self): return self.messages_obj
    class API:
        def __init__(self): self.users_obj = Users()
        def users(self): return self.users_obj
    api = API()
    assert GmailService(service=api).send(to="a@b.fr", subject="Hi", body="Hello", attachment_path=pdf)["id"] == "msg-1"
    class Broken:
        def users(self): raise OSError("network")
    from services.gmail_service import GmailSendError
    with pytest.raises(GmailSendError): GmailService(service=Broken()).send(to="a@b.fr", subject="Hi", body="Hello", attachment_path=pdf)


def test_candidate_status_graduated_email_fallback(send_db):
    from agents.email_generator import EmailGenerator
    from database.models import JobOffer
    session, app, contact, *_ = send_db
    profile = session.get(CandidateProfile, app.profile_id)
    company = session.get(Company, app.company_id)
    generated, _, mode, _ = EmailGenerator().generate(profile, company, None, contact, "VERIFIED_EMAIL")
    assert mode == "template_no_llm"
    assert "Diplômé d'un Master en Intelligence Artificielle à Sorbonne Université" in generated.body
    assert "étudiant" not in generated.body.casefold()
    assert 70 <= len(generated.body.split()) <= 130


def test_invalid_llm_response_falls_back_to_natural_template(send_db):
    from agents.email_generator import EmailGenerator
    session, app, contact, *_ = send_db
    profile, company = session.get(CandidateProfile, app.profile_id), session.get(Company, app.company_id)
    class InvalidProvider:
        def generate(self, *args, **kwargs): return "not JSON"
    generated, _, mode, _ = EmailGenerator(InvalidProvider()).generate(profile, company, None, contact, "RECRUITER_CONTACT")
    assert mode == "template_invalid_or_unavailable_llm"
    assert generated.body and "Diplômé" in generated.body


def test_oauth_missing_credentials_is_safe(monkeypatch, tmp_path):
    import services.oauth_service as oauth
    safe = Settings(_env_file=None, gmail_credentials_path=str(tmp_path / "missing.json"), gmail_token_path=str(tmp_path / "token.json"))
    monkeypatch.setattr(oauth, "get_settings", lambda: safe)
    status = oauth.gmail_oauth_status()
    assert status["configured"] is False and status["connected"] is False
    assert "secret" not in str(status).casefold() and "token.json" not in str(status)


def test_expired_oauth_token_refresh_is_persisted_without_logging(monkeypatch, tmp_path):
    import google.oauth2.credentials
    import services.oauth_service as oauth
    credentials_file, token_file = tmp_path / "credentials.json", tmp_path / "token.json"
    credentials_file.write_text("{}", encoding="utf-8"); token_file.write_text("{}", encoding="utf-8")
    safe = Settings(_env_file=None, gmail_credentials_path=str(credentials_file), gmail_token_path=str(token_file))
    monkeypatch.setattr(oauth, "get_settings", lambda: safe)
    class Credentials:
        expired=True; refresh_token="private-refresh-token"; valid=False
        granted_scopes=["https://www.googleapis.com/auth/gmail.send"]
        @classmethod
        def from_authorized_user_file(cls, *args, **kwargs): return cls()
        def refresh(self, request): self.valid=True; self.expired=False
        def to_json(self): return '{"access_token":"private-access-token"}'
    monkeypatch.setattr(google.oauth2.credentials, "Credentials", Credentials)
    creds=oauth.gmail_credentials()
    assert creds.valid and token_file.read_text(encoding="utf-8").endswith("}")
    assert token_file.stat().st_mode & 0o777 == 0o600


def test_oauth_scope_alias_is_accepted_only_when_gmail_send_is_granted(monkeypatch, tmp_path):
    import types
    import google_auth_oauthlib.flow
    import services.oauth_service as oauth
    credentials_file, token_file = tmp_path / "credentials.json", tmp_path / "token.json"
    credentials_file.write_text("{}", encoding="utf-8")
    safe = Settings(_env_file=None, gmail_credentials_path=str(credentials_file), gmail_token_path=str(token_file))
    monkeypatch.setattr(oauth, "get_settings", lambda: safe)
    class Cred:
        valid=True
        def __init__(self, granted_scopes=None): self.granted_scopes=granted_scopes
        def to_json(self): return '{"access_token":"private"}'
    class Flow:
        def __init__(self, scope): self.oauth2session=types.SimpleNamespace(token={"scope":scope})
        def run_local_server(self, **kwargs): self.kwargs=kwargs; return Cred(self.oauth2session.token.get("scope"))
    selected = {"flow":Flow("email openid https://www.googleapis.com/auth/gmail.send")}
    class FakeFlow:
        @staticmethod
        def from_client_secrets_file(*args, **kwargs): return selected["flow"]
    monkeypatch.setattr(google_auth_oauthlib.flow, "InstalledAppFlow", FakeFlow)
    credentials=oauth.gmail_credentials(allow_interactive=True)
    assert credentials.valid and token_file.is_file()
    assert selected["flow"].kwargs["host"] == "127.0.0.1"
    assert selected["flow"].kwargs["port"] == 8765
    assert selected["flow"].kwargs["open_browser"] is False
    assert "URL Google" in selected["flow"].kwargs["authorization_prompt_message"]

    token_file.unlink()
    selected["flow"]=Flow("email openid")
    with pytest.raises(oauth.OAuthConfigurationError, match="n'a pas accordé"):
        oauth.gmail_credentials(allow_interactive=True)
    assert not token_file.exists()


def test_scope_report_uses_google_auth_checker_only_when_grants_are_unavailable():
    from services.oauth_service import GMAIL_SEND_SCOPE, _scope_report

    class Credentials:
        granted_scopes = None
        scopes = None
        def has_scopes(self, requested): return requested == [GMAIL_SEND_SCOPE]

    assert _scope_report(Credentials()) == (True, [])

    class IncompleteCredentials(Credentials):
        granted_scopes = ["openid"]
        def has_scopes(self, requested): return True

    assert _scope_report(IncompleteCredentials()) == (False, ["openid"])

    class EmptyGrantCredentials:
        granted_scopes = []
        scopes = None

    assert _scope_report(
        EmptyGrantCredentials(),
        "openid https://www.googleapis.com/auth/gmail.send",
    ) == (True, ["gmail.send", "openid"])


def test_streamlit_auto_send_and_gmail_pages_render(monkeypatch, tmp_path):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from config import ROOT, get_settings
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    get_settings.cache_clear(); st.cache_resource.clear()
    for name in ("Auto Send", "Gmail"):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        app.sidebar.radio[0].set_value(name).run()
        assert not app.exception
    get_settings.cache_clear(); st.cache_resource.clear()
