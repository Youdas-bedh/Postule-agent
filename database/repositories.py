import hashlib
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session
from database.models import AgentRun, CandidateProfile


def save_profile(session: Session, filename: str, content: str) -> tuple[CandidateProfile, bool]:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result = session.execute(insert(CandidateProfile).values(
        filename=filename, content=content, content_hash=digest
    ).on_conflict_do_nothing(index_elements=["content_hash"]))
    created = result.rowcount == 1
    profile = session.scalar(select(CandidateProfile).where(CandidateProfile.content_hash == digest))
    assert profile is not None
    if created:
        session.add(AgentRun(action="PROFILE_IMPORTED", status="SUCCESS", details=f"profile_id={profile.id}"))
    return profile, created


def list_profiles(session: Session) -> list[CandidateProfile]:
    return list(session.scalars(select(CandidateProfile).order_by(CandidateProfile.id.desc())))
