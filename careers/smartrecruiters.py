"""Public SmartRecruiters postings API adapter."""
from .base import JobOfferData
from .generic import relevance_keywords
from utils.job_urls import normalize_job_url

def parse_smartrecruiters(data):
    out=[]
    for item in data.get("content",[]):
        title=item.get("name")
        if not title: continue
        loc=item.get("location") or {}; url=item.get("ref") or ""
        if not url and item.get("id"): url=f"https://jobs.smartrecruiters.com/{data.get('company',{}).get('identifier','')}/{item['id']}"
        if not url: continue
        keys=relevance_keywords(title)
        try: url=normalize_job_url(url)
        except ValueError: continue
        out.append(JobOfferData(title=title,location=loc.get("fullLocation") or loc.get("city"),country=loc.get("country"),department=(item.get("department") or {}).get("label"),job_url=url,source_type="smartrecruiters",external_id=str(item.get("id")) if item.get("id") else None,is_potentially_relevant=bool(keys),relevance_keywords=keys))
    return out
