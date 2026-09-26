"""LLM-suggested company names, verified against public web results."""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from discovery.base import CompanyCandidate
from llm.base import LLMProvider
from utils.company_names import normalize_company_name
from utils.urls import is_public_web_domain, is_third_party_domain, normalize_domain

logger = logging.getLogger(__name__)
_GENERIC_NAMES = {"anonymous", "anonyme", "confidential", "company confidential", "stealth", "undisclosed", "unknown", "entreprise inconnue", "mistral ai"}


class _CompanyNames(BaseModel):
    companies: list[str] = Field(min_length=1, max_length=30)


def _parse_names(raw: str, limit: int) -> list[str]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = _CompanyNames.model_validate(json.loads(text))
    except (ValueError, ValidationError, TypeError) as exc:
        raise ValueError("Le LLM n'a pas retourné une liste d'entreprises valide.") from exc
    names: list[str] = []
    seen: set[str] = set()
    for value in parsed.companies:
        name = " ".join(str(value).split()).strip(" -–—,.;")
        normalized = normalize_company_name(name)
        if not name or len(name) < 2 or normalized in _GENERIC_NAMES or normalized in seen:
            continue
        if any(word in normalized.split() for word in ("anonymous", "confidential", "undisclosed", "stealth")):
            continue
        seen.add(normalized)
        names.append(name[:255])
        if len(names) >= limit:
            break
    return names


def _name_tokens(name: str) -> set[str]:
    ignored = {"sas", "sa", "sarl", "inc", "ltd", "groupe", "group", "the", "company", "france", "ai"}
    return {part for part in normalize_company_name(name).split() if len(part) >= 4 and part not in ignored}


class LLMCompanySource:
    """Ask the configured local LLM for names; never trust model-provided URLs."""

    def __init__(self, provider: LLMProvider, *, search_factory=None, timeout: int = 8):
        self.provider = provider
        self.search_factory = search_factory
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        prompt = (
            "Suggest real, named companies that operate in the requested field and have a presence in France. "
            "Return only JSON: {\"companies\":[\"official company name\"]}. "
            "Do not include anonymous, confidential, stealth, hypothetical, or unverified organizations. "
            "Do not invent websites, hiring status, contacts, or job openings. Return up to the requested count.\n"
            f"Request: {query}\nCount: {max(1, min(limit, 20))}"
        )
        raw = self.provider.generate(
            prompt,
            system_prompt="You are a candidate generator, not a source of current facts. Return only named organizations, with no URLs or claims.",
            max_tokens=min(1200, 100 + max(1, limit) * 45),
            json_mode=True,
        )
        names = _parse_names(raw, max(1, min(limit, 20)))
        if not names:
            return []
        if self.search_factory is None:
            from ddgs import DDGS

            search = DDGS(timeout=self.timeout)
        else:
            search = self.search_factory()
        candidates: list[CompanyCandidate] = []
        try:
            for name in names:
                try:
                    results = search.text(f'"{name}" site officiel France entreprise', region="fr-fr", max_results=5, backend="auto")
                except Exception as exc:
                    logger.info("[DISCOVERY] Official-site search failed (%s)", type(exc).__name__)
                    continue
                tokens = _name_tokens(name)
                for item in results or []:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("href") or item.get("url") or "").strip()
                    host = normalize_domain(url)
                    if not host or not is_public_web_domain(host) or is_third_party_domain(url):
                        continue
                    title = str(item.get("title") or "").strip()
                    body = str(item.get("body") or item.get("snippet") or "").strip()
                    evidence = normalize_company_name(f"{title} {body} {host}")
                    if tokens and not any(token in evidence.split() for token in tokens):
                        continue
                    root = f"https://{host}/"
                    candidates.append(CompanyCandidate(
                        company_name=name,
                        website_url=root,
                        location="France",
                        sector=query[:255],
                        description=body[:1200] or None,
                        source_url=url,
                        source_type="llm_web_search",
                        source_title=title,
                        query=query,
                    ))
                    break
        finally:
            close = getattr(search, "close", None)
            if close:
                close()
        return candidates
