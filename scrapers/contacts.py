"""Bounded extraction from company-owned and explicitly linked career pages."""
from __future__ import annotations
import json, logging, re, time
from urllib.parse import urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from database.models import Company, JobOffer
from careers.base import detect_ats
from agents.contact_verifier import classify_contact_role, extract_emails, validate_email
from utils.urls import is_public_web_domain, normalize_domain, normalize_url

logger=logging.getLogger(__name__)
USER_AGENT="PostuleAgentPublicContactResearch/1.0"
LINK_TERMS=("career","job","recruit","talent","people","human resources","contact","team","join","apply","candidature","postuler","spontanée","formulaire","mentions légales")
FORM_TERMS=("spontaneous application","unsolicited application","general application","talent community","candidature spontanée","candidature libre","rejoindre notre vivier","join our talent community")
EMAIL_CONTEXT_TERMS=("recruitment","recruiting","careers","career","jobs","talent","human resources"," hr "," rh ","recrutement","recruteur")

class PublicContact(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra="ignore")
    name:str|None=None; role:str|None=None; email:str|None=None; source_url:str
    source_type:str; confidence_score:int=Field(ge=0,le=100); verification_status:str
    linkedin_url:str|None=None; email_domain_match:bool|None=None; email_mx_status:str|None=None; email_is_disposable:bool|None=None

class ContactScrapeResult(BaseModel):
    contacts:list[PublicContact]=Field(default_factory=list)
    application_form_url:str|None=None
    status:str="complete"
    requests:int=0
    pages_scanned:int=0
    elapsed_seconds:float=0.0
    errors:list[str]=Field(default_factory=list)

def _walk_people(value):
    if isinstance(value,list):
        for item in value:yield from _walk_people(item)
    elif isinstance(value,dict):
        kind=value.get("@type",[]);kind=[kind] if isinstance(kind,str) else kind
        if "Person" in kind:yield value
        for key in ("@graph","mainEntity","employee","employees","itemListElement"):
            if key in value:yield from _walk_people(value[key])

def _email_role(email:str,context:str)->str|None:
    local=email.split("@",1)[0].casefold()
    if local.startswith(("recruitment","recruiting","jobs","careers","career","talent","rh","hr")):return "Recruitment"
    return classify_contact_role(context) or ("Public company contact" if local in {"info","hello","contact","office","support"} else None)

def _source_type(url:str,company_domain:str,career_domains:set[str],job_urls:set[str])->tuple[str,int]:
    host=normalize_domain(url)
    if normalize_url(url) in job_urls:return "OFFICIAL_JOB_POSTING",90
    if host in career_domains:return "OFFICIAL_CAREERS",90
    if host==company_domain or host and host.endswith("."+company_domain):
        path=urlsplit(url).path.casefold()
        if any(token in path for token in ("career","job","talent","recruit","recrut","join","emploi")):return "OFFICIAL_CAREERS",90
        return "OFFICIAL_WEBSITE",100
    return "OFFICIAL_CAREERS",90

