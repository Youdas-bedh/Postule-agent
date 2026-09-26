"""Public Ashby job board API adapter."""
from .base import JobOfferData
from .generic import relevance_keywords
from utils.job_urls import normalize_job_url
def parse_ashby(data):
    out=[]
    for item in data.get("jobs",[]):
        title=item.get("title"); url=item.get("jobUrl") or item.get("applyUrl")
        if not title or not url:continue
        text=str(item.get("descriptionPlain","")); keys=relevance_keywords(title,text)
        try:url=normalize_job_url(url)
        except ValueError:continue
        out.append(JobOfferData(title=title,location=item.get("location"),department=item.get("department"),employment_type=item.get("employmentType"),description=text or None,job_url=url,source_type="ashby",external_id=str(item.get("id")) if item.get("id") else None,published_at=item.get("publishedAt"),is_potentially_relevant=bool(keys),relevance_keywords=keys))
    return out
