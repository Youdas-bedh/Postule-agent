"""Public Greenhouse board API adapter."""
from .base import JobOfferData
from .generic import relevance_keywords
from utils.job_urls import normalize_job_url

def parse_greenhouse(data, board, page_url):
    output = []
    for item in data.get("jobs", []):
        title = item.get("title")
        if not title: continue
        url = item.get("absolute_url") or page_url
        content = str(item.get("content", ""))
        keys = relevance_keywords(title, content)
        try: url = normalize_job_url(url)
        except ValueError: continue
        output.append(JobOfferData(title=title, location=(item.get("location") or {}).get("name"), description=content or None, job_url=url, source_type="greenhouse", external_id=str(item.get("id")) if item.get("id") else None, is_potentially_relevant=bool(keys), relevance_keywords=keys))
    return output
