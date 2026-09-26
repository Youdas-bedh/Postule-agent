"""Choose a safe application route and persist a reviewable, unsent draft."""
from __future__ import annotations
import hashlib,json,logging,time
from datetime import datetime,timezone
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from agents.contact_verifier import rank_contacts
from agents.cv_selector import CVSelector,CVSelection
from agents.email_generator import EmailGenerator
from agents.application_workflow import ApplicationWorkflowError,record_event
from config import get_settings
from database.models import Application,ApplicationStatus,CandidateProfile,Company,Contact,CVFile,JobOffer
from llm.base import LLMError,LLMProvider
from utils.geography import is_france_job

logger=logging.getLogger(__name__)
ApplicationChannel=Literal["DIRECT_JOB_APPLICATION","CAREER_FORM","VERIFIED_EMAIL","RECRUITER_CONTACT","NO_ACTION"]

class ApplicationPlan(BaseModel):
    company_id:int
    profile_id:int|None=None
    application_type:str
    application_channel:ApplicationChannel
    target_job_id:int|None=None
    job_url:str|None=None
    contact_id:int|None=None
    cv:CVSelection
    generation_reason:str
    application_strategy:str
    application_url:str|None=None
    status:str="DRAFT_READY"

class GeneratedApplication(BaseModel):
    model_config=ConfigDict(extra="forbid")
    company_id:int
    job_offer_id:int|None=None
    target_job_id:int|None=None
    contact_id:int|None=None
    application_type:str
    application_channel:ApplicationChannel
    selected_cv:int|None=None
    cv_match_score:int=Field(ge=0,le=100)
    cv_selection_reason:str
    subject:str|None=None
    body:str|None=None
    short_message:str|None=None
    motivation_text:str|None=None
    application_url:str|None=None
    application_strategy:str
    application_notes:str|None=None
    generation_reason:str
    source_facts:list[str]=Field(default_factory=list)
    status:str="DRAFT_READY"
    application_id:int|None=None
    cached:bool=False
    generation_time_seconds:float=0.0

