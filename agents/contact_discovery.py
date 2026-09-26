"""Public, source-backed contact discovery with cache and safe channel ranking."""
from __future__ import annotations
import logging, time
from datetime import datetime,timezone,timedelta
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from config import get_settings
from database.models import CandidateProfile,Company,Contact,JobOffer
from agents.contact_verifier import contact_priority,rank_contacts
from scrapers.contacts import ContactScraper,ContactScrapeResult,PublicContact
from utils.geography import is_france_job

logger=logging.getLogger(__name__)
APPLICATION_CHANNELS={"DIRECT_JOB_APPLICATION","CAREER_FORM","VERIFIED_EMAIL","RECRUITER_CONTACT","NO_RELIABLE_CONTACT"}

class ContactDiscoveryResult(BaseModel):
    company_id:int
    contacts_found:int=0
    verified_contacts:int=0
    probable_contacts:int=0
    unverified_contacts:int=0
    best_contact_id:int|None=None
    best_contact_reason:str|None=None
    preferred_channel:str="NO_RELIABLE_CONTACT"
    application_form_url:str|None=None
    status:str="empty"
    requests:int=0
    pages_scanned:int=0
    elapsed_seconds:float=0.0
    cached:bool=False

def _role_reason(contact:Contact)->str:
    if contact.source_type=="INFERRED_PATTERN":return "Adresse déduite d'un motif, non vérifiée et non éligible à une utilisation automatique."
    if contact.role=="Recruitment" or (contact.email or "").casefold().startswith(("jobs@","careers@","career@","recruitment@","recruiting@","talent@","hr@","rh@")):
        return f"Adresse de recrutement publiée par une source officielle (confiance {int(contact.confidence_score or 0)}/100)."
    return f"Meilleur contact professionnel public selon le rôle et la confiance (confiance {int(contact.confidence_score or 0)}/100)."

