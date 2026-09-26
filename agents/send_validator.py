"""Fail-closed checks for automatic outreach; never performs external actions."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import Application, ApplicationStatus, CandidateProfile, Company, Contact, CVFile, JobOffer


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@dataclass(frozen=True)
class SendValidation:
    can_send: bool
    blocking_reasons: list[str]
    fingerprint: str | None = None


def fingerprint_for(app: Application, contact: Contact, cv: CVFile) -> str:
    raw = "|".join((str(app.company_id), (contact.email or "").casefold(), str(app.job_offer_id or ""), str(cv.id), app.application_type))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_send(session: Session, app: Application, settings: Settings | None = None, *, now: datetime | None = None) -> SendValidation:
    cfg = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if not cfg.auto_send_enabled and not cfg.email_dry_run:
        reasons.append("AUTO_SEND_ENABLED est désactivé.")
    company = session.get(Company, app.company_id)
    profile = session.get(CandidateProfile, app.profile_id)
    contact = session.get(Contact, app.contact_id) if app.contact_id else None
    cv = session.get(CVFile, app.selected_cv) if app.selected_cv else None
    if not company: reasons.append("Entreprise absente.")
    if not profile or not profile.content.strip(): reasons.append("Profil candidat actif absent.")
    if not contact or contact.verification_status != "VERIFIED":
        reasons.append("Le contact n'est pas VERIFIED.")
    if not contact or (contact.confidence_score or 0) < cfg.min_contact_confidence:
        reasons.append(f"Confiance contact inférieure à {cfg.min_contact_confidence}.")
    if not contact or not contact.email or not EMAIL_RE.fullmatch(contact.email.strip()): reasons.append("Adresse email absente ou invalide.")
    if app.application_channel not in {"VERIFIED_EMAIL", "RECRUITER_CONTACT"}:
        reasons.append("Le canal de candidature n'est pas un email contact vérifié.")
    if not cv: reasons.append("CV sélectionné absent.")
    elif not _valid_cv(cv): reasons.append("CV illisible, non-PDF ou trop volumineux (limite 15 Mo).")
    subject = app.final_subject or app.subject
    body = app.final_body or app.body
    if not subject or not subject.strip(): reasons.append("Sujet absent.")
    if not body or not body.strip(): reasons.append("Message absent.")
    if app.status == ApplicationStatus.SENT or app.sent_at: reasons.append("Candidature déjà envoyée.")
    if app.status not in {ApplicationStatus.DRAFT_READY, ApplicationStatus.READY_TO_SEND, ApplicationStatus.APPROVED}:
        reasons.append(f"Statut non éligible : {getattr(app.status, 'value', app.status)}.")
    if not cfg.email_dry_run and app.status != ApplicationStatus.APPROVED:
        reasons.append("Approbation humaine requise avant tout envoi réel.")
    fp = fingerprint_for(app, contact, cv) if contact and cv else None
    if fp:
        duplicate = session.scalar(select(Application.id).where(Application.send_fingerprint == fp, Application.id != app.id))
        sent_duplicate = session.scalar(select(Application.id).where(Application.id != app.id, Application.company_id == app.company_id, Application.contact_id == app.contact_id, Application.job_offer_id.is_(app.job_offer_id) if app.job_offer_id is None else Application.job_offer_id == app.job_offer_id, Application.sent_at.is_not(None)))
        if duplicate or sent_duplicate: reasons.append("Doublon d'envoi détecté.")
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = list(session.scalars(select(Application).where(Application.sent_at >= day_start)))
    if len(sent_today) >= cfg.daily_email_limit: reasons.append("Limite quotidienne d'envoi atteinte.")
    last_sent = session.scalar(select(Application.sent_at).where(Application.sent_at.is_not(None)).order_by(Application.sent_at.desc()).limit(1))
    if last_sent:
        last_sent = last_sent.replace(tzinfo=timezone.utc) if last_sent.tzinfo is None else last_sent
        if now - last_sent < timedelta(seconds=cfg.min_send_interval_seconds): reasons.append("Intervalle minimal entre envois non écoulé.")
    sent_company = len(list(session.scalars(select(Application.id).where(Application.company_id == app.company_id, Application.sent_at.is_not(None)))))
    if sent_company >= cfg.max_emails_per_company: reasons.append("Limite d'emails par entreprise atteinte.")
    facts = (app.source_facts_json or "").casefold()
    forbidden_phrases = (r"\bétudiant(?:e)?\b", r"\ben fin de (?:mon )?master\b", r"\bbientôt diplômé", r"\bje termine mon master\b")
    if any(re.search(pattern, body or "", re.IGNORECASE) for pattern in forbidden_phrases):
        reasons.append("Le message contient une formulation interdite sur le statut étudiant/diplôme.")
    # Reject technical skills named in the email if they do not occur in the candidate source.
    if profile and body:
        from agents.email_generator import EmailGenerator
        if not set(EmailGenerator._skills(body)) <= set(EmailGenerator._skills(profile.content)):
            reasons.append("Le message mentionne une compétence absente du profil.")
        job = session.get(JobOffer, app.job_offer_id) if app.job_offer_id else None
        allowed_numbers = profile.content + " " + ((job.title or "") + " " + (job.description or "") if job else "")
        invented_numbers = {n for n in re.findall(r"\b\d+(?:[.,]\d+)?\b", body) if n not in allowed_numbers}
        if invented_numbers:
            reasons.append("Le message contient des chiffres absents du profil et de l'offre.")
    if app.source_facts_json and not facts:
        reasons.append("Faits source invalides.")
    return SendValidation(not reasons, reasons, fp)


def _valid_cv(cv: CVFile) -> bool:
    path = Path(cv.storage_path)
    if path.suffix.lower() != ".pdf": return False
    try:
        if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > 15_000_000: return False
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages) > 0
    except Exception:
        return False
