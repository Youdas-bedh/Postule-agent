"""Human review and approval for local application drafts. No external actions."""
from __future__ import annotations
import json,logging
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session,sessionmaker
from agents.application_strategy import ApplicationStrategyAgent
from agents.application_workflow import ApplicationWorkflowError,application_events,approve_draft,edit_draft,ignore_draft,reject_draft
from agents.contact_verifier import rank_contacts
from config import get_settings
from database.models import Application,ApplicationStatus,ApplicationEvent,CandidateProfile,Company,Contact,CVFile,JobOffer
from database.repositories import list_profiles
from llm.base import LLMError
from llm.factory import create_provider
from services.cv_service import CVImportError,import_cv
from services.send_queue import run_outreach

logger=logging.getLogger(__name__)

def render_applications(factory:sessionmaker[Session])->None:
    st.header("Applications")
    st.caption("Validation humaine obligatoire. Les décisions sont enregistrées localement ; aucun email n’est envoyé et aucun formulaire n’est soumis.")
    with factory() as session:
        profiles=list_profiles(session);companies=list(session.scalars(select(Company).order_by(Company.name)))
    if not profiles:
        st.warning("Aucun profil candidat. Importer un profil est nécessaire avant de préparer une candidature.");return
    profile_by_id={p.id:p for p in profiles};active=st.session_state.get("profile_id")
    if active not in profile_by_id:active=profiles[0].id
    chosen=st.selectbox("Profil candidat actif",list(profile_by_id),index=list(profile_by_id).index(active),format_func=lambda i:f"#{i} — {profile_by_id[i].filename}",key="applications_profile")
    st.session_state["profile_id"]=chosen
    with st.expander("CV importés pour ce profil",expanded=False):
        with factory() as session:cvs=list(session.scalars(select(CVFile).where(CVFile.profile_id==chosen).order_by(CVFile.id)))
        for cv in cvs:st.write(f"#{cv.id} · {cv.filename} · {len(cv.extracted_text or '')} caractères extraits")
        if not cvs:st.info("Aucun CV importé. Les candidatures resteront en NO_ACTION tant qu’un CV n’est pas sélectionnable.")
        with st.form(f"cv_import_form_{chosen}",clear_on_submit=True):
            uploaded=st.file_uploader("Importer un CV (PDF, DOCX, TXT ou Markdown)",type=["pdf","docx","txt","md","markdown"],key=f"cv_upload_{chosen}")
            import_clicked=st.form_submit_button("Import CV",disabled=uploaded is None)
        if import_clicked and uploaded is not None:
            try:
                with factory.begin() as session:
                    profile=session.get(CandidateProfile,chosen);import_cv(session,profile,uploaded.name,uploaded.getvalue())
                st.success("CV importé sans modifier l’original.");st.rerun()
            except (CVImportError,OSError,ValueError) as exc:st.error(f"Import impossible : {exc}")
    for company in companies:
        with factory() as session:
            company=session.get(Company,company.id);profile=session.get(CandidateProfile,chosen)
            existing=session.scalar(select(Application).where(Application.company_id==company.id,Application.profile_id==chosen).order_by(Application.id.desc()))
            plan=ApplicationStrategyAgent().plan(session,company,profile)
            job=session.get(JobOffer,existing.job_offer_id) if existing and existing.job_offer_id else session.get(JobOffer,plan.target_job_id) if plan.target_job_id else None
            contact=session.get(Contact,existing.contact_id) if existing and existing.contact_id else session.get(Contact,plan.contact_id) if plan.contact_id else None
            if contact is None and company.best_contact_id:contact=session.get(Contact,company.best_contact_id)
            if contact is None:
                contacts=list(session.scalars(select(Contact).where(Contact.company_id==company.id)))
                ranked=rank_contacts(contacts)
                contact=next((c for c in ranked if c.verification_status=="VERIFIED" and int(c.confidence_score or 0)>=get_settings().min_contact_confidence),ranked[0] if ranked else None)
            cv=session.get(CVFile,existing.selected_cv) if existing and existing.selected_cv else session.get(CVFile,plan.cv.selected_cv) if plan.cv.selected_cv else None
            events=application_events(session,existing.id) if existing else []
            facts=list(_facts(existing)) if existing else []
            snapshot={"app_id":existing.id if existing else None,"status":_status(existing.status) if existing else None,"application_type":existing.application_type if existing else plan.application_type,"channel":existing.application_channel if existing else plan.application_channel,"score":existing.score if existing else (job.match_score if job else None),"cv_score":existing.cv_match_score if existing else plan.cv.cv_match_score,"cv_reason":existing.cv_selection_reason if existing else plan.cv.cv_selection_reason,"subject":_final(existing,"subject"),"body":_final(existing,"body"),"short_message":_final(existing,"short_message"),"motivation":_final(existing,"motivation_text"),"url":existing.application_url if existing else plan.application_url,"strategy":existing.application_strategy if existing else plan.application_strategy,"reason":existing.generation_reason if existing else plan.generation_reason,"decision_reason":existing.decision_reason if existing else None,"edited":existing.edited_by_user if existing else False,"events":[(e.event,e.created_at,e.details) for e in events],"facts":facts,"approved_at":existing.approved_at if existing else None}
            company_name=company.name;company_id=company.id;plan_channel=plan.application_channel;plan_status=plan.status;plan_reason=plan.generation_reason;plan_url=plan.application_url;plan_job_url=plan.job_url;plan_score=plan.cv.cv_match_score;plan_cv_reason=plan.cv.cv_selection_reason
            job_title=job.title if job else "Candidature spontanée";job_url=job.url if job else plan.job_url;match_score=job.match_score if job else None
            contact_name=contact.name if contact else None;contact_role=contact.role if contact else None;contact_email=contact.email if contact and contact.verification_status=="VERIFIED" else None;contact_confidence=int(contact.confidence_score or 0) if contact else None;contact_status=contact.verification_status if contact else None
            cv_name=cv.filename if cv else None
        with st.container(border=True):
            st.subheader(company_name)
            if snapshot["app_id"]:
                st.write(f"**Offre :** {job_title} · **Score profil/offre :** {snapshot['score'] if snapshot['score'] is not None else '—'} · **Canal :** {snapshot['channel']} · **Contact :** {_contact_label(contact_name,contact_role,contact_status)} · **Confiance contact :** {contact_confidence if contact_confidence is not None else '—'} · **CV sélectionné :** {cv_name or '—'} · **Score CV :** {snapshot['cv_score'] if snapshot['cv_score'] is not None else '—'} · **Statut :** {snapshot['status']}")
                _render_existing(factory,snapshot,job_title,job_url,cv_name,contact_email,contact_confidence,company_id,chosen)
            else:
                st.write(f"**Offre :** {job_title} · **Score profil/offre :** {match_score if match_score is not None else '—'} · **Canal :** {plan_channel} · **Contact :** {_contact_label(contact_name,contact_role,contact_status)} · **Confiance contact :** {contact_confidence if contact_confidence is not None else '—'} · **CV sélectionné :** {cv_name or '—'} · **Score CV :** {plan_score} · **Statut :** {plan_status}")
                st.caption(plan_cv_reason)
                if plan_channel=="NO_ACTION":st.warning(plan_reason)
                if plan_channel=="DIRECT_JOB_APPLICATION" and plan_url:st.link_button("Apply directly on the official job page",plan_url)
                elif plan_job_url:st.link_button("Open target job page",plan_job_url)
                if plan_channel!="NO_ACTION" and st.button("Generate application",key=f"generate_application_{company_id}"):_generate(factory,company_id,chosen,False)

