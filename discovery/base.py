"""Interfaces and records for pluggable company-discovery sources."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CompanyCandidate:
    company_name: str
    website_url: str | None = None
    location: str | None = None
    sector: str | None = None
    company_type: str | None = None
    description: str | None = None
    linkedin_url: str | None = None
    source_url: str | None = None
    source_type: str = "manual"
    source_title: str = ""
    query: str = ""


class DiscoverySource(Protocol):
    def search(self, query: str, limit: int) -> list[CompanyCandidate]: ...
