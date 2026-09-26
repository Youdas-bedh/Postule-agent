"""Fast, read-only global pipeline dashboard."""
from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session, sessionmaker

from services.dashboard_service import dashboard_data, filter_dashboard_rows, csv_bytes, xlsx_bytes


def render_dashboard(factory: sessionmaker[Session]) -> None:
    st.header("Dashboard")
    st.caption("Vue d’historique de toutes les données enregistrées. Les nouvelles offres utilisées par Career & Jobs, Job Matching et Applications sont limitées aux postes localisés en France.")
    # Snapshot once per render; this page only reads saved database state.
    with factory() as session:
        data = dashboard_data(session)
    counts = data["counts"]
    metrics = [
        ("Entreprises découvertes", "companies_discovered"), ("Entreprises analysées", "companies_analyzed"),
        ("Pages carrière", "career_pages_found"), ("Offres extraites", "offers_extracted"),
        ("Offres avec matching", "offers_with_matching"), ("Contacts trouvés", "contacts_found"),
        ("Contacts VERIFIED", "contacts_verified"), ("DRAFT_READY", "applications_draft_ready"),
        ("APPROVED", "applications_approved"), ("REJECTED", "applications_rejected"),
        ("IGNORED", "applications_ignored"),
        ("Ready to send", "ready_to_send"), ("Sent today", "sent_today"),
        ("Sent total", "sent_total"), ("Failed", "send_failed"),
        ("Skipped", "send_skipped"), ("Quota restante", "remaining_daily_quota"),
    ]
    for start in range(0, len(metrics), 4):
        columns = st.columns(4)
        for col, (label, key) in zip(columns, metrics[start:start + 4]):
            col.metric(label, counts[key])

    rows = data["dashboard"]
    companies = sorted({r["company"] for r in rows if r.get("company")})
    statuses = sorted({r["application_status"] for r in rows if r.get("application_status")})
    locations = sorted({r["location"] for r in rows if r.get("location")})
    channels = sorted({r["application_channel"] for r in rows if r.get("application_channel")})
    verifications = sorted({r["contact_verification"] for r in rows if r.get("contact_verification")})
    st.subheader("Pipeline")
    with st.expander("Filtres", expanded=False):
        c1, c2, c3 = st.columns(3)
        selected_companies = c1.multiselect("Entreprise", companies)
        max_score = max([int(r["job_match_score"]) for r in rows if r.get("job_match_score") is not None] or [100])
        min_score = c2.slider("Score minimum", 0, max(100, max_score), 0)
        selected_statuses = c3.multiselect("Statut", statuses)
        c4, c5, c6 = st.columns(3)
        location_filter = c4.text_input("Localisation contient")
        selected_channels = c5.multiselect("Canal", channels)
        selected_verifications = c6.multiselect("Vérification contact", verifications)
        c7, c8 = st.columns(2)
        start_date = c7.date_input("Date à partir du", value=None)
        end_date = c8.date_input("Date jusqu’au", value=None)
    if isinstance(start_date, tuple): start_date = start_date[0] if start_date else None
    if isinstance(end_date, tuple): end_date = end_date[-1] if end_date else None
    filtered = filter_dashboard_rows(rows, companies=selected_companies, min_score=min_score,
        statuses=selected_statuses, location=location_filter, channels=selected_channels,
        contact_verifications=selected_verifications, start_date=start_date, end_date=end_date)
    display_fields = ["company", "sector", "location", "company_relevance_score", "job_title", "job_match_score",
        "contact", "contact_confidence", "application_channel", "selected_cv", "application_status", "application_date", "last_updated"]
    labels = {"company":"Entreprise", "sector":"Secteur", "location":"Localisation", "company_relevance_score":"Company relevance score",
        "job_title":"Offre", "job_match_score":"Job match score", "contact":"Contact", "contact_confidence":"Contact confidence",
        "application_channel":"Canal", "selected_cv":"Selected CV", "application_status":"Application status",
        "application_date":"Application date", "last_updated":"Last updated"}
    st.dataframe(pd.DataFrame([{labels[k]: r.get(k) for k in display_fields} for r in filtered]), width="stretch", hide_index=True)

    st.subheader("Vues utiles")
    left, right = st.columns(2)
    with left:
        st.markdown("**Top job matches**")
        st.dataframe(pd.DataFrame(sorted((r for r in rows if r.get("job_match_score") is not None), key=lambda r:r["job_match_score"], reverse=True)[:10])[ ["company", "job_title", "job_match_score", "job_url"] ] if any(r.get("job_match_score") is not None for r in rows) else pd.DataFrame(columns=["company","job_title","job_match_score","job_url"]), hide_index=True, width="stretch")
        st.markdown("**Contacts vérifiés**")
        st.dataframe(pd.DataFrame([r for r in data["contacts"] if r["verification_status"] == "VERIFIED"])[["company", "contact_name", "contact_role", "contact_email", "contact_confidence"]] if any(r["verification_status"] == "VERIFIED" for r in data["contacts"]) else pd.DataFrame(columns=["company","contact_name","contact_role","contact_email","contact_confidence"]), hide_index=True, width="stretch")
        st.markdown("**Candidatures à valider**")
        st.dataframe(pd.DataFrame([r for r in data["applications"] if r["status"] == "DRAFT_READY"]), hide_index=True, width="stretch")
    with right:
        st.markdown("**Entreprises sans canal fiable**")
        no_channel = [r for r in data["companies"] if not r["application_channel"] or r["application_channel"] in {"UNKNOWN", "NONE"}]
        st.dataframe(pd.DataFrame(no_channel, columns=["company", "sector", "location", "career_url"]), hide_index=True, width="stretch")
        st.markdown("**Offres récemment découvertes**")
        st.dataframe(pd.DataFrame(data["job_offers"][:10]), hide_index=True, width="stretch")

    st.subheader("Historique des candidatures")
    st.dataframe(pd.DataFrame(data["events"], columns=["application_id", "company", "event", "timestamp", "details"]), hide_index=True, width="stretch")

    st.subheader("Exports")
    csv_labels = {"companies":"Companies CSV", "job_offers":"Job offers CSV", "contacts":"Contacts CSV", "applications":"Applications CSV"}
    columns = st.columns(4)
    for col, dataset in zip(columns, csv_labels):
        with col:
            col.download_button(csv_labels[dataset], csv_bytes(data[dataset], dataset), f"{dataset}.csv", "text/csv", key=f"dashboard_csv_{dataset}")
    st.download_button("Télécharger le classeur Excel", xlsx_bytes(data), "postule-agent-export.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dashboard_xlsx")