def _render_existing(factory:sessionmaker[Session],data:dict,job_title:str,job_url:str|None,cv_name:str|None,contact_email:str|None,contact_confidence:int|None,company_id:int,profile_id:int)->None:
    ident=data["app_id"];status=data["status"]
    preview=f"Preview final application — #{ident}"
    with st.expander(preview,expanded=True):
        if data["channel"]=="DIRECT_JOB_APPLICATION":
            st.markdown(f"**Offer:** {job_title}")
            if job_url:st.link_button("Apply directly on the official job page",job_url)
            st.markdown(f"**Selected CV:** {cv_name or '—'} · {data['cv_score']}/100")
            if data["strategy"]:st.markdown(f"**Application note:** {data['strategy']}")
        elif data["channel"]=="CAREER_FORM":
            if data["url"]:st.link_button("Career form",data["url"])
            st.markdown(f"**Selected CV:** {cv_name or '—'} · {data['cv_score']}/100")
            if data["motivation"]:st.markdown("**Motivation**\n\n"+data["motivation"])
        else:
            if data["subject"]:st.markdown("**Subject:** "+data["subject"])
            if contact_email:st.markdown(f"**To:** {contact_email} · confidence {contact_confidence}/100")
            if data["body"]:st.markdown("**Body**\n\n"+data["body"])
            if data["short_message"]:st.markdown("**Short message**\n\n"+data["short_message"])
            if data["url"]:st.link_button("Contact source",data["url"])
            if cv_name:st.markdown(f"**Selected CV:** {cv_name} · {data['cv_score']}/100")
            if data["motivation"]:st.markdown("**Motivation**\n\n"+data["motivation"])
        if data["reason"]:st.markdown("**Strategy reason:** "+data["reason"])
        if data["facts"]:
            st.markdown("**Source facts — review these before approval:**")
            for fact in data["facts"]:st.markdown(f"- {fact}")
        if data["edited"]:st.caption("Edited manually by the user.")
        if data["decision_reason"]:st.caption("Decision note: "+data["decision_reason"])
        if data["events"]:
            with st.expander("Audit history"):
                for event,when,details in data["events"]:st.write(f"{when} · {event} · {details or ''}")
    if status==ApplicationStatus.DRAFT_READY.value:
        edit_key=f"application_edit_mode_{ident}"
        if not st.session_state.get(edit_key,False):
            if st.button("EDIT",key=f"application_edit_{ident}"):st.session_state[edit_key]=True;st.rerun()
        else:
            with st.form(f"application_edit_form_{ident}"):
                subject=st.text_input("Subject",data["subject"] or "")
                body=st.text_area("Body",data["body"] or "",height=220)
                short=st.text_area("Short message",data["short_message"] or "",height=100)
                motivation=st.text_area("Motivation",data["motivation"] or "",height=140)
                save=st.form_submit_button("Save edits")
            cancel=st.button("Cancel edit",key=f"application_edit_cancel_{ident}")
            if cancel:st.session_state[edit_key]=False;st.rerun()
            if save:
                _edit(factory,ident,subject,body,short,motivation);st.session_state[edit_key]=False;st.rerun()
        reason=st.text_input("Decision reason (optional)",key=f"decision_reason_{ident}")
        regenerate,approve,reject,ignore=st.columns(4)
        with regenerate:
            if st.button("Regenerate",key=f"application_regenerate_{ident}"):_generate(factory,company_id,profile_id,True)
        with approve:
            if st.button("APPROVE",key=f"application_approve_{ident}",type="primary"):_decision(factory,ident,"approve",reason)
        with reject:
            if st.button("REJECT",key=f"application_reject_{ident}"):_decision(factory,ident,"reject",reason)
        with ignore:
            if st.button("IGNORE",key=f"application_ignore_{ident}"):_decision(factory,ident,"ignore",reason)
    elif status==ApplicationStatus.APPROVED.value:
        st.success(f"APPROVED le {data['approved_at']}. Brouillon verrouillé contre la régénération automatique.")

