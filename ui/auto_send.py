"""Auto-send preview and Gmail connection status for the existing pipeline."""
from __future__ import annotations

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agents.send_validator import validate_send
from config import get_settings
from database.models import Application, ApplicationEvent, ApplicationStatus, Company, Contact, CVFile
from services.oauth_service import gmail_oauth_status
from services.send_queue import run_outreach


def render_auto_send(factory: sessionmaker[Session]) -> None:
    cfg = get_settings()
    st.header("Auto Send")
    st.caption("Validation automatique et aperçu. EMAIL_DRY_RUN=true n'appelle jamais Gmail.")
    with factory() as session:
        apps = list(session.scalars(select(Application).order_by(Application.created_at.desc(), Application.id.desc())))
        contacts = {x.id: x for x in session.scalars(select(Contact))}
        companies = {x.id: x.name for x in session.scalars(select(Company))}
        cvs = {x.id: x.filename for x in session.scalars(select(CVFile))}
        ready = sum(validate_send(session, a, cfg).can_send for a in apps if a.status in {ApplicationStatus.DRAFT_READY, ApplicationStatus.READY_TO_SEND, ApplicationStatus.APPROVED})
        today_count = sum(1 for a in apps if a.sent_at and a.sent_at.date().isoformat() == __import__('datetime').datetime.now(__import__('datetime').timezone.utc).date().isoformat())
        sent_total = sum(a.sent_at is not None for a in apps)
        skipped = sum(a.status == ApplicationStatus.SKIPPED or a.send_status == "SKIPPED" for a in apps)
        failed = sum(a.status == ApplicationStatus.SEND_FAILED for a in apps)
        rows = [(a, validate_send(session, a, cfg)) for a in apps]
    cols = st.columns(6)
    for col, label, value in zip(cols, ("Ready to send", "Sent today", "Sent total", "Skipped", "Failed", "Remaining daily quota"),
                                  (ready, today_count, sent_total, skipped, failed, max(0, cfg.daily_email_limit-today_count))):
        col.metric(label, value)
    if not cfg.email_dry_run and (not cfg.auto_send_enabled or not cfg.gmail_enabled):
        st.warning("L'envoi réel est verrouillé : EMAIL_DRY_RUN doit rester actif pendant cette phase, et GMAIL_ENABLED/AUTO_SEND_ENABLED sont désactivés.")
    if st.button("Run automatic outreach", type="primary", disabled=not cfg.email_dry_run and not (cfg.auto_send_enabled and cfg.gmail_enabled)):
        with factory.begin() as session:
            results = run_outreach(session, settings=cfg)
        st.session_state["auto_send_last_run"] = results
        st.rerun()
    st.subheader("Candidatures")
    for app, validation in rows:
        contact = contacts.get(app.contact_id)
        label = f"{companies.get(app.company_id, 'Entreprise inconnue')} · {contact.name if contact and contact.name else 'Contact absent'} · {app.status.value if hasattr(app.status, 'value') else app.status}"
        with st.expander(label):
            st.write({"Company": companies.get(app.company_id), "Contact": contact.name if contact else None,
                      "Email": contact.email if contact else app.recipient_email, "Verification": contact.verification_status if contact else None,
                      "Confidence": contact.confidence_score if contact else None, "Job match score": app.score,
                      "CV": cvs.get(app.selected_cv), "Status": app.send_status or (app.status.value if hasattr(app.status, 'value') else app.status)})
            st.write("**Blocking reasons:**", validation.blocking_reasons or "Aucun")
            st.write("**Subject:**", app.final_subject or app.subject or "—")
            st.write(app.final_body or app.body or "Message absent.")
    for result in st.session_state.get("auto_send_last_run", []):
        if result["dry_run_status"] == "WOULD_SEND":
            st.success(f"WOULD_SEND · {result['recipient']} · {result['subject']}")


def render_gmail(factory: sessionmaker[Session]) -> None:
    cfg = get_settings()
    st.header("Gmail")
    status = gmail_oauth_status()
    with factory() as session:
        sent = list(session.scalars(select(Application).where(Application.sent_at.is_not(None))))
    today = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).date()
    sent_today = sum(1 for app in sent if app.sent_at.date() == today)
    st.json({"configured": status["configured"], "connected": status["connected"], "account": status["account"],
             "token_valid": status["token_valid"], "emails_sent_today": sent_today, "daily_limit": cfg.daily_email_limit,
             "last_error": status["error"]})
    if st.button("Connect Gmail", disabled=not status["configured"]):
        st.info("Sur RunPod, ouvre un tunnel SSH pour le port 8765 puis exécute `python scripts/connect_gmail.py` dans le terminal du projet. Le script affiche le lien Google et enregistre le jeton sans l'afficher.")
