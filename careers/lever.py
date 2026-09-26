"""Public Lever postings API adapter."""
from .base import JobOfferData
from .generic import relevance_keywords
from utils.job_urls import normalize_job_url

def parse_lever(data):
    output=[]
    for item in data if isinstance(data,list) else []:
        title=item.get("text")
        if not title: continue
        categories=item.get("categories") or {}; location=categories.get("location")
        desc=(item.get("descriptionPlain") or item.get("description") or "")
        url=item.get("hostedUrl") or item.get("applyUrl")
        if not url: continue
        keys=relevance_keywords(title,desc)
        try: url=normalize_job_url(url)
        except ValueError: continue
        output.append(JobOfferData(title=title,location=location,department=categories.get("team"),employment_type=categories.get("commitment"),description=desc or None,job_url=url,source_type="lever",external_id=str(item.get("id")) if item.get("id") else None,is_potentially_relevant=bool(keys),relevance_keywords=keys))
    return output