def _generate(factory:sessionmaker[Session],company_id:int,profile_id:int,force:bool)->None:
    provider=None
    try:
        with factory() as session:
            company=session.get(Company,company_id);profile=session.get(CandidateProfile,profile_id)
            if not company:return
            plan=ApplicationStrategyAgent().plan(session,company,profile)
            if plan.application_channel in {"VERIFIED_EMAIL","RECRUITER_CONTACT"}:
                try:
                    candidate=create_provider(get_settings());status=candidate.check_connection()
                    if status.available:provider=candidate
                    else:candidate.close()
                except (LLMError,ValueError,RuntimeError):provider=None
            result=ApplicationStrategyAgent().generate(session,company,profile,provider=provider,force=force)
            session.commit();st.toast("Brouillon enregistré." if result.status=="DRAFT_READY" else result.generation_reason)
        settings=get_settings()
        if settings.auto_send_on_discovery and result.application_id and result.status=="DRAFT_READY":
            with factory.begin() as session:
                preview=run_outreach(session,settings=settings,application_ids={result.application_id})
            if preview and preview[0]["dry_run_status"]=="WOULD_SEND":
                st.info("Aperçu automatique prêt (WOULD_SEND) ; aucun email n’a été transmis.")
    except ApplicationWorkflowError as exc:st.error(str(exc))
    finally:
        if provider:provider.close()
    st.rerun()

def _edit(factory,ident,subject,body,short,motivation)->None:
    with factory.begin() as session:
        application=session.get(Application,ident)
        if not application:raise ApplicationWorkflowError("Candidature introuvable.")
        edit_draft(session,application,subject=subject or None,body=body or None,short_message=short or None,motivation_text=motivation or None)

def _decision(factory,ident,action,reason)->None:
    try:
        with factory.begin() as session:
            application=session.get(Application,ident)
            if not application:raise ApplicationWorkflowError("Candidature introuvable.")
            {"approve":approve_draft,"reject":reject_draft,"ignore":ignore_draft}[action](session,application) if action=="approve" else {"reject":reject_draft,"ignore":ignore_draft}[action](session,application,reason)
        st.toast(f"Décision enregistrée : {action.upper()}.")
    except ApplicationWorkflowError as exc:st.error(str(exc))
    st.rerun()

def _facts(application:Application|None)->list[str]:
    try:return json.loads(application.source_facts_json or "[]") if application else []
    except (ValueError,TypeError):return []

def _final(application:Application|None,key:str)->str|None:
    if application is None:return None
    final=getattr(application,"final_"+key,None)
    return final if final is not None else getattr(application,key,None)

def _status(status)->str:return status.value if hasattr(status,"value") else str(status)

def _contact_label(name,role,status)->str:
    if name:return name
    if role:return f"{role} ({status or 'statut inconnu'})"
    return f"Contact ({status or 'statut inconnu'})" if status else "—"
