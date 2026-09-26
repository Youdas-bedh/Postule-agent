"""Explicitly triggered, France-only discovery-to-email campaign runner."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from agents.application_strategy import ApplicationStrategyAgent
from agents.application_workflow import record_event
from agents.career_agent import CareerAgent
from agents.company_discovery import CompanyDiscoveryAgent
from agents.contact_discovery import ContactDiscoveryAgent
from agents.job_matcher import JobMatcher
from agents.send_validator import validate_send
from config import Settings
from database.models import Application, ApplicationStatus, CandidateProfile, Company, Contact, CVFile, JobOffer
from discovery.llm_company_source import LLMCompanySource
from llm.base import LLMProvider
from services.send_queue import run_outreach
from utils.geography import is_france_job

logger = logging.getLogger(__name__)


@dataclass
class CampaignItem:
    company: str
    website: str | None = None
    job: str | None = None
    job_url: str | None = None
    score: int | None = None
    contact: str | None = None
    email: str | None = None
    cv: str | None = None
    subject: str | None = None
    body: str | None = None
    status: str = "IGNORED"
    reason: str | None = None


@dataclass
class CampaignResult:
    companies_found: int = 0
    jobs_scanned: int = 0
    contacts_scanned: int = 0
    items: list[CampaignItem] = field(default_factory=list)


def run_campaign(factory: sessionmaker, *, profile_id: int, query: str, max_companies: int,
                 provider: LLMProvider, settings: Settings,
                 company_source: LLMCompanySource | None = None,
                 career_agent: CareerAgent | None = None,
                 contact_agent: ContactDiscoveryAgent | None = None,
                 matcher: JobMatcher | None = None,
                 strategy: ApplicationStrategyAgent | None = None) -> CampaignResult:
    """Find verified French companies, scan/match public jobs, draft and safely queue emails."""
    result = CampaignResult()
    gmail_ready = True
    if not settings.email_dry_run and settings.auto_send_enabled and settings.gmail_enabled:
        try:
            from services.oauth_service import gmail_oauth_status
            gmail_ready = bool(gmail_oauth_status().get("connected"))
        except Exception:
            gmail_ready = False
    source = company_source or LLMCompanySource(provider)
    candidates = source.search(query, max_companies)
    career_agent = career_agent or CareerAgent()
    contact_agent = contact_agent or ContactDiscoveryAgent()
    matcher = matcher or JobMatcher(provider)
    strategy = strategy or ApplicationStrategyAgent(prefer_email=True)

    with factory() as session:
        profile = session.get(CandidateProfile, profile_id)
        if profile is None:
            raise ValueError("Aucun profil actif sélectionné.")
        companies = CompanyDiscoveryAgent(sources=[]).discover(
            session, job_titles=[query], locations=["France"], company_types=[], sectors=[query[:255]],
            max_companies=max_companies, manual_candidates=candidates,
        )
        result.companies_found = len(companies)
        company_ids = [c.id for c in companies]

    for company_id in company_ids:
        try:
            with factory() as session:
                company = session.get(Company, company_id)
                if not company:
                    continue
                company_name = company.name
                career_agent.scan_company(session, company)
            with factory() as session:
                company = session.get(Company, company_id)
                if company:
                    contact_agent.find_contacts(session, company)
            with factory() as session:
                profile = session.get(CandidateProfile, profile_id)
                company = session.get(Company, company_id)
                if not profile or not company:
                    continue
                jobs = list(session.scalars(select(JobOffer).where(JobOffer.company_id == company_id)))
                result.jobs_scanned += len(jobs)
                contacts = list(session.scalars(select(Contact).where(Contact.company_id == company_id)))
                result.contacts_scanned += len(contacts)
                eligible_contacts = [c for c in contacts if c.verification_status == "VERIFIED"
                                     and c.email and (c.confidence_score or 0) >= settings.min_contact_confidence]
                if not eligible_contacts:
                    result.items.append(CampaignItem(company=company_name, website=company.website_url or company.website,
                        reason="Aucun contact email public vérifié au seuil de confiance."))
                    continue
                french_jobs = [j for j in jobs if is_france_job(j.country, j.location)]
                for job in french_jobs:
                    matcher.match(session, profile, job, company_name=company_name)
                session.commit()
                eligible_jobs = sorted((j for j in french_jobs if j.match_score is not None
                                        and j.match_score >= settings.direct_application_min_score
                                        and j.match_profile_hash == profile.content_hash),
                                       key=lambda j: j.match_score or 0, reverse=True)
                if not eligible_jobs:
                    result.items.append(CampaignItem(company=company_name, website=company.website_url or company.website,
                        reason=f"Aucune offre française au score minimum de {settings.direct_application_min_score}/100."))
                    continue
                job = eligible_jobs[0]
                plan = strategy.plan(session, company, profile)
                if plan.application_channel not in {"RECRUITER_CONTACT", "VERIFIED_EMAIL"} or plan.target_job_id != job.id:
                    # Prefer the best verified contact, and force generation against the chosen matching offer.
                    contact = sorted(eligible_contacts, key=lambda c: (c.confidence_score or 0), reverse=True)[0]
                    app = _get_or_create_draft(session, company, profile, job, contact, provider, strategy)
                else:
                    generated = strategy.generate(session, company, profile, provider=provider)
                    app = session.get(Application, generated.application_id) if generated.application_id else None
                if app is None:
                    result.items.append(CampaignItem(company=company_name, job=job.title, job_url=job.url,
                        score=int(job.match_score), reason="Impossible de préparer un brouillon email."))
                    continue
                contact = session.get(Contact, app.contact_id) if app.contact_id else None
                cv = session.get(CVFile, app.selected_cv) if app.selected_cv else None
                item = CampaignItem(company=company_name, website=company.website_url or company.website,
                    job=job.title, job_url=job.url, score=int(job.match_score or 0),
                    contact=contact.name if contact else None, email=contact.email if contact else None,
                    cv=cv.filename if cv else None, subject=app.subject, body=app.body)
                if settings.email_dry_run:
                    queued = run_outreach(session, settings=settings, application_ids={app.id})
                    item.status = queued[0]["dry_run_status"] if queued else "BLOCKED"
                    item.reason = "; ".join(queued[0]["blocking_reasons"]) or "Aucun email envoyé (aperçu dry-run)." if queued else "Aucune action de file."
                elif settings.auto_send_enabled and settings.gmail_enabled:
                    if not gmail_ready:
                        item.status, item.reason = "BLOCKED", "Compte Gmail déconnecté ; autorise Gmail avant l'envoi réel."
                        result.items.append(item)
                        continue
                    if app.status == ApplicationStatus.READY_TO_SEND and app.send_status == "WOULD_SEND":
                        # A prior preview is not approval; the explicit campaign launch under real-send
                        # settings revalidates it from draft state before the configured auto-approval.
                        app.status = ApplicationStatus.DRAFT_READY
                        app.send_status = None
                    # Validate all non-status rules in dry-run mode before applying the configured automatic approval.
                    validation_cfg = settings.model_copy(update={"email_dry_run": True})
                    validation = validate_send(session, app, validation_cfg)
                    if not validation.can_send:
                        item.status, item.reason = "BLOCKED", "; ".join(validation.blocking_reasons)
                    elif app.status == ApplicationStatus.DRAFT_READY:
                        app.final_subject, app.final_body = app.subject, app.body
                        app.status = ApplicationStatus.APPROVED
                        app.approved_at = datetime.now(timezone.utc)
                        record_event(session, app, "APPROVED", "Approbation automatique explicite via Campagne après contrôles de sécurité.")
                        session.flush()
                    if item.status != "BLOCKED":
                        queued = run_outreach(session, settings=settings, application_ids={app.id})
                        item.status = queued[0]["dry_run_status"] if queued else "BLOCKED"
                        item.reason = "; ".join(queued[0]["blocking_reasons"]) if queued and queued[0]["blocking_reasons"] else None
                else:
                    item.status, item.reason = "DRAFT_READY", "Envoi réel désactivé dans .env (AUTO_SEND_ENABLED/GMAIL_ENABLED/EMAIL_DRY_RUN)."
                session.commit()
                result.items.append(item)
        except Exception as exc:
            logger.warning("[CAMPAIGN] company_id=%s failed (%s)", company_id, type(exc).__name__)
            result.items.append(CampaignItem(company=locals().get("company_name", f"Entreprise #{company_id}"),
                reason=f"Étape interrompue ({type(exc).__name__})."))
    return result


def _get_or_create_draft(session, company, profile, job, contact, provider, strategy):
    """Generate through the same guarded strategy by temporarily choosing the matching contact route."""
    # The strategy uses the company's best eligible job; if it differs from the selected one, fail closed.
    generated = strategy.generate(session, company, profile, provider=provider)
    if generated.application_id:
        app = session.get(Application, generated.application_id)
        if app and app.job_offer_id == job.id and app.contact_id:
            return app
    return None
