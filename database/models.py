from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class ApplicationStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    ANALYZED = "ANALYZED"
    CONTACT_FOUND = "CONTACT_FOUND"
    DRAFT_READY = "DRAFT_READY"
    APPROVED = "APPROVED"
    SENT = "SENT"
    REJECTED = "REJECTED"
    REPLIED = "REPLIED"
    IGNORED = "IGNORED"
    READY_TO_SEND = "READY_TO_SEND"
    SENDING = "SENDING"
    SEND_FAILED = "SEND_FAILED"
    SKIPPED = "SKIPPED"

class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class CVFile(Base):
    __tablename__ = "cv_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"))
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    extracted_text: Mapped[str | None] = mapped_column(Text)

class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    website: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None]
    sector: Mapped[str | None]
    size: Mapped[str | None]
    description: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    career_url: Mapped[str | None] = mapped_column(Text)
    career_last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    career_scan_status: Mapped[str | None] = mapped_column(String(40))
    contacts_last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contacts_scan_status: Mapped[str | None] = mapped_column(String(40))
    preferred_application_channel: Mapped[str | None] = mapped_column(String(40))
    application_form_url: Mapped[str | None] = mapped_column(Text)
    best_contact_id: Mapped[int | None]
    best_contact_reason: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    normalized_name: Mapped[str | None] = mapped_column(String(255), index=True)
    website_url: Mapped[str | None] = mapped_column(Text)
    company_type: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str | None] = mapped_column(String(100))
    discovery_query: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_domain_confidence: Mapped[float | None] = mapped_column(default=0.0)
    analysis_json: Mapped[str | None] = mapped_column(Text)
    analysis_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_relevance_score: Mapped[float | None] = mapped_column()
    relevance_reasons_json: Mapped[str | None] = mapped_column(Text)

class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_company_id", "company_id"),
        Index("ix_contacts_email", "email"),
        Index("ix_contacts_verification_status", "verification_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str | None]
    role: Mapped[str | None]
    email: Mapped[str | None]
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str]
    confidence_score: Mapped[float] = mapped_column(default=0)
    verification_status: Mapped[str] = mapped_column(default="UNVERIFIED")
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_domain_match: Mapped[bool | None]
    email_mx_status: Mapped[str | None] = mapped_column(String(20))
    email_is_disposable: Mapped[bool | None]

class JobOffer(Base):
    __tablename__ = "job_offers"
    __table_args__ = (Index("ix_job_offers_match_score", "match_score"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    title: Mapped[str]
    url: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None]
    match_score: Mapped[float | None]
    country: Mapped[str | None] = mapped_column(String(100))
    remote_policy: Mapped[str | None] = mapped_column(String(100))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    experience_level: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(255))
    requirements: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[float | None]
    salary_max: Mapped[float | None]
    salary_currency: Mapped[str | None] = mapped_column(String(10))
    source_type: Mapped[str | None] = mapped_column(String(80))
    external_id: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_potentially_relevant: Mapped[bool] = mapped_column(default=False)
    relevance_keywords: Mapped[str | None] = mapped_column(Text)
    match_profile_hash: Mapped[str | None] = mapped_column(String(64))
    match_offer_hash: Mapped[str | None] = mapped_column(String(64))
    matching_json: Mapped[str | None] = mapped_column(Text)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_status", "status"),
        Index("ix_applications_profile_company", "profile_id", "company_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    job_offer_id: Mapped[int | None] = mapped_column(ForeignKey("job_offers.id"))
    application_type: Mapped[str]
    selected_cv: Mapped[int | None] = mapped_column(ForeignKey("cv_files.id"))
    score: Mapped[float | None]
    status: Mapped[ApplicationStatus] = mapped_column(SAEnum(ApplicationStatus), default=ApplicationStatus.DISCOVERED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    followup_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_channel: Mapped[str | None] = mapped_column(String(40))
    application_strategy: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text)
    cv_match_score: Mapped[float | None]
    cv_selection_reason: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    short_message: Mapped[str | None] = mapped_column(Text)
    motivation_text: Mapped[str | None] = mapped_column(Text)
    generation_reason: Mapped[str | None] = mapped_column(Text)
    source_facts_json: Mapped[str | None] = mapped_column(Text)
    cache_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_user: Mapped[bool] = mapped_column(default=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    final_subject: Mapped[str | None] = mapped_column(Text)
    final_body: Mapped[str | None] = mapped_column(Text)
    final_short_message: Mapped[str | None] = mapped_column(Text)
    final_motivation_text: Mapped[str | None] = mapped_column(Text)
    recipient_email: Mapped[str | None] = mapped_column(String(320))
    recipient_name: Mapped[str | None] = mapped_column(String(255))
    gmail_message_id: Mapped[str | None] = mapped_column(String(255))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255))
    send_status: Mapped[str | None] = mapped_column(String(40))
    send_error: Mapped[str | None] = mapped_column(Text)
    send_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True)
    subject_sent: Mapped[str | None] = mapped_column(Text)
    cv_sent: Mapped[bool] = mapped_column(default=False)

class ApplicationEvent(Base):
    __tablename__ = "application_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    details: Mapped[str | None] = mapped_column(Text)

class Email(Base):
    __tablename__ = "emails"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    subject: Mapped[str]
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str]
    status: Mapped[str]
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
