"""Human decision transitions and content-safe application audit events."""
from __future__ import annotations
from datetime import datetime,timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from database.models import Application,ApplicationEvent,ApplicationStatus

class ApplicationWorkflowError(ValueError):pass

def _now():return datetime.now(timezone.utc)

def record_event(session:Session,application:Application,event:str,details:str|None=None)->ApplicationEvent:
    allowed={"GENERATED","REGENERATED","EDITED","APPROVED","REJECTED","IGNORED","READY_TO_SEND","SEND_STARTED","SENT","SEND_FAILED","SKIPPED","WOULD_SEND"}
    if event not in allowed:raise ApplicationWorkflowError("Événement d’audit non autorisé.")
    row=ApplicationEvent(application_id=application.id,event=event,created_at=_now(),details=details)
    session.add(row);return row

def _draft(application:Application)->None:
    if application.status!=ApplicationStatus.DRAFT_READY:
        raise ApplicationWorkflowError(f"Action impossible : la candidature est {application.status.value}.")

def edit_draft(session:Session,application:Application,*,subject:str|None,body:str|None,short_message:str|None,motivation_text:str|None)->Application:
    _draft(application)
    values={"subject":subject,"body":body,"short_message":short_message,"motivation_text":motivation_text}
    for key,value in values.items():
        setattr(application,key,value)
        setattr(application,{"subject":"final_subject","body":"final_body","short_message":"final_short_message","motivation_text":"final_motivation_text"}[key],value)
    application.edited_by_user=True;application.edited_at=_now()
    record_event(session,application,"EDITED","Champs modifiés : "+", ".join(values))
    session.flush();return application

def approve_draft(session:Session,application:Application)->Application:
    _draft(application)
    application.final_subject=application.subject;application.final_body=application.body
    application.final_short_message=application.short_message;application.final_motivation_text=application.motivation_text
    application.status=ApplicationStatus.APPROVED;application.approved_at=_now();application.decision_reason=None
    record_event(session,application,"APPROVED","Brouillon approuvé par décision utilisateur.")
    session.flush();return application

def reject_draft(session:Session,application:Application,reason:str|None=None)->Application:
    _draft(application);application.status=ApplicationStatus.REJECTED;application.rejected_at=_now();application.decision_reason=_clean_reason(reason)
    record_event(session,application,"REJECTED","Candidature rejetée par décision utilisateur.")
    session.flush();return application

def ignore_draft(session:Session,application:Application,reason:str|None=None)->Application:
    _draft(application);application.status=ApplicationStatus.IGNORED;application.ignored_at=_now();application.decision_reason=_clean_reason(reason)
    record_event(session,application,"IGNORED","Candidature ignorée par décision utilisateur.")
    session.flush();return application

def _clean_reason(value:str|None)->str|None:
    if value is None:return None
    # Keep decision notes short; audit details never contain their contents.
    result=" ".join(value.split())[:500]
    return result or None

def application_events(session:Session,application_id:int)->list[ApplicationEvent]:
    return list(session.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id==application_id).order_by(ApplicationEvent.created_at,ApplicationEvent.id)))
