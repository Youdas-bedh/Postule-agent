"""Review publicly discoverable, source-backed company contacts."""
from __future__ import annotations
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from agents.contact_discovery import ContactDiscoveryAgent
from config import get_settings
from database.models import Company, Contact

def render_contacts(factory: sessionmaker[Session]) -> None:
    st.header("Contacts")
    st.caption("Informations professionnelles publiques uniquement. Aucun message n’est rédigé ou envoyé ; une adresse déduite n’est jamais considérée comme vérifiée.")
    with factory() as session:
        companies=list(session.scalars(select(Company).order_by(Company.name)))
        ids=[c.id for c in companies]
        data={c.id:(c.name,c.domain,c.preferred_application_channel,c.contacts_scan_status,c.application_form_url,c.best_contact_id,c.best_contact_reason,c.contacts_last_scanned_at) for c in companies}
        contacts=list(session.scalars(select(Contact).where(Contact.company_id.in_(ids)).order_by(Contact.company_id,Contact.confidence_score.desc()))) if ids else []
    if not companies:
        st.info("Aucune entreprise enregistrée. Ajoute-en dans Company Discovery."); return
    selected=st.multiselect("Entreprise",[c.name for c in companies])
    term=st.text_input("Mot-clé (nom, rôle ou entreprise)")
    statuses=["Tous","VERIFIED","PROBABLE","UNVERIFIED"]
    status_filter=st.selectbox("Vérification",statuses)
    sources=sorted({c.source_type for c in contacts if c.source_type})
    source_filter=st.selectbox("Source",["Toutes",*sources])
    min_confidence=st.slider("Confiance minimale",0,100,0)
    company_lookup={c.name:c.id for c in companies}
    chosen={company_lookup[n] for n in selected} if selected else set(ids)
    visible=[c for c in contacts if c.company_id in chosen and term.casefold() in " ".join((c.name or "",c.role or "",data[c.company_id][0])).casefold() and (status_filter=="Tous" or c.verification_status==status_filter) and (source_filter=="Toutes" or c.source_type==source_filter) and (c.confidence_score or 0)>=min_confidence]
    col1,col2,col3=st.columns(3)
    col1.metric("Entreprises",len(chosen)); col2.metric("Contacts publics",len(visible)); col3.metric("Vérifiés",sum(c.verification_status=="VERIFIED" for c in visible))
    for company in companies:
        if company.id not in chosen: continue
        name,domain,channel,status,form,best_id,reason,last=data[company.id]
        with st.container(border=True):
            st.subheader(name)
            st.caption(f"Domaine : {domain or '—'} · Dernier scan : {last or 'jamais'} · Statut : {status or 'jamais scanné'} · Canal recommandé : {channel or 'NO_RELIABLE_CONTACT'}")
            left,right=st.columns(2)
            with left:
                if st.button("Find contacts",key=f"contacts_scan_{company.id}",type="primary"):
                    _scan(factory,company.id,False);st.rerun()
            with right:
                if st.button("Force rescan",key=f"contacts_force_{company.id}"):
                    _scan(factory,company.id,True);st.rerun()
            rows=[c for c in visible if c.company_id==company.id]
            if reason: st.caption("Meilleur contact : "+reason)
            if form: st.link_button("Ouvrir le formulaire carrière",form)
            if rows:
                for contact in rows:
                    state=contact.verification_status or "UNVERIFIED"
                    threshold=get_settings().contact_min_confidence
                    label=f"{contact.name or contact.email or 'Contact sans nom'} · {contact.role or 'Rôle non précisé'} · {state} · confiance {contact.confidence_score or 0}/100"
                    with st.expander(label):
                        if state!="VERIFIED" or (contact.confidence_score or 0)<threshold:
                            st.warning("Contact non vérifié ou sous le seuil de confiance ; ne pas traiter comme une adresse fiable.")
                        if contact.email: st.write("Email public :",contact.email)
                        if contact.linkedin_url: st.link_button("Profil LinkedIn public",contact.linkedin_url)
                        st.write(f"Source : [{contact.source_type}]({contact.source_url})")
                        st.write(f"Domaine correspondant : {contact.email_domain_match if contact.email_domain_match is not None else 'N/A'} · MX : {contact.email_mx_status or 'non vérifié'} · Jetable : {contact.email_is_disposable if contact.email_is_disposable is not None else 'N/A'}")
            elif not status: st.info("Aucun scan effectué.")
            else: st.info("Aucun contact public n’a été retenu.")

def _scan(factory:sessionmaker[Session],company_id:int,force:bool)->None:
    with factory() as session:
        company=session.get(Company,company_id)
        if company is None:return
        result=ContactDiscoveryAgent().find_contacts(session,company,force=force)
        st.session_state[f"contacts_result_{company_id}"]=result.model_dump()
        st.toast(f"Scan terminé : {result.contacts_found} contact(s), canal {result.preferred_channel}.")