class ContactDiscoveryAgent:
    def __init__(self,scraper:ContactScraper|None=None,*,cache_days:int|None=None,min_confidence:int|None=None,direct_threshold:int|None=None):
        cfg=get_settings();self.scraper=scraper or ContactScraper(timeout=cfg.contact_scan_timeout,max_pages=cfg.contact_scan_max_pages)
        self.cache=timedelta(days=cache_days or cfg.contact_scan_cache_days);self.min_confidence=cfg.contact_min_confidence if min_confidence is None else min_confidence
        self.direct_threshold=cfg.direct_job_application_threshold if direct_threshold is None else direct_threshold
    def find_contacts(self,session:Session,company:Company,*,force:bool=False)->ContactDiscoveryResult:
        started=time.perf_counter();now=datetime.now(timezone.utc);last=company.contacts_last_scanned_at
        if last and last.tzinfo is None:last=last.replace(tzinfo=timezone.utc)
        if not force and last and now-last<self.cache:return self._result(session,company,cached=True)
        logger.info("[CONTACT] Scanning public contact sources for %s",company.name)
        company.contacts_scan_status="running";session.flush()
        try:scan=self.scraper.scan(session,company)
        except Exception as exc:
            logger.exception("[CONTACT] Scan failed company_id=%s",company.id)
            scan=ContactScrapeResult(status="failed",errors=[type(exc).__name__])
        existing=list(session.scalars(select(Contact).where(Contact.company_id==company.id)))
        for candidate in scan.contacts:self._upsert(session,company,candidate,existing,now)
        company.contacts_last_scanned_at=now;company.contacts_scan_status=scan.status
        if scan.application_form_url:company.application_form_url=scan.application_form_url
        current_profile=session.scalar(select(CandidateProfile).order_by(CandidateProfile.id.desc()))
        offers=list(session.scalars(select(JobOffer).where(JobOffer.company_id==company.id)))
        direct=bool(current_profile and any(is_france_job(o.country,o.location) and o.match_score is not None and o.match_score>=self.direct_threshold and o.match_profile_hash==current_profile.content_hash for o in offers))
        contacts=list(session.scalars(select(Contact).where(Contact.company_id==company.id)))
        ranked=rank_contacts(contacts)
        company.best_contact_id=ranked[0].id if ranked else None
        company.best_contact_reason=_role_reason(ranked[0]) if ranked else None
        eligible=[c for c in ranked if c.verification_status=="VERIFIED" and int(c.confidence_score or 0)>=self.min_confidence and (c.email or "")]
        if direct:channel="DIRECT_JOB_APPLICATION"
        elif eligible:channel="RECRUITER_CONTACT" if eligible[0].name or eligible[0].role in {"Recruitment","Talent Acquisition","Recruiter","HR","Engineering Manager"} else "VERIFIED_EMAIL"
        elif company.application_form_url:channel="CAREER_FORM"
        else:channel="NO_RELIABLE_CONTACT"
        company.preferred_application_channel=channel
        session.commit()
        elapsed=time.perf_counter()-started
        counts=self._counts(contacts)
        logger.info("[CONTACT] company=%s contacts=%d verified=%d probable=%d unverified=%d channel=%s",company.name,len(contacts),counts["VERIFIED"],counts["PROBABLE"],counts["UNVERIFIED"],channel)
        return ContactDiscoveryResult(company_id=company.id,contacts_found=len(contacts),verified_contacts=counts["VERIFIED"],probable_contacts=counts["PROBABLE"],unverified_contacts=counts["UNVERIFIED"],best_contact_id=company.best_contact_id,best_contact_reason=company.best_contact_reason,preferred_channel=channel,application_form_url=company.application_form_url,status=scan.status,requests=scan.requests,pages_scanned=scan.pages_scanned,elapsed_seconds=elapsed)
    def _upsert(self,session,company,candidate:PublicContact,existing:list[Contact],now):
        email=candidate.email.casefold() if candidate.email else None
        row=next((c for c in existing if email and (c.email or "").casefold()==email),None)
        if row is None and candidate.name and candidate.role:
            row=next((c for c in existing if (c.name or "").casefold()==candidate.name.casefold() and (c.role or "").casefold()==candidate.role.casefold()),None)
        if row is None:
            row=Contact(company_id=company.id,name=candidate.name,role=candidate.role,email=email,source_url=candidate.source_url,source_type=candidate.source_type,confidence_score=candidate.confidence_score,verification_status=candidate.verification_status,linkedin_url=candidate.linkedin_url,discovered_at=now,verified_at=now if candidate.verification_status=="VERIFIED" else None,email_domain_match=candidate.email_domain_match,email_mx_status=candidate.email_mx_status,email_is_disposable=candidate.email_is_disposable)
            session.add(row);existing.append(row);return
        # Retain the best source and never downgrade a previously verified row.
        if candidate.confidence_score>=int(row.confidence_score or 0):
            row.name=row.name or candidate.name;row.role=row.role or candidate.role;row.email=row.email or email
            row.source_url=candidate.source_url;row.source_type=candidate.source_type;row.confidence_score=candidate.confidence_score
            if row.verification_status!="VERIFIED" or candidate.verification_status=="VERIFIED":row.verification_status=candidate.verification_status
            row.linkedin_url=row.linkedin_url or candidate.linkedin_url;row.email_domain_match=candidate.email_domain_match if candidate.email_domain_match is not None else row.email_domain_match
            row.email_mx_status=candidate.email_mx_status or row.email_mx_status;row.email_is_disposable=candidate.email_is_disposable if candidate.email_is_disposable is not None else row.email_is_disposable
            if candidate.verification_status=="VERIFIED" and not row.verified_at:row.verified_at=now
    @staticmethod
    def _counts(contacts):
        return {s:sum(c.verification_status==s for c in contacts) for s in ("VERIFIED","PROBABLE","UNVERIFIED")}
    def _result(self,session,company,*,cached):
        contacts=list(session.scalars(select(Contact).where(Contact.company_id==company.id)));counts=self._counts(contacts)
        offers=list(session.scalars(select(JobOffer).where(JobOffer.company_id==company.id)));profile=session.scalar(select(CandidateProfile).order_by(CandidateProfile.id.desc()))
        direct=bool(profile and any(is_france_job(o.country,o.location) and o.match_score is not None and o.match_score>=self.direct_threshold and o.match_profile_hash==profile.content_hash for o in offers))
        eligible=[c for c in rank_contacts(contacts) if c.verification_status=="VERIFIED" and int(c.confidence_score or 0)>=self.min_confidence and c.email]
        channel="DIRECT_JOB_APPLICATION" if direct else "RECRUITER_CONTACT" if eligible and (eligible[0].name or eligible[0].role in {"Recruitment","Talent Acquisition","Recruiter","HR","Engineering Manager"}) else "VERIFIED_EMAIL" if eligible else "CAREER_FORM" if company.application_form_url else "NO_RELIABLE_CONTACT"
        return ContactDiscoveryResult(company_id=company.id,contacts_found=len(contacts),verified_contacts=counts["VERIFIED"],probable_contacts=counts["PROBABLE"],unverified_contacts=counts["UNVERIFIED"],best_contact_id=company.best_contact_id,best_contact_reason=company.best_contact_reason,preferred_channel=channel,application_form_url=company.application_form_url,status=company.contacts_scan_status or "cached",cached=cached)
