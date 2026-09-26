"""Company discovery from optional search/public-list sources plus manual input."""
from __future__ import annotations
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database.models import Company
from config import get_settings
from discovery.base import CompanyCandidate, DiscoverySource
from discovery.public_lists import PublicListSource
from discovery.web_search import WebSearchSource
from utils.company_names import normalize_company_name
from utils.urls import is_public_web_domain, is_third_party_domain, normalize_domain, normalize_url

logger = logging.getLogger(__name__)
OFFICIAL_DOMAIN_THRESHOLD = 0.75


def _queries(job_titles: list[str], locations: list[str], company_types: list[str], sectors: list[str], maximum: int = 6) -> list[str]:
    roles = job_titles or ["companies"]
    verticals = sectors or [""]
    types = company_types or [""]
    places = locations or [""]
    queries: list[str] = []
    for role in roles:
        for vertical in verticals:
            for company_type in types:
                for place in places:
                    words = [word for word in (vertical, company_type, role, place) if word]
                    query = " ".join(words)
                    if query and query not in queries:
                        queries.append(query)
                    if len(queries) >= maximum:
                        return queries
    return queries


def _verified_official(candidate: CompanyCandidate, timeout: float = 12) -> tuple[str, str, float] | None:
    raw = candidate.website_url
    normalized = normalize_url(raw)
    if not normalized:
        return None
    domain = normalize_domain(normalized)
    if not domain or not is_public_web_domain(domain) or is_third_party_domain(domain):
        return None
    # Search results that point to deep article pages are not promoted to official domains.
    path = urlsplit(normalized).path.strip("/")
    if candidate.source_type == "web_search" and path.count("/") > 1:
        return None
    try:
        response = httpx.get(normalized, timeout=timeout, follow_redirects=True, headers={"User-Agent": "PostuleAgentCompanyResearch/1.0"})
        response.raise_for_status()
        final = normalize_url(str(response.url))
        final_domain = normalize_domain(final)
        if not final_domain or is_third_party_domain(final_domain) or "html" not in response.headers.get("content-type", "text/html").lower():
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        expected = normalize_company_name(candidate.company_name)
        observed = normalize_company_name(title)
        if observed and (observed == expected or expected in observed or observed in expected):
            confidence = 0.96
        else:
            name_terms = [word for word in expected.split() if len(word) > 2]
            domain_terms = final_domain.replace("-", ".").split(".")
            confidence = 0.85 if name_terms and any(term in domain_terms for term in name_terms) else 0.60
        if confidence < OFFICIAL_DOMAIN_THRESHOLD:
            logger.info("[DOMAIN] Name/title mismatch; refusing to label %s official (confidence=%.2f)", final_domain, confidence)
            return final_domain, final or f"https://{final_domain}/", confidence
        logger.info("[DOMAIN] Official domain candidate: %s confidence=%.2f", final_domain, confidence)
        return final_domain, final or f"https://{final_domain}/", confidence
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("[DOMAIN] Candidate site unavailable (%s)", type(exc).__name__)
        return None


def _existing(session: Session, domain: str, website: str, normalized_name: str) -> Company | None:
    # Priority: normalized domain, normalized official URL, then exact normalized name.
    found = session.scalar(select(Company).where(Company.domain == domain))
    if found:
        return found
    # Normalize legacy and manually created domain/URL values before name lookup.
    for existing in session.scalars(select(Company)):
        if normalize_domain(existing.domain) == domain:
            return existing
        if normalize_domain(existing.website_url or existing.website) == domain:
            return existing
    found = session.scalar(select(Company).where(Company.website == website))
    if found:
        return found
    if normalized_name:
        found = session.scalar(select(Company).where(Company.normalized_name == normalized_name))
        if found:
            return found
        # Backfill-safe exact name matching for phase-1/2 company rows.
        for legacy in session.scalars(select(Company).where(Company.normalized_name.is_(None))):
            if normalize_company_name(legacy.name) == normalized_name:
                return legacy
    return None


class CompanyDiscoveryAgent:
    def __init__(self, sources: Iterable[DiscoverySource] | None = None, *, timeout: float = 12, max_concurrency: int = 3):
        settings = get_settings()
        self.sources = list(sources) if sources is not None else [
            WebSearchSource(endpoint=settings.company_search_api_url, api_key=settings.company_search_api_key),
            PublicListSource(path=settings.company_public_lists_path),
        ]
        self.timeout = timeout
        self.max_concurrency = max(1, min(max_concurrency, 3))
        self.last_elapsed_seconds = 0.0

    def discover(self, session: Session, *, job_titles: list[str], locations: list[str], company_types: list[str], sectors: list[str], max_companies: int = 10, manual_candidates: list[CompanyCandidate] | None = None) -> list[Company]:
        discovery_started = time.perf_counter()
        max_companies = max(1, min(int(max_companies), 50))
        queries = _queries(job_titles, locations, company_types, sectors)
        candidates: list[CompanyCandidate] = list(manual_candidates or [])
        for query in queries:
            logger.info("[DISCOVERY] Query: %s", query)
            for source in self.sources:
                try:
                    candidates.extend(source.search(query, max_companies))
                except Exception as exc:
                    logger.warning("[DISCOVERY] Source failed (%s)", type(exc).__name__)
        # Stable exact-name/domain de-duplication before network checks.
        unique: list[CompanyCandidate] = []
        seen_domains: set[str] = set()
        seen_names: set[str] = set()
        for candidate in candidates:
            name_key = normalize_company_name(candidate.company_name)
            domain_key = normalize_domain(candidate.website_url)
            if not name_key or (domain_key and domain_key in seen_domains) or name_key in seen_names:
                continue
            if domain_key:
                seen_domains.add(domain_key)
            seen_names.add(name_key)
            unique.append(candidate)
        selected = unique[:max_companies]
        verified: list[tuple[CompanyCandidate, str, str, float]] = []
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {pool.submit(_verified_official, candidate, self.timeout): candidate for candidate in selected if candidate.website_url}
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    result = future.result()
                    if result and result[2] >= OFFICIAL_DOMAIN_THRESHOLD:
                        verified.append((candidate, *result))
                    elif result:
                        logger.info("[DOMAIN] Confidence below threshold; not labeling official")
                except Exception as exc:
                    logger.info("[DOMAIN] Verification failed (%s)", type(exc).__name__)
        companies: list[Company] = []
        for candidate, domain, website, confidence in verified:
            name_norm = normalize_company_name(candidate.company_name)
            existing = _existing(session, domain, website, name_norm)
            if existing:
                logger.info("[DEDUP] Existing company detected: %s", existing.name)
                companies.append(existing)
                continue
            query = candidate.query or (queries[0] if queries else "manual")
            company = Company(
                name=candidate.company_name[:255], normalized_name=name_norm, domain=domain,
                website=website, website_url=website, location=candidate.location,
                sector=candidate.sector, company_type=candidate.company_type,
                description=candidate.description, linkedin_url=candidate.linkedin_url,
                source_url=candidate.source_url or website, source_type=candidate.source_type,
                discovery_query=query, discovered_at=datetime.now(timezone.utc),
                official_domain_confidence=confidence,
            )
            session.add(company)
            try:
                session.commit(); session.refresh(company)
            except IntegrityError:
                session.rollback()
                existing = _existing(session, domain, website, name_norm)
                if existing:
                    companies.append(existing)
                continue
            logger.info("[DB] Company saved")
            logger.info("[DISCOVERY] Candidate: %s", company.name)
            companies.append(company)
            if len(companies) >= max_companies:
                break
        self.last_elapsed_seconds = time.perf_counter() - discovery_started
        return companies[:max_companies]
