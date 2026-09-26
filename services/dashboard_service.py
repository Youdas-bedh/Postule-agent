"""Read-only dashboard queries and allow-listed data exports."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Application, ApplicationEvent, ApplicationStatus, CandidateProfile,
    Company, Contact, CVFile, JobOffer,
)


CSV_FIELDS = {
    "companies": ["company", "sector", "location", "company_relevance_score", "website", "career_url", "application_channel", "discovered_at", "analyzed_at"],
    "job_offers": ["company", "job_title", "job_url", "location", "job_match_score", "is_matched", "employment_type", "remote_policy", "discovered_at", "updated_at"],
    "contacts": ["company", "contact_name", "contact_role", "contact_email", "contact_confidence", "verification_status", "source_type", "source_url", "discovered_at", "verified_at"],
    "applications": ["company", "job_title", "job_url", "job_match_score", "contact_name", "contact_role", "contact_email", "contact_confidence", "application_channel", "selected_cv", "cv_match_score", "status", "created_at", "approved_at", "updated_at"],
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _safe_cell(value: Any) -> Any:
    value = _iso(value)
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    companies = list(session.scalars(select(Company).order_by(Company.name)))
    jobs = list(session.scalars(select(JobOffer).order_by(JobOffer.discovered_at.desc().nullslast(), JobOffer.id.desc())))
    contacts = list(session.scalars(select(Contact).order_by(Contact.confidence_score.desc(), Contact.id)))
    apps = list(session.scalars(select(Application).order_by(Application.created_at.desc(), Application.id.desc())))
    cvs = {cv.id: cv.filename for cv in session.scalars(select(CVFile))}
    company_by_id = {c.id: c for c in companies}
    contact_by_id = {c.id: c for c in contacts}
    job_by_id = {j.id: j for j in jobs}
    contact_by_company: dict[int, list[Contact]] = {}
    for contact in contacts:
        contact_by_company.setdefault(contact.company_id, []).append(contact)

    company_rows = [{
        "company": c.name, "sector": c.sector, "location": c.location,
        "company_relevance_score": c.company_relevance_score, "website": c.website_url or c.website,
        "career_url": c.career_url, "application_channel": c.preferred_application_channel,
        "discovered_at": _iso(c.discovered_at), "analyzed_at": _iso(c.analysis_updated_at),
    } for c in companies]
    job_rows = [{
        "company": company_by_id[j.company_id].name if j.company_id in company_by_id else None,
        "job_title": j.title, "job_url": j.url, "location": j.location,
        "job_match_score": j.match_score,
        "is_matched": bool(j.matching_json and j.match_profile_hash and j.match_offer_hash),
        "employment_type": j.employment_type, "remote_policy": j.remote_policy,
        "discovered_at": _iso(j.discovered_at), "updated_at": _iso(j.updated_at),
    } for j in jobs]
    contact_rows = [{
        "company": company_by_id[c.company_id].name if c.company_id in company_by_id else None,
        "contact_name": c.name, "contact_role": c.role, "contact_email": c.email,
        "contact_confidence": c.confidence_score, "verification_status": c.verification_status,
        "source_type": c.source_type, "source_url": c.source_url,
        "discovered_at": _iso(c.discovered_at), "verified_at": _iso(c.verified_at),
    } for c in contacts]
    application_rows = []
    dashboard_rows = []
    apps_by_job: dict[int, list[Application]] = {}
    apps_without_job: dict[int, list[Application]] = {}
    for app in apps:
        company = company_by_id.get(app.company_id)
        job = job_by_id.get(app.job_offer_id)
        contact = contact_by_id.get(app.contact_id) if app.contact_id else None
        if contact is None and company:
            preferred = next((x for x in contact_by_company.get(company.id, []) if x.id == company.best_contact_id), None)
            contact = preferred or next(iter(contact_by_company.get(company.id, [])), None)
        row = {
            "company": company.name if company else None, "job_title": job.title if job else None,
            "job_url": job.url if job else None, "job_match_score": job.match_score if job else None,
            "contact_name": contact.name if contact else None, "contact_role": contact.role if contact else None,
            "contact_email": contact.email if contact else None, "contact_confidence": contact.confidence_score if contact else None,
            "application_channel": app.application_channel or (company.preferred_application_channel if company else None),
            "selected_cv": cvs.get(app.selected_cv), "cv_match_score": app.cv_match_score,
            "status": _status(app.status), "created_at": _iso(app.created_at),
            "approved_at": _iso(app.approved_at), "updated_at": _iso(app.edited_at or app.generated_at or app.created_at),
        }
        application_rows.append(row)
        if app.job_offer_id:
            apps_by_job.setdefault(app.job_offer_id, []).append(app)
        else:
            apps_without_job.setdefault(app.company_id, []).append(app)

    for j in jobs:
        company = company_by_id.get(j.company_id)
        matching_apps = apps_by_job.get(j.id, [])
        relevant_contacts = contact_by_company.get(j.company_id, [])
        contact = next((x for x in relevant_contacts if company and x.id == company.best_contact_id), None)
        contact = contact or next(iter(relevant_contacts), None)
        targets = matching_apps or [None]
        for app in targets:
            app_contact = contact_by_id.get(app.contact_id) if app and app.contact_id else contact
            dashboard_rows.append({
                "company": company.name if company else None, "sector": company.sector if company else None,
                "location": j.location or (company.location if company else None),
                "company_relevance_score": company.company_relevance_score if company else None,
                "job_title": j.title, "job_url": j.url, "job_match_score": j.match_score,
                "contact": app_contact.name if app_contact else None,
                "contact_confidence": app_contact.confidence_score if app_contact else None,
                "contact_verification": app_contact.verification_status if app_contact else None,
                "application_channel": (app.application_channel if app else None) or (company.preferred_application_channel if company else None),
                "selected_cv": cvs.get(app.selected_cv) if app else None,
                "application_status": _status(app.status) if app else None,
                "application_date": app.created_at if app else None,
                "last_updated": app.edited_at or app.generated_at or app.created_at if app else j.updated_at or j.discovered_at,
            })
    for company in companies:
        if any(j.company_id == company.id for j in jobs):
            continue
        for app in apps_without_job.get(company.id, []) or [None]:
            contact = contact_by_id.get(app.contact_id) if app and app.contact_id else None
            contact = contact or next(iter(contact_by_company.get(company.id, [])), None)
            dashboard_rows.append({
                "company": company.name, "sector": company.sector, "location": company.location,
                "company_relevance_score": company.company_relevance_score, "job_title": None, "job_url": None,
                "job_match_score": None, "contact": contact.name if contact else None,
                "contact_confidence": contact.confidence_score if contact else None,
                "contact_verification": contact.verification_status if contact else None,
                "application_channel": (app.application_channel if app else None) or company.preferred_application_channel,
                "selected_cv": cvs.get(app.selected_cv) if app else None,
                "application_status": _status(app.status) if app else None,
                "application_date": app.created_at if app else None,
                "last_updated": (app.edited_at or app.generated_at or app.created_at) if app else company.analysis_updated_at or company.discovered_at,
            })
    return {"companies": company_rows, "job_offers": job_rows, "contacts": contact_rows,
            "applications": application_rows, "dashboard": dashboard_rows}


def dashboard_data(session: Session) -> dict[str, Any]:
    data = _snapshot(session)
    apps = list(session.scalars(select(Application)))
    companies, jobs = data["companies"], data["job_offers"]
    counts = {"companies_discovered": len(companies),
        "companies_analyzed": sum(bool(c["analyzed_at"]) for c in companies),
        "career_pages_found": sum(bool(c["career_url"]) for c in companies),
        "offers_extracted": len(jobs),
        "offers_with_matching": sum(bool(j["is_matched"]) for j in jobs),
        "contacts_found": len(data["contacts"]),
        "contacts_verified": sum(c["verification_status"] == "VERIFIED" for c in data["contacts"])}
    for status in ("DRAFT_READY", "APPROVED", "REJECTED", "IGNORED"):
        counts[f"applications_{status.lower()}"] = sum(a["status"] == status for a in data["applications"])
    today = datetime.now(timezone.utc).date()
    counts.update({
        "ready_to_send": sum(_status(a.status) == "READY_TO_SEND" for a in apps),
        "sent_today": sum(bool(a.sent_at and a.sent_at.date() == today) for a in apps),
        "sent_total": sum(bool(a.sent_at) for a in apps),
        "send_failed": sum(_status(a.status) == "SEND_FAILED" for a in apps),
        "send_skipped": sum(_status(a.status) == "SKIPPED" or a.send_status == "SKIPPED" for a in apps),
    })
    from config import get_settings
    counts["remaining_daily_quota"] = max(0, get_settings().daily_email_limit - counts["sent_today"])
    events = session.execute(select(ApplicationEvent, Company.name)
        .join(Application, Application.id == ApplicationEvent.application_id)
        .join(Company, Company.id == Application.company_id)
        .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc())).all()
    history = [{"application_id": e.application_id, "company": name, "event": e.event,
                "timestamp": e.created_at, "details": e.details} for e, name in events]
    return {"counts": counts, **data, "events": history}


def filter_dashboard_rows(rows: list[dict[str, Any]], *, companies: list[str] | None = None,
                          min_score: float = 0, statuses: list[str] | None = None,
                          location: str = "", channels: list[str] | None = None,
                          contact_verifications: list[str] | None = None,
                          start_date: date | None = None, end_date: date | None = None) -> list[dict[str, Any]]:
    selected_companies = set(companies or [])
    selected_statuses = set(statuses or [])
    selected_channels = set(channels or [])
    selected_verifications = set(contact_verifications or [])
    location = (location or "").casefold().strip()
    result = []
    for row in rows:
        score = row.get("job_match_score")
        if selected_companies and row.get("company") not in selected_companies: continue
        if score is not None and score < min_score: continue
        if score is None and min_score > 0: continue
        if selected_statuses and row.get("application_status") not in selected_statuses: continue
        if location and location not in (row.get("location") or "").casefold(): continue
        if selected_channels and row.get("application_channel") not in selected_channels: continue
        if selected_verifications and row.get("contact_verification") not in selected_verifications: continue
        row_date = row.get("application_date") or row.get("last_updated")
        row_date = row_date.date() if isinstance(row_date, datetime) else row_date
        if start_date and (row_date is None or row_date < start_date): continue
        if end_date and (row_date is None or row_date > end_date): continue
        result.append(row)
    return result


def csv_bytes(rows: list[dict[str, Any]], dataset: str) -> bytes:
    if dataset not in CSV_FIELDS:
        raise ValueError("Dataset d'export non autorisé")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS[dataset], extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe_cell(row.get(key)) for key in CSV_FIELDS[dataset]})
    return output.getvalue().encode("utf-8-sig")


APPLICATION_FIELDS = CSV_FIELDS["applications"]


def xlsx_bytes(data: dict[str, Any]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, key in (("Companies", "companies"), ("Jobs", "job_offers"), ("Contacts", "contacts"), ("Applications", "applications")):
        sheet = workbook.create_sheet(title)
        fields = APPLICATION_FIELDS if title == "Applications" else CSV_FIELDS[key]
        sheet.append(fields)
        for row in data.get(key, []):
            sheet.append([_safe_cell(row.get(field)) for field in fields])
    summary = workbook.create_sheet("Summary")
    summary.append(["metric", "value"])
    for key, value in data.get("counts", {}).items():
        summary.append([key, value])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def application_history(session: Session) -> list[dict[str, Any]]:
    return dashboard_data(session)["events"]
