"""Optional configurable search adapter; expects JSON {results:[{title,url,snippet}]}.

No search credentials or vendor are hardcoded. Configure an endpoint compatible
with this small contract using COMPANY_SEARCH_API_URL and optional API key.
"""
from __future__ import annotations
import logging
import os
import httpx
from discovery.base import CompanyCandidate
from utils.urls import is_third_party_domain

logger = logging.getLogger(__name__)


class WebSearchSource:
    def __init__(self, endpoint: str | None = None, api_key: str | None = None, timeout: float = 12):
        self.endpoint = endpoint if endpoint is not None else os.getenv("COMPANY_SEARCH_API_URL", "").strip()
        self.api_key = api_key if api_key is not None else os.getenv("COMPANY_SEARCH_API_KEY", "")
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        if not self.endpoint:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = httpx.get(self.endpoint, params={"q": query, "limit": limit}, headers=headers, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("[DISCOVERY] Search API unavailable (%s)", type(exc).__name__)
            return []
        results = body.get("results", []) if isinstance(body, dict) else []
        candidates: list[CompanyCandidate] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            title, url = str(item.get("title", "")).strip(), str(item.get("url", "")).strip()
            if not title or not url:
                continue
            # Search result pages/aggregators are evidence, never official websites.
            candidates.append(CompanyCandidate(
                company_name=title.split(" | ")[0].split(" — ")[0].strip(),
                website_url=None if is_third_party_domain(url) else url,
                location=str(item.get("location", "")).strip() or None,
                sector=str(item.get("sector", "")).strip() or None,
                company_type=str(item.get("company_type", "")).strip() or None,
                description=str(item.get("snippet", ""))[:1200] or None,
                linkedin_url=str(item.get("linkedin_url", "")).strip() or None,
                source_url=url, source_type="web_search", source_title=title, query=query,
            ))
        return candidates
