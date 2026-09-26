from pathlib import Path
import sqlite3
from typing import Any
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from config import ROOT, get_settings
from database.models import Base


def build_engine(url: str | None = None) -> Engine:
    parsed = make_url(url or get_settings().database_url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError("Seul SQLite est pris en charge en phase 1.")
    if parsed.database and parsed.database != ":memory:":
        path = Path(parsed.database)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        parsed = parsed.set(database=str(path))
    engine = create_engine(parsed, hide_parameters=True, connect_args={"check_same_thread": False, "timeout": 30})
    @event.listens_for(engine, "connect")
    def configure_sqlite(connection: sqlite3.Connection, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    # Additive SQLite migration for databases created during phases 1 and 2.
    # Existing tables and rows are retained; new fields start as NULL.
    if engine.dialect.name == "sqlite":
        columns = {item["name"] for item in inspect(engine).get_columns("companies")}
        additions = {
            "normalized_name": "VARCHAR(255)", "website_url": "TEXT",
            "company_type": "VARCHAR(100)", "source_type": "VARCHAR(100)",
            "discovery_query": "TEXT", "discovered_at": "DATETIME",
            "official_domain_confidence": "FLOAT DEFAULT 0.0", "analysis_json": "TEXT",
            "analysis_updated_at": "DATETIME", "company_relevance_score": "FLOAT",
            "relevance_reasons_json": "TEXT",
            "career_last_scanned_at": "DATETIME", "career_scan_status": "VARCHAR(40)",
            "contacts_last_scanned_at": "DATETIME", "contacts_scan_status": "VARCHAR(40)",
            "preferred_application_channel": "VARCHAR(40)", "application_form_url": "TEXT",
            "best_contact_id": "INTEGER", "best_contact_reason": "TEXT",
        }
        with engine.begin() as connection:
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE companies ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_companies_normalized_name ON companies (normalized_name)"))
            # Unique constraints already index company domain and job URL; these
            # additive indexes cover common filters, foreign-key lookups and joins.
        job_columns = {item["name"] for item in inspect(engine).get_columns("job_offers")}
        job_additions = {
            "country": "VARCHAR(100)", "remote_policy": "VARCHAR(100)", "employment_type": "VARCHAR(100)",
            "experience_level": "VARCHAR(100)", "department": "VARCHAR(255)", "requirements": "TEXT", "skills": "TEXT",
            "salary_min": "FLOAT", "salary_max": "FLOAT", "salary_currency": "VARCHAR(10)",
            "source_type": "VARCHAR(80)", "external_id": "VARCHAR(255)", "published_at": "DATETIME",
            "discovered_at": "DATETIME", "updated_at": "DATETIME", "is_potentially_relevant": "BOOLEAN NOT NULL DEFAULT 0",
            "relevance_keywords": "TEXT",
            "match_profile_hash": "VARCHAR(64)", "match_offer_hash": "VARCHAR(64)",
            "matching_json": "TEXT", "matched_at": "DATETIME",
        }
        with engine.begin() as connection:
            for name, definition in job_additions.items():
                if name not in job_columns:
                    connection.execute(text(f"ALTER TABLE job_offers ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_job_offers_company_id ON job_offers (company_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_job_offers_external_id ON job_offers (company_id, external_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_job_offers_match_score ON job_offers (match_score)"))
        contact_columns = {item["name"] for item in inspect(engine).get_columns("contacts")}
        contact_additions = {
            "linkedin_url": "TEXT", "discovered_at": "DATETIME", "verified_at": "DATETIME",
            "email_domain_match": "BOOLEAN", "email_mx_status": "VARCHAR(20)", "email_is_disposable": "BOOLEAN",
        }
        with engine.begin() as connection:
            for name, definition in contact_additions.items():
                if name not in contact_columns:
                    connection.execute(text(f"ALTER TABLE contacts ADD COLUMN {name} {definition}"))
            # Some early installations contain deliberately partial legacy
            # tables; only add indexes where the corresponding column exists.
            for index_name, column_name in (
                ("ix_contacts_company_id", "company_id"),
                ("ix_contacts_email", "email"),
                ("ix_contacts_verification_status", "verification_status"),
            ):
                if column_name in contact_columns:
                    connection.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON contacts ({column_name})"))
        application_columns = {item["name"] for item in inspect(engine).get_columns("applications")}
        application_additions = {
            "application_channel": "VARCHAR(40)", "application_strategy": "TEXT", "application_url": "TEXT",
            "cv_match_score": "FLOAT", "cv_selection_reason": "TEXT", "subject": "TEXT", "body": "TEXT",
            "short_message": "TEXT", "motivation_text": "TEXT", "generation_reason": "TEXT",
            "source_facts_json": "TEXT", "cache_hash": "VARCHAR(64)", "generated_at": "DATETIME",
            "rejected_at": "DATETIME", "ignored_at": "DATETIME", "edited_at": "DATETIME",
            "edited_by_user": "BOOLEAN NOT NULL DEFAULT 0", "decision_reason": "TEXT",
            "final_subject": "TEXT", "final_body": "TEXT", "final_short_message": "TEXT",
            "final_motivation_text": "TEXT",
            "recipient_email": "VARCHAR(320)", "recipient_name": "VARCHAR(255)",
            "gmail_message_id": "VARCHAR(255)", "gmail_thread_id": "VARCHAR(255)",
            "send_status": "VARCHAR(40)", "send_error": "TEXT", "send_fingerprint": "VARCHAR(64)",
            "subject_sent": "TEXT", "cv_sent": "BOOLEAN NOT NULL DEFAULT 0",
        }
        with engine.begin() as connection:
            for name, definition in application_additions.items():
                if name not in application_columns:
                    connection.execute(text(f"ALTER TABLE applications ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_applications_cache_hash ON applications (cache_hash)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_applications_status ON applications (status)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_applications_profile_company ON applications (profile_id, company_id)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_applications_send_fingerprint ON applications (send_fingerprint) WHERE send_fingerprint IS NOT NULL"))
            connection.execute(text("""INSERT INTO application_events (application_id,event,created_at,details)
                SELECT a.id,'GENERATED',COALESCE(a.generated_at,a.created_at,CURRENT_TIMESTAMP),'Pre-existing draft imported into the decision audit.'
                FROM applications a
                WHERE a.status='DRAFT_READY' AND NOT EXISTS
                    (SELECT 1 FROM application_events e WHERE e.application_id=a.id)"""))


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)

if __name__ == "__main__":
    engine = build_engine()
    init_db(engine)
    print("Base SQLite initialisée (9 tables).")
