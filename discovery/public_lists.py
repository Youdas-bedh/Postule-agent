"""Optional JSON public-list source configurable via COMPANY_PUBLIC_LISTS_PATH."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from discovery.base import CompanyCandidate

logger = logging.getLogger(__name__)


class PublicListSource:
    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("COMPANY_PUBLIC_LISTS_PATH", "")) if (path or os.getenv("COMPANY_PUBLIC_LISTS_PATH")) else None

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        if not self.path or not self.path.is_file():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[DISCOVERY] Public list unavailable (%s)", type(exc).__name__)
            return []
        if not isinstance(rows, list):
            return []
        terms = {word.casefold() for word in query.split() if len(word) > 2}
        result = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("company_name"):
                continue
            blob = " ".join(str(row.get(key, "")) for key in ("company_name", "sector", "company_type", "location", "description")).casefold()
            if terms and not any(term in blob for term in terms):
                continue
            result.append(CompanyCandidate(
                company_name=str(row["company_name"]), website_url=row.get("website_url"),
                location=row.get("location"), sector=row.get("sector"),
                company_type=row.get("company_type"), description=row.get("description"),
                linkedin_url=row.get("linkedin_url"), source_url=row.get("source_url"),
                source_type="public_list", query=query,
            ))
            if len(result) >= limit:
                break
        return result
