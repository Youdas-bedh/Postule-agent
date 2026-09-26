"""Career page scan workflow and additive offer persistence."""
from __future__ import annotations
import json, logging, re
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from database.models import Company, JobOffer
from careers.base import CareerScanResult, JobOfferData
from scrapers.careers import CareerScraper
from utils.job_urls import normalize_job_url

logger=logging.getLogger(__name__)
def _key(value): return re.sub(r"\W+"," ",(value or "").casefold()).strip()
def persist_offers(session:Session, company:Company, offers:list[JobOfferData])->tuple[int,int]:
    existing=list(session.scalars(select(JobOffer).where(JobOffer.company_id==company.id)))
    added=updated=0
    for offer in offers:
        url=normalize_job_url(offer.job_url)
        row=next((x for x in existing if offer.external_id and x.external_id==offer.external_id and x.source_type==offer.source_type),None)
        row=row or next((x for x in existing if _safe_url(x.url)==url),None)
        row=row or next((x for x in existing if _key(x.title)==_key(offer.title) and _key(x.location)==_key(offer.location)),None)
        values={"title":offer.title,"url":url,"description":offer.description,"location":offer.location,"country":offer.country,"remote_policy":offer.remote_policy,"employment_type":offer.employment_type,"experience_level":offer.experience_level,"department":offer.department,"requirements":json.dumps(offer.requirements,ensure_ascii=False),"skills":json.dumps(offer.skills,ensure_ascii=False),"salary_min":offer.salary_min,"salary_max":offer.salary_max,"salary_currency":offer.salary_currency,"source_type":offer.source_type,"external_id":offer.external_id,"published_at":offer.published_at,"is_potentially_relevant":offer.is_potentially_relevant,"relevance_keywords":json.dumps(offer.relevance_keywords,ensure_ascii=False)}
        if row is None:
            row=JobOffer(company_id=company.id,match_score=None,discovered_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc),**values)
            session.add(row); existing.append(row); added+=1; logger.info("[DEDUP] New offer stored")
        else:
            changed=False
            for field,value in values.items():
                # Once an ATS identifier is known, a URL-based duplicate with a
                # different/missing identifier must not churn the stable key.
                if field == "external_id" and row.external_id:
                    continue
                if value is not None and getattr(row,field)!=value:
                    setattr(row,field,value); changed=True
            if changed:
                row.updated_at=datetime.now(timezone.utc); updated+=1; logger.info("[DEDUP] Existing job updated")
    session.flush()
    return added,updated
def _safe_url(value):
    try:return normalize_job_url(value)
    except ValueError:return value

class CareerAgent:
    def __init__(self,scraper:CareerScraper|None=None,cache_hours:int=24):
        self.scraper=scraper or CareerScraper(); self.cache=timedelta(hours=cache_hours)
    def scan_company(self,session:Session,company:Company,force:bool=False)->CareerScanResult:
        now=datetime.now(timezone.utc)
        last=company.career_last_scanned_at
        if last and last.tzinfo is None:last=last.replace(tzinfo=timezone.utc)
        if not force and last and now-last<self.cache:
            offers=list(session.scalars(select(JobOffer).where(JobOffer.company_id==company.id)))
            return CareerScanResult(career_url=company.career_url,ats_type=offers[0].source_type if offers else None,status="cached",jobs=[self._to_data(x) for x in offers])
        logger.info("[CAREER] Searching career page for %s",company.name)
        company.career_scan_status="running"; session.flush()
        result=self.scraper.scan(company.website_url or company.website,company.career_url)
        if result.career_url: company.career_url=result.career_url
        company.career_last_scanned_at=now; company.career_scan_status=result.status
        if result.status in {"complete","empty"}:
            added,updated=persist_offers(session,company,result.jobs)
            logger.info("[JOBS] Found %d jobs; %d potentially relevant",len(result.jobs),sum(j.is_potentially_relevant for j in result.jobs))
            logger.info("[DB] Saved %d job offers (%d updated)",added,updated)
        session.commit()
        return result
    @staticmethod
    def _to_data(row):
        def arr(raw:str|None)->list[str]:
            try:value=json.loads(raw or "[]")
            except (ValueError,TypeError):return []
            return [str(item) for item in value if isinstance(item,(str,int,float))] if isinstance(value,list) else []
        return JobOfferData(title=row.title,location=row.location,country=row.country,remote_policy=row.remote_policy,employment_type=row.employment_type,experience_level=row.experience_level,department=row.department,description=row.description,requirements=arr(row.requirements),skills=arr(row.skills),salary_min=row.salary_min,salary_max=row.salary_max,salary_currency=row.salary_currency,job_url=row.url,source_type=row.source_type or "database",external_id=row.external_id,published_at=row.published_at,is_potentially_relevant=row.is_potentially_relevant,relevance_keywords=arr(row.relevance_keywords))