class ApplicationStrategyAgent:
    def __init__(self,cv_selector:CVSelector|None=None,email_generator:EmailGenerator|None=None,*,direct_min_score:int|None=None,spontaneous_min_score:int|None=None,min_contact_confidence:int|None=None,prefer_email:bool=False):
        cfg=get_settings();self.cv_selector=cv_selector or CVSelector();self.email_generator=email_generator
        self.direct_min=cfg.direct_application_min_score if direct_min_score is None else direct_min_score
        self.spontaneous_min=cfg.spontaneous_company_min_score if spontaneous_min_score is None else spontaneous_min_score
        self.min_contact=cfg.min_contact_confidence if min_contact_confidence is None else min_contact_confidence
        self.prefer_email=prefer_email

    def plan(self,session:Session,company:Company,profile:CandidateProfile|None)->ApplicationPlan:
        no_cv=CVSelection(selected_cv=None,cv_match_score=0,cv_selection_reason="Aucun CV importé pour le profil candidat actif.")
        if profile is None:
            return ApplicationPlan(company_id=company.id,application_type="NO_ACTION",application_channel="NO_ACTION",cv=no_cv,generation_reason="Missing profile: aucun profil candidat actif.",application_strategy="Aucune action : importe ou sélectionne d’abord un profil candidat.",status="NO_ACTION")
        cvs=list(session.scalars(select(CVFile).where(CVFile.profile_id==profile.id)))
        offers=[offer for offer in session.scalars(select(JobOffer).where(JobOffer.company_id==company.id).order_by(JobOffer.match_score.desc().nullslast(),JobOffer.id.desc())) if is_france_job(offer.country,offer.location)]
        direct=next((job for job in offers if job.match_score is not None and job.match_score>=self.direct_min and job.match_profile_hash==profile.content_hash and job.url),None)
        job=direct
        if job is None:job=next((o for o in offers if o.match_score is not None and o.match_profile_hash==profile.content_hash),None)
        cv=self.cv_selector.select(session,profile,company,job)
        if direct:
            if not cvs:return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="NO_ACTION",application_channel="NO_ACTION",target_job_id=direct.id,job_url=direct.url,cv=cv,generation_reason="Missing CV: aucun CV importé ; la candidature est bloquée sans document à sélectionner.",application_strategy="NO_ACTION — importe un CV avant de préparer une candidature.",status="NO_ACTION")
            if self.prefer_email:
                contacts=list(session.scalars(select(Contact).where(Contact.company_id==company.id,Contact.verification_status=="VERIFIED",Contact.confidence_score>=self.min_contact,Contact.email.is_not(None))))
                contacts=rank_contacts(contacts)
                if contacts:
                    contact=contacts[0]
                    channel="RECRUITER_CONTACT" if contact.name or contact.role in {"Recruitment","Talent Acquisition","Recruiter","HR","Engineering Manager"} else "VERIFIED_EMAIL"
                    return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="DIRECT_JOB_APPLICATION",application_channel=channel,target_job_id=direct.id,job_url=direct.url,contact_id=contact.id,cv=cv,generation_reason=f"Offre France correspondante ({direct.match_score}/100) et contact vérifié.",application_strategy="Candidature ciblée par email à un contact vérifié.",application_url=contact.source_url)
            return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="DIRECT_JOB_APPLICATION",application_channel="DIRECT_JOB_APPLICATION",target_job_id=direct.id,job_url=direct.url,cv=cv,generation_reason=f"Offre correspondante avec score {direct.match_score}/100, au-dessus du seuil {self.direct_min}.",application_strategy="Apply directly on the official job page",application_url=direct.url)
        relevance=float(company.company_relevance_score or 0)
        if relevance<self.spontaneous_min:
            return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="NO_ACTION",application_channel="NO_ACTION",target_job_id=job.id if job else None,job_url=job.url if job else None,cv=cv,generation_reason=f"Company relevance too low: {relevance:.0f}/100, seuil {self.spontaneous_min}.",application_strategy="Aucune candidature préparée ; pertinence entreprise sous le seuil.",status="NO_ACTION")
        eligible=list(session.scalars(select(Contact).where(Contact.company_id==company.id,Contact.verification_status=="VERIFIED",Contact.confidence_score>=self.min_contact,Contact.email.is_not(None))))
        eligible=rank_contacts(eligible);contact=eligible[0] if eligible else None
        if company.application_form_url:
            channel="CAREER_FORM";app_url=company.application_form_url
        elif contact:
            channel="RECRUITER_CONTACT" if contact.name or contact.role in {"Recruitment","Talent Acquisition","Recruiter","HR","Engineering Manager"} else "VERIFIED_EMAIL"
            app_url=contact.source_url
        else:
            return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="NO_ACTION",application_channel="NO_ACTION",target_job_id=job.id if job else None,job_url=job.url if job else None,cv=cv,generation_reason="No reliable contact: aucun formulaire ni contact email vérifié au seuil requis.",application_strategy="Aucune voie de candidature fiable n’est enregistrée.",status="NO_ACTION")
        if not cvs:return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="NO_ACTION",application_channel="NO_ACTION",target_job_id=job.id if job else None,job_url=job.url if job else None,contact_id=contact.id if contact else None,cv=cv,generation_reason="Missing CV: aucun CV importé ; la candidature est bloquée sans document à sélectionner.",application_strategy="NO_ACTION — importe un CV avant de préparer une candidature.",application_url=app_url,status="NO_ACTION")
        return ApplicationPlan(company_id=company.id,profile_id=profile.id,application_type="SPONTANEOUS_APPLICATION",application_channel=channel,target_job_id=job.id if job else None,job_url=job.url if job else None,contact_id=contact.id if contact else None,cv=cv,generation_reason=f"Pertinence entreprise {relevance:.0f}/100 ; aucune offre au-dessus du seuil direct {self.direct_min}.",application_strategy="Candidature spontanée via formulaire carrière." if channel=="CAREER_FORM" else "Candidature spontanée adressée à un contact professionnel vérifié.",application_url=app_url)

    def generate(self,session:Session,company:Company,profile:CandidateProfile|None,*,provider:LLMProvider|None=None,force:bool=False,confirm_approved:bool=False)->GeneratedApplication:
        started=time.perf_counter();plan=self.plan(session,company,profile)
        if plan.application_channel=="NO_ACTION":
            return GeneratedApplication(company_id=company.id,application_type="NO_ACTION",application_channel="NO_ACTION",cv_match_score=0,cv_selection_reason=plan.cv.cv_selection_reason,application_strategy=plan.application_strategy,generation_reason=plan.generation_reason,status="NO_ACTION",target_job_id=plan.target_job_id,generation_time_seconds=time.perf_counter()-started)
        assert profile is not None and plan.cv.selected_cv is not None
        job=session.get(JobOffer,plan.target_job_id) if plan.target_job_id else None
        contact=session.get(Contact,plan.contact_id) if plan.contact_id else None
        cv=session.get(CVFile,plan.cv.selected_cv)
        cache_data="\n".join([profile.content_hash,company.name,company.domain or "",company.sector or "",company.description or "",company.analysis_json or "",str(round(float(company.company_relevance_score or 0),3)),str(plan.application_url or ""),str(job.id if job else ""),job.title or "" if job else "",job.url or "" if job else "",job.description or "" if job else "",job.requirements or "" if job else "",job.skills or "" if job else "",str(round(float(job.match_score),3)) if job and job.match_score is not None else "",str(contact.id if contact else ""),str(contact.email if contact else ""),str(contact.role if contact else ""),str(contact.verification_status if contact else ""),str(cv.id),cv.filename,cv.extracted_text or "",str(plan.cv.cv_match_score),plan.application_channel])
        digest=hashlib.sha256(cache_data.encode()).hexdigest()
        stmt=select(Application).where(Application.company_id==company.id,Application.profile_id==profile.id,Application.job_offer_id==job.id if job else Application.job_offer_id.is_(None),Application.application_type==plan.application_type).order_by(Application.id.desc())
        existing=session.scalar(stmt)
        if existing and existing.status==ApplicationStatus.APPROVED:
            if not confirm_approved:raise ApplicationWorkflowError("Une candidature approuvée est verrouillée ; une nouvelle génération nécessite une confirmation explicite.")
            existing=None
        elif existing and existing.status in {ApplicationStatus.REJECTED,ApplicationStatus.IGNORED}:
            if not force:raise ApplicationWorkflowError("Une candidature rejetée ou ignorée ne peut pas être régénérée automatiquement.")
            existing=None
        if existing and existing.cache_hash==digest and not force:
            return self._from_row(existing,plan,True,time.perf_counter()-started)
        regenerated=existing is not None
        if plan.application_channel=="DIRECT_JOB_APPLICATION":
            facts=[f"Profil importé : {profile.filename}.",f"Offre : {job.title}.",f"Score de matching sauvegardé : {job.match_score}/100."]
            draft_subject=draft_body=draft_short=draft_motivation=None;reason=plan.generation_reason
            mode="direct-application note; no email generated"
        else:
            generator=self.email_generator or EmailGenerator(provider)
            draft,facts,mode,_=generator.generate(profile,company,job,contact,plan.application_channel)
            draft_subject,draft_body,draft_short,draft_motivation=draft.subject,draft.body,draft.short_message,draft.motivation_text
            reason=plan.generation_reason+" Génération : "+mode+". Brouillon à relire ; aucun envoi effectué."
        draft_application=Application(company_id=company.id,profile_id=profile.id,contact_id=plan.contact_id,job_offer_id=plan.target_job_id,application_type=plan.application_type,selected_cv=plan.cv.selected_cv,score=job.match_score if job else None,status=ApplicationStatus.DRAFT_READY,application_channel=plan.application_channel,application_strategy=plan.application_strategy,application_url=plan.application_url,cv_match_score=plan.cv.cv_match_score,cv_selection_reason=plan.cv.cv_selection_reason,subject=draft_subject,body=draft_body,short_message=draft_short,motivation_text=draft_motivation,final_subject=draft_subject,final_body=draft_body,final_short_message=draft_short,final_motivation_text=draft_motivation,edited_by_user=False,edited_at=None,approved_at=None,rejected_at=None,ignored_at=None,decision_reason=None,generation_reason=reason,source_facts_json=json.dumps(facts,ensure_ascii=False),cache_hash=digest,generated_at=datetime.now(timezone.utc))
        if existing:
            for field in ("contact_id","job_offer_id","application_type","selected_cv","score","status","application_channel","application_strategy","application_url","cv_match_score","cv_selection_reason","subject","body","short_message","motivation_text","final_subject","final_body","final_short_message","final_motivation_text","edited_by_user","edited_at","approved_at","rejected_at","ignored_at","decision_reason","generation_reason","source_facts_json","cache_hash","generated_at"):
                setattr(existing,field,getattr(draft_application,field))
            row=existing
        else:
            row=draft_application;session.add(row)
        session.flush()
        record_event(session,row,"REGENERATED" if regenerated else "GENERATED",f"Brouillon préparé : {plan.application_type} / {plan.application_channel}.")
        session.flush()
        return GeneratedApplication(company_id=company.id,job_offer_id=plan.target_job_id,target_job_id=plan.target_job_id,contact_id=plan.contact_id,application_type=plan.application_type,application_channel=plan.application_channel,selected_cv=plan.cv.selected_cv,cv_match_score=plan.cv.cv_match_score,cv_selection_reason=plan.cv.cv_selection_reason,subject=draft_subject,body=draft_body,short_message=draft_short,motivation_text=draft_motivation,application_url=plan.application_url,application_strategy=plan.application_strategy,application_notes=plan.application_strategy if plan.application_channel=="DIRECT_JOB_APPLICATION" else None,generation_reason=reason,source_facts=facts,status="DRAFT_READY",application_id=row.id,generation_time_seconds=time.perf_counter()-started)

    @staticmethod
    def _from_row(row:Application,plan:ApplicationPlan,cached:bool,elapsed:float)->GeneratedApplication:
        try:facts=json.loads(row.source_facts_json or "[]")
        except ValueError:facts=[]
        return GeneratedApplication(company_id=row.company_id,job_offer_id=row.job_offer_id,target_job_id=row.job_offer_id,contact_id=row.contact_id,application_type=row.application_type,application_channel=row.application_channel or "NO_ACTION",selected_cv=row.selected_cv,cv_match_score=int(row.cv_match_score or 0),cv_selection_reason=row.cv_selection_reason or plan.cv.cv_selection_reason,subject=row.subject,body=row.body,short_message=row.short_message,motivation_text=row.motivation_text,application_url=row.application_url,application_strategy=row.application_strategy or "",application_notes=row.application_strategy if row.application_channel=="DIRECT_JOB_APPLICATION" else None,generation_reason=row.generation_reason or "",source_facts=facts,status=row.status.value if isinstance(row.status,ApplicationStatus) else str(row.status),application_id=row.id,cached=cached,generation_time_seconds=elapsed)
