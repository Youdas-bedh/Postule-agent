"""Deterministic public HTML and JobPosting JSON-LD extraction."""
import json, re
from html import unescape
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .base import JobOfferData
from utils.job_urls import normalize_job_url
CAREER_PATHS=("/careers","/jobs","/careers/jobs","/jobs/search","/join-us","/joinus","/work-with-us","/opportunities","/recruitment","/recrutement","/nous-rejoindre","/nousrejoindre","/carrieres","/offres-emploi")
CAREER_TERMS=("career","jobs","join us","opportunities","open position","nous rejoindre","carri","recrutement","offres d'emploi")
KEYWORDS=("AI","Artificial Intelligence","Machine Learning","Data Scientist","Data Engineer","Generative AI","LLM","NLP","Computer Vision","MLOps","Software Engineer")
def relevance_keywords(title,description="",extra=""):
    body=description or ""
    # ATS descriptions commonly prepend a repeated company-wide blurb. Prefer
    # the position-specific sections when a boundary is available.
    match=re.search(r"\b(?:the role|role summary|about the role|what you will do|responsibilities|qualifications|requirements)\b",body,re.I)
    if match: body=body[match.start():]
    body=re.sub(r"https?://\S+|www\.\S+", " ", body, flags=re.I)
    content=" ".join((title or "",body,extra or ""))
    found=[]
    for keyword in KEYWORDS:
        if keyword in {"AI","LLM","NLP","MLOps"}:
            if re.search(r"\b"+re.escape(keyword)+r"\b",content,re.I):found.append(keyword)
        elif keyword.casefold() in content.casefold():found.append(keyword)
    return found
def _clean(v):
 if v is None or isinstance(v,(dict,list)): return None
 return re.sub(r"\s+"," ",BeautifulSoup(unescape(str(v)),"html.parser").get_text(" ",strip=True)) or None
def _location(v):
 if isinstance(v,list): v=v[0] if v else None
 if not isinstance(v,dict): return None,None
 a=v.get("address",v)
 if isinstance(a,list): a=a[0] if a else {}
 if not isinstance(a,dict): return None,None
 return ", ".join(map(str,[a[k] for k in ("addressLocality","addressRegion","postalCode") if a.get(k)])) or None,a.get("addressCountry")
def _walk(p):
 if isinstance(p,list):
  for x in p: yield from _walk(x)
 elif isinstance(p,dict):
  typ=p.get("@type",[]); typ=[typ] if isinstance(typ,str) else typ
  if "JobPosting" in typ: yield p
  for k in ("@graph","itemListElement","mainEntity"):
   if k in p: yield from _walk(p[k])
def parse_jobposting(item,page_url,source_type="jsonld"):
 title=_clean(item.get("title")); url=item.get("url") or page_url
 if not title:return None
 loc,country=_location(item.get("jobLocation")); ident=item.get("identifier")
 if isinstance(ident,dict):ident=ident.get("value") or ident.get("name")
 desc=_clean(item.get("description")); req=item.get("qualifications") or item.get("responsibilities"); reqs=[_clean(req)] if isinstance(req,str) and _clean(req) else []
 skills=item.get("skills",[]); skills=[s.strip() for s in re.split(r"[,;]",skills) if s.strip()] if isinstance(skills,str) else skills if isinstance(skills,list) else []
 salary=item.get("baseSalary") or {}; salary=salary.get("value",salary) if isinstance(salary,dict) else {}; lo=salary.get("minValue") if isinstance(salary,dict) else None; hi=salary.get("maxValue") if isinstance(salary,dict) else None; val=salary.get("value") if isinstance(salary,dict) else None
 if isinstance(val,(int,float)):lo=hi=val
 curr=(item.get("baseSalary") or {}).get("currency") if isinstance(item.get("baseSalary"),dict) else None
 remote="remote" if str(item.get("jobLocationType","")).upper().endswith("TELECOMMUTE") else None
 keys=relevance_keywords(title,desc," ".join(map(str,skills)))
 try:url=normalize_job_url(urljoin(page_url,str(url)))
 except ValueError:return None
 return JobOfferData(title=title,location=loc,country=country,remote_policy=remote,employment_type=_clean(item.get("employmentType")),department=_clean(item.get("occupationalCategory")),description=desc,requirements=reqs,skills=[str(x) for x in skills],salary_min=lo,salary_max=hi,salary_currency=curr,job_url=url,source_type=source_type,external_id=str(ident) if ident else None,published_at=item.get("datePosted"),is_potentially_relevant=bool(keys),relevance_keywords=keys)
def career_links(html,page_url):
 soup=BeautifulSoup(html,"html.parser"); out=[]
 for a in soup.find_all("a",href=True):
  label=(a.get_text(" ",strip=True)+" "+a.get("aria-label","")).casefold()
  if any(t in label for t in CAREER_TERMS):out.append(urljoin(page_url,a["href"]))
 return list(dict.fromkeys(out))
def parse_jobs(html,page_url,source_type="generic"):
 soup=BeautifulSoup(html,"html.parser"); jobs=[]
 for script in soup.select('script[type="application/ld+json"]'):
  try:
   for item in _walk(json.loads(script.string or script.get_text())):
    parsed=parse_jobposting(item,page_url,source_type)
    if parsed:jobs.append(parsed)
  except (ValueError,TypeError):pass
 if jobs:return _dedup(jobs)
 for card in soup.select('[data-job-id], [class*="job-card"], [class*="job-listing"], [class*="position-card"], article'):
  a=card.find("a",href=True)
  # On job-result pages the anchor is the actual listing; headings may be a
  # department/category label instead of the position title.
  job_anchor=next((link for link in card.find_all("a",href=True) if "/jobs/" in link.get("href","")),None)
  h=card.select_one(".job-card__title") or card.find(["h1","h2","h3","h4"]); t=h or job_anchor or a
  if not t:continue
  title=_clean(t.get_text(" ",strip=True))
  if not title or len(title)>240:continue
  desc=_clean(card.get_text(" ",strip=True)); url=urljoin(page_url,(job_anchor or a)["href"]) if (job_anchor or a) else page_url; keys=relevance_keywords(title,desc)
  city=card.select_one(".job-card__city"); country_node=card.select_one(".job-card__country"); remote=card.select_one(".job-card__workFromHome"); dept=card.select_one(".dn-jobdetails__meta-tag")
  location=" ".join(v for v in [city.get_text(" ",strip=True) if city else None,country_node.get_text(" ",strip=True) if country_node else None] if v) or None
  try:url=normalize_job_url(url)
  except ValueError:continue
  jobs.append(JobOfferData(title=title,description=desc,location=location,country=country_node.get_text(" ",strip=True) if country_node else None,remote_policy=remote.get_text(" ",strip=True) if remote else None,department=dept.get_text(" ",strip=True) if dept else None,job_url=url,source_type=source_type,external_id=card.get("data-job-id"),is_potentially_relevant=bool(keys),relevance_keywords=keys))
 return _dedup(jobs)
def _dedup(jobs):
 out=[]; seen=set()
 for j in jobs:
  if j.job_url not in seen:seen.add(j.job_url);out.append(j)
 return out
