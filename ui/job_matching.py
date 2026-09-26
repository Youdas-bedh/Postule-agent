"""Candidate-profile to stored-job matching UI."""
from __future__ import annotations
import json, logging
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from agents.job_matcher import JobMatcher
from database.models import CandidateProfile, Company, JobOffer
from database.repositories import list_profiles
from config import get_settings
from llm.base import LLMError
from llm.factory import create_provider
from utils.geography import is_france_job

logger=logging.getLogger(__name__)

def render_job_matching(factory:sessionmaker[Session]):
    st.header("Job Matching")
    st.caption("Comparaison explicable entre le profil candidat actif et les offres enregistrées. Les affirmations du LLM sont retenues seulement avec des citations exactes des sources.")
    with factory() as session:
        profiles=list_profiles(session)
        companies=list(session.scalars(select(Company).order_by(Company.name)))
        all_offers=list(session.scalars(select(JobOffer).order_by(JobOffer.id)))
        total_offers=len(all_offers)
        offers=[offer for offer in all_offers if is_france_job(offer.country,offer.location)]
        rows=[(o.id,o.title,o.company_id,o.location,o.match_score,o.matching_json,o.match_profile_hash,o.url,o.is_potentially_relevant,o.matched_at) for o in offers]
        profile_rows=[(p.id,p.filename,p.created_at,p.content_hash) for p in profiles]
    if not profiles:
        st.warning("Importe un profil Markdown dans Profil candidat avant de lancer le matching."); return
    profile_by_id={p[0]:p for p in profile_rows}
    active_id=st.session_state.get("profile_id")
    if active_id not in profile_by_id:active_id=profile_rows[0][0]
    labels={p[0]:f"#{p[0]} — {p[1]} — {p[2]:%d/%m/%Y %H:%M} UTC" for p in profile_rows}
    selected_id=st.selectbox("Profil candidat actif",list(profile_by_id),index=list(profile_by_id).index(active_id),format_func=lambda x:labels[x],key="job_match_profile")
    st.session_state["profile_id"]=selected_id
    with factory() as session:
        profile=session.get(CandidateProfile,selected_id)
        companies={c.id:c.name for c in session.scalars(select(Company))}
        stale_ids={offer.id for offer in session.scalars(select(JobOffer)) if is_france_job(offer.country,offer.location) and JobMatcher.needs_match(profile,offer)}
    if not rows:
        message="Aucune offre enregistrée. Lance d'abord un scan dans Career & Jobs." if total_offers==0 else "Aucune offre enregistrée n’est explicitement localisée en France. Les offres étrangères ou à localisation incertaine ne sont pas matchées."
        st.info(message); return
    company_filter=st.multiselect("Entreprise",sorted(set(companies.values())))
    location_filter=st.text_input("Localisation contient")
    keyword_filter=st.text_input("Mot-clé dans l'offre")
    minimum=st.slider("Score minimum affiché",0,100,0)
    maximum=st.number_input("Maximum d'offres à traiter par exécution",min_value=1,max_value=200,value=20,step=5)
    force=st.checkbox("Forcer le recalcul, même si le profil et l'offre n'ont pas changé")
    base=[r for r in rows if (not company_filter or companies.get(r[2],"") in company_filter) and location_filter.casefold() in (r[3] or "").casefold() and keyword_filter.casefold() in (r[1] or "").casefold()]
    st.caption(f"{len(base)} offres correspondent aux filtres · {sum(1 for r in base if r[0] in stale_ids)} à calculer pour ce profil.")
    if st.button("Run matching",type="primary",disabled=not base):
        with factory() as session:
            active_profile=session.get(CandidateProfile,selected_id)
            db_offers=[session.get(JobOffer,r[0]) for r in base]
            provider=None; provider_error=None
            try:
                candidate=create_provider(get_settings()); status=candidate.check_connection()
                if status.available:provider=candidate
                else:provider_error=status.message; candidate.close()
            except (LLMError,ValueError,RuntimeError) as exc:provider_error=f"LLM indisponible ({type(exc).__name__})"
            if provider_error:st.warning(provider_error+" Le calcul déterministe reste disponible.")
            matcher=JobMatcher(provider)
            queue=[o for o in db_offers if o and (force or matcher.needs_match(active_profile,o))][:int(maximum)]
            if not queue:st.info("Les offres correspondant aux filtres sont déjà à jour.")
            else:
                progress=st.progress(0); status=st.status(f"Matching de {len(queue)} offres…",expanded=False); durations=[]; failures=0
                for index,offer in enumerate(queue,1):
                    try:
                        result=matcher.match(session,active_profile,offer,company_name=companies.get(offer.company_id),force=force)
                        durations.append(result.elapsed_seconds)
                    except Exception as exc:
                        failures+=1; logger.exception("[MATCH] Échec offer_id=%s",offer.id)
                    progress.progress(index/len(queue))
                session.commit()
                if provider:provider.close()
                status.update(label=f"{len(queue)-failures}/{len(queue)} offres analysées · moyenne {sum(durations)/len(durations):.2f} s" if durations else "Matching terminé avec erreurs",state="complete" if not failures else "error")
        st.rerun()
    # Refresh persisted rows for display after the operation.
    with factory() as session:
        display=[]
        for row in rows:
            offer=session.get(JobOffer,row[0]); current=offer.id not in stale_ids; score=offer.match_score if current else None
            if score is not None and score < minimum:continue
            if company_filter and companies.get(offer.company_id,"") not in company_filter:continue
            if location_filter.casefold() not in (offer.location or "").casefold():continue
            if keyword_filter.casefold() not in (offer.title+" "+(offer.description or "")).casefold():continue
            payload={}
            try:payload=json.loads((offer.matching_json if current else None) or "{}")
            except ValueError:pass
            display.append({"Offre":offer.title,"Entreprise":companies.get(offer.company_id,""),"Score":int(score) if score is not None else None,"Statut matching":"Calculé" if score is not None else "À calculer","Matching skills":", ".join(payload.get("matching_skills",[])),"Missing skills":", ".join(payload.get("missing_skills",[])),"Strengths":"; ".join(payload.get("strengths",[])),"Concerns":"; ".join(payload.get("concerns",[])),"Localisation":offer.location or "","Recommendation":payload.get("recommendation_reason",""),"Lien":offer.url,"Calculé":offer.matched_at.isoformat() if offer.matched_at else ""})
    st.dataframe(display,width="stretch",hide_index=True,column_config={"Score":st.column_config.NumberColumn("Score",format="%d"),"Lien":st.column_config.LinkColumn("Lien",display_text="Ouvrir l'offre")})
