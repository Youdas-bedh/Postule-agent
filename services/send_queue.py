"""Small database-backed outreach queue; no broker and fail-closed by default."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.application_workflow import record_event
from agents.send_validator import validate_send
from config import Settings, get_settings
from database.models import Application, ApplicationStatus, Contact, CVFile, Company
from services.gmail_service import GmailService


def run_outreach(session: Session, *, settings: Settings | None = None, gmail: GmailService | None = None, sleep=time.sleep, application_ids: set[int] | None = None) -> list[dict]:
    cfg = settings or get_settings()
    eligible_statuses = (
        [ApplicationStatus.DRAFT_READY, ApplicationStatus.READY_TO_SEND, ApplicationStatus.APPROVED]
        if cfg.email_dry_run else [ApplicationStatus.APPROVED]
    )
    statement = select(Application).where(
        Application.status.in_(eligible_statuses),
        Application.send_status.is_(None) | (Application.send_status != "SKIPPED"),
    )
    if application_ids is not None:
        if not application_ids: return []
        statement = statement.where(Application.id.in_(application_ids))
    rows = list(session.scalars(statement.order_by(Application.created_at, Application.id)))
    results: list[dict] = []
    last_send_time: float | None = None
    considered_companies: set[int] = set()
    planned_today = len(list(session.scalars(select(Application.id).where(Application.sent_at.is_not(None), Application.sent_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)))))
    for app in rows:
        contact = session.get(Contact, app.contact_id) if app.contact_id else None
        cv = session.get(CVFile, app.selected_cv) if app.selected_cv else None
        if last_send_time is not None and not cfg.email_dry_run:
            wait = cfg.min_send_interval_seconds - (time.monotonic() - last_send_time)
            if wait > 0: sleep(wait)
        validation = validate_send(session, app, cfg)
        if cfg.email_dry_run and validation.can_send and planned_today >= cfg.daily_email_limit:
            validation = type(validation)(False, [*validation.blocking_reasons, "Limite quotidienne de la file atteinte."], validation.fingerprint)
        company = session.get(Company, app.company_id)
        targeted_job = bool(app.job_offer_id and app.score is not None and app.score >= cfg.direct_application_min_score)
        if not company or (not targeted_job and float(company.company_relevance_score or 0) < cfg.spontaneous_company_min_score):
            validation = type(validation)(False, [*validation.blocking_reasons, "Pertinence de l'entreprise sous le seuil de candidature spontanée."], validation.fingerprint)
        if app.company_id in considered_companies and cfg.max_emails_per_company <= 1:
            validation = type(validation)(False, [*validation.blocking_reasons, "Un seul email par entreprise et par lot."], validation.fingerprint)
        if not validation.can_send:
            if (contact is None or contact.verification_status != "VERIFIED"
                    or (contact.confidence_score or 0) < cfg.min_contact_confidence
                    or (not targeted_job and (not company or float(company.company_relevance_score or 0) < cfg.spontaneous_company_min_score))):
                if app.status != ApplicationStatus.APPROVED:
                    app.status = ApplicationStatus.SKIPPED
                app.send_status = "SKIPPED"
                record_event(session, app, "SKIPPED", "; ".join(validation.blocking_reasons)[:500])
            outcome = "SKIPPED" if app.send_status == "SKIPPED" else "BLOCKED"
            results.append(_result(app, contact, cv, validation.blocking_reasons, outcome))
            continue
        app.recipient_email, app.recipient_name = contact.email, contact.name
        app.send_fingerprint = validation.fingerprint
        app.subject_sent = app.final_subject or app.subject
        if app.status == ApplicationStatus.READY_TO_SEND and app.send_status == "WOULD_SEND":
            results.append(_result(app, contact, cv, [], "WOULD_SEND"))
            considered_companies.add(app.company_id)
            planned_today += 1
            continue
        app.status = ApplicationStatus.READY_TO_SEND
        app.send_status = "READY_TO_SEND"
        record_event(session, app, "READY_TO_SEND", "Validations automatiques réussies.")
        if cfg.email_dry_run:
            app.send_status = "WOULD_SEND"
            record_event(session, app, "WOULD_SEND", "Dry-run : aucun appel à Gmail.")
            results.append(_result(app, contact, cv, [], "WOULD_SEND"))
            considered_companies.add(app.company_id)
            continue
        if not cfg.auto_send_enabled or not cfg.gmail_enabled:
            results.append(_result(app, contact, cv, ["AUTO_SEND_ENABLED et GMAIL_ENABLED doivent être actifs pour l'envoi réel."], "READY_TO_SEND"))
            continue
        app.status = ApplicationStatus.SENDING
        app.send_status = "SENDING"
        record_event(session, app, "SEND_STARTED", "Transmission Gmail démarrée.")
        session.flush()
        try:
            response = (gmail or GmailService()).send(to=contact.email, subject=app.subject_sent or "", body=app.final_body or app.body or "", attachment_path=cv.storage_path)
            app.status = ApplicationStatus.SENT
            app.send_status = "SENT"
            app.gmail_message_id = response.get("id")
            app.gmail_thread_id = response.get("threadId")
            app.sent_at = datetime.now(timezone.utc)
            app.cv_sent = True
            app.send_error = None
            record_event(session, app, "SENT", "Gmail a accepté le message.")
            last_send_time = time.monotonic()
            considered_companies.add(app.company_id)
            planned_today += 1
            results.append(_result(app, contact, cv, [], "SENT"))
        except Exception as exc:
            app.status = ApplicationStatus.SEND_FAILED
            app.send_status = "SEND_FAILED"
            app.send_error = type(exc).__name__
            record_event(session, app, "SEND_FAILED", f"Échec Gmail ({type(exc).__name__}).")
            results.append(_result(app, contact, cv, [f"Envoi Gmail échoué ({type(exc).__name__})."], "SEND_FAILED"))
        session.flush()
    return results


def _result(app, contact, cv, reasons, status):
    return {"application_id": app.id, "company_id": app.company_id, "contact": contact.name if contact else None,
            "recipient": contact.email if contact else None, "verification": contact.verification_status if contact else None,
            "confidence": contact.confidence_score if contact else None, "job_offer_id": app.job_offer_id,
            "job_match": app.score, "selected_cv": cv.filename if cv else None,
            "subject": app.final_subject or app.subject, "body": app.final_body or app.body,
            "can_send": not reasons, "blocking_reasons": reasons, "dry_run_status": status}