class ContactScraper:
    def __init__(self,*,timeout:float=8,max_pages:int=10,client=None):
        self.timeout=timeout;self.max_pages=max(1,max_pages);self.client=client;self.request_count=0
    def _get(self,url):
        self.request_count+=1
        response=(self.client.get(url,timeout=self.timeout,follow_redirects=True,headers={"User-Agent":USER_AGENT}) if self.client else httpx.get(url,timeout=self.timeout,follow_redirects=True,headers={"User-Agent":USER_AGENT}))
        response.raise_for_status();return response
    def scan(self,session:Session,company:Company)->ContactScrapeResult:
        started=time.perf_counter();before=self.request_count;errors=[];contacts=[];forms=[];visited=set();queue=[]
        website=normalize_url(company.website_url or company.website);company_domain=normalize_domain(company.domain or website)
        if not website or not company_domain:return ContactScrapeResult(status="invalid_company_domain")
        career_url=normalize_url(company.career_url) if company.career_url else None
        offers=list(session.scalars(select(JobOffer).where(JobOffer.company_id==company.id).order_by(JobOffer.match_score.desc().nullslast(),JobOffer.is_potentially_relevant.desc(),JobOffer.id.desc()).limit(3)))
        job_urls={normalize_url(o.url) for o in offers if normalize_url(o.url) and (normalize_domain(o.url)==company_domain or detect_ats(o.url))}
        career_domains={normalize_domain(x) for x in [career_url,*job_urls] if x and normalize_domain(x)!=company_domain}
        allowed_hosts={company_domain,*career_domains}
        seeds=[website]
        if career_url:seeds.insert(0,career_url)
        seeds.extend(sorted(job_urls))
        queue.extend(seeds)
        first_party_roots={company_domain}
        def allowed(url:str)->bool:
            host=normalize_domain(url)
            if not host or not is_public_web_domain(host) or "linkedin.com" in host:return False
            return any(host==root or host.endswith("."+root) for root in first_party_roots) or host in allowed_hosts
        while queue and len(visited)<self.max_pages:
            requested=queue.pop(0);canonical=normalize_url(requested)
            if not canonical or canonical in visited or not allowed(canonical):continue
            visited.add(canonical)
            try:
                response=self._get(canonical);resolved=str(response.url)
                if not allowed(resolved):continue
                if "html" not in response.headers.get("content-type","text/html").lower():continue
                soup=BeautifulSoup(response.text,"html.parser")
                text=soup.get_text(" ",strip=True)
                source_type,base_confidence=_source_type(resolved,company_domain,career_domains,job_urls)
                # JSON-LD people and visible email addresses are extracted only
                # from pages we have just fetched. LinkedIn URLs are recorded,
                # never fetched.
                for script in soup.select('script[type="application/ld+json"]'):
                    try:payload=json.loads(script.string or script.get_text())
                    except (ValueError,TypeError):continue
                    for person in _walk_people(payload):
                        name=person.get("name") if isinstance(person.get("name"),str) else None
                        role=person.get("jobTitle") if isinstance(person.get("jobTitle"),str) else None
                        email=person.get("email") if isinstance(person.get("email"),str) else None
                        person_url=person.get("url") or person.get("sameAs")
                        if isinstance(person_url,list):person_url=next((u for u in person_url if isinstance(u,str) and "linkedin.com/" in u),None)
                        linkedin=person_url if isinstance(person_url,str) and "linkedin.com/" in person_url and urlsplit(person_url).scheme in {"https","http"} else None
                        if email:
                            verification=validate_email(email,company_domain,source_type=source_type,confidence_score=base_confidence)
                            contacts.append(PublicContact(name=name,role=classify_contact_role(role) or role,email=verification.email,source_url=resolved,source_type=source_type,confidence_score=verification.confidence_score,verification_status=verification.verification_status,linkedin_url=linkedin,email_domain_match=verification.domain_match,email_mx_status=verification.mx_status,email_is_disposable=verification.disposable))
                        elif name and role:
                            contacts.append(PublicContact(name=name,role=classify_contact_role(role) or role,source_url=resolved,source_type=source_type,confidence_score=80,verification_status="PROBABLE",linkedin_url=linkedin))
                # mailto is an actual published address, just like visible text.
                email_context={}
                for anchor in soup.select('a[href^="mailto:"]'):
                    address=anchor.get("href","").split(":",1)[1].split("?",1)[0].strip()
                    parent=anchor
                    for _ in range(3):
                        if parent.parent:parent=parent.parent
                    context=parent.get_text(" ",strip=True)[:600]
                    for email in extract_emails(address):email_context[email]=context
                emails=set(extract_emails(text))|set(email_context)
                for email in emails:
                    context=email_context.get(email)
                    if not context:
                        at=text.casefold().find(email.casefold());context=text[max(0,at-140):at+len(email)+140] if at>=0 else ""
                    verification=validate_email(email,company_domain,source_type=source_type,confidence_score=base_confidence)
                    contacts.append(PublicContact(name=None,role=_email_role(email,context),email=verification.email,source_url=resolved,source_type=source_type,confidence_score=verification.confidence_score,verification_status=verification.verification_status,email_domain_match=verification.domain_match,email_mx_status=verification.mx_status,email_is_disposable=verification.disposable))
                # Only explicit career-form links or application forms are saved;
                # the scraper never submits any form.
                for anchor in soup.find_all("a",href=True):
                    label=(anchor.get_text(" ",strip=True)+" "+anchor["href"]).casefold()
                    if any(term in label for term in FORM_TERMS):forms.append(urljoin(resolved,anchor["href"]))
                for form in soup.find_all("form"):
                    context=(form.get("action","")+" "+form.get_text(" ",strip=True)+" "+" ".join(i.get("name","") for i in form.find_all(["input","textarea"]))).casefold()
                    if any(term in context for term in ("resume","résumé","cv","application","candidature","careers","jobs")):
                        action=form.get("action") or resolved;forms.append(urljoin(resolved,action))
                for anchor in soup.find_all("a",href=True):
                    href=urljoin(resolved,anchor["href"]);label=(anchor.get_text(" ",strip=True)+" "+anchor["href"]).casefold()
                    if any(term in label for term in FORM_TERMS):continue
                    if allowed(href) and any(term in label for term in LINK_TERMS):
                        canonical_link=normalize_url(href)
                        if canonical_link and canonical_link not in visited and canonical_link not in queue:queue.insert(0,canonical_link)
            except (httpx.TimeoutException,httpx.NetworkError) as exc:
                errors.append(type(exc).__name__);logger.info("[CONTACT] Page timed out or failed network (%s)",type(exc).__name__)
            except httpx.HTTPStatusError as exc:
                errors.append(f"HTTP_{exc.response.status_code}");logger.info("[CONTACT] Page returned HTTP %s",exc.response.status_code)
            except (ValueError,TypeError) as exc:
                errors.append(type(exc).__name__);logger.info("[CONTACT] Page was not usable (%s)",type(exc).__name__)
        dedup=[];keys=set()
        for contact in contacts:
            key=(contact.email.casefold() if contact.email else "",(contact.name or "").casefold(),(contact.role or "").casefold())
            if key in keys:continue
            keys.add(key);dedup.append(contact)
        form=next((u for u in forms if allowed(u)),None)
        status="partial" if errors and dedup else "failed" if errors and not dedup and not form else "empty" if not dedup and not form else "complete"
        return ContactScrapeResult(contacts=dedup,application_form_url=form,status=status,requests=self.request_count-before,pages_scanned=len(visited),elapsed_seconds=time.perf_counter()-started,errors=errors)
