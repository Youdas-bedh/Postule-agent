"""Streamlit interface for public careers and offers."""
from __future__ import annotations
import json
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from agents.career_agent import CareerAgent
from database.models import Company, JobOffer
from utils.geography import is_france_job

def render_career_jobs(factory:sessionmaker[Session]):
    st.header("Career & Jobs")
    st.caption("Offres publiques collectées depuis les pages carrière. Le matching profil ↔ offre est disponible dans Job Matching ; aucun contact RH n'est collecté.")
    with factory() as session:
        companies=list(session.scalars(select(Company).order_by(Company.name)))
        rows=[(c.id,c.name,c.career_url,c.career_last_scanned_at,c.career_scan_status,session.scalar(select(JobOffer.id).where(JobOffer.company_id==c.id).limit(1)) is not None) for c in companies]
        counts={c.id:0 for c in companies}
        location_counts=session.execute(select(JobOffer.company_id,JobOffer.country,JobOffer.location,func.count(JobOffer.id)).group_by(JobOffer.company_id,JobOffer.country,JobOffer.location)).all()
        for company_id,country,location,count in location_counts:
            if company_id in counts and is_france_job(country,location):counts[company_id]+=count
    if not rows:
        st.info("Aucune entreprise en base. Ajoute d'abord des entreprises dans Company Discovery."); return
    st.dataframe([{"Company":r[1],"Career page":r[2] or "Non détectée","Number of jobs found":counts[r[0]],"Last scan":r[3].isoformat() if r[3] else "—","Scan status":r[4] or "À scanner"} for r in rows],width="stretch",hide_index=True)
    st.caption("Le tableau d’offres ci-dessous ne montre que les postes explicitement localisés en France. Les pays inconnus et postes internationaux sont écartés ; le scan garde les données source en base.")
    names={r[1]:r[0] for r in rows}; selected=st.multiselect("Companies to scan",list(names),default=[])
    force=st.checkbox("Force rescan")
    if st.button("Scan careers",type="primary",disabled=not selected):
        for index,name in enumerate(selected,1):
            with st.status(f"Scan {index}/{len(selected)} · {name}",expanded=False) as status:
                try:
                    with factory() as session:
                        company=session.get(Company,names[name]); result=CareerAgent().scan_company(session,company,force=force)
                    if result.error: status.update(label=f"{name} · {result.status}: {result.error}",state="error")
                    else: status.update(label=f"{name} · {len(result.jobs)} offres · {result.status} · {result.elapsed_seconds:.1f} s",state="complete")
                except Exception as exc:
                    status.update(label=f"{name} · erreur {type(exc).__name__}",state="error")
        st.rerun()
    st.subheader("Job offers")
    with factory() as session:
        offers=list(session.scalars(select(JobOffer).order_by(JobOffer.published_at.desc().nullslast(),JobOffer.id.desc())))
        total_offer_count=len(offers)
        company_names={c.id:c.name for c in companies}
        data=[]
        for o in offers:
            if not is_france_job(o.country,o.location):continue
            try: words=json.loads(o.relevance_keywords or "[]")
            except ValueError: words=[]
            data.append({"Title":o.title,"Company":company_names.get(o.company_id,""),"Location":o.location or "","Remote":o.remote_policy or "","Type":o.employment_type or "","Potential relevance":o.is_potentially_relevant,"Keywords":", ".join(words),"Published date":o.published_at.isoformat() if o.published_at else "","Source":o.source_type or "","Job URL":o.url})
    if not data:
        message="Aucune offre collectée pour le moment." if total_offer_count==0 else "Aucune offre enregistrée n’est explicitement localisée en France. Les postes étrangers ou sans localisation vérifiable sont masqués."
        st.info(message); return
    company_filter=st.multiselect("Company",sorted({r["Company"] for r in data}))
    location_filter=st.text_input("Location contains")
    remote_filter=st.selectbox("Remote",["All","Remote","Not remote"])
    keyword_filter=st.text_input("Keyword")
    type_filter=st.multiselect("Employment type",sorted({r["Type"] for r in data if r["Type"]}))
    filtered=[r for r in data if (not company_filter or r["Company"] in company_filter) and location_filter.casefold() in r["Location"].casefold() and (remote_filter=="All" or (r["Remote"] and remote_filter=="Remote") or (not r["Remote"] and remote_filter=="Not remote")) and keyword_filter.casefold() in (r["Title"]+" "+r["Keywords"]+" "+r["Location"]).casefold() and (not type_filter or r["Type"] in type_filter)]
    st.dataframe(filtered,width="stretch",hide_index=True,column_config={"Job URL":st.column_config.LinkColumn("Job URL",display_text="Open offer")})
