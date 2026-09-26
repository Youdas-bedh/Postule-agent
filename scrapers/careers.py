"""Bounded, polite discovery and extraction from public career pages."""
from __future__ import annotations
import re, time, logging
from urllib.parse import urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup
from careers.base import CareerScanResult, detect_ats
from careers.generic import CAREER_PATHS, career_links, parse_jobs
from careers.greenhouse import parse_greenhouse
from careers.lever import parse_lever
from careers.smartrecruiters import parse_smartrecruiters
from careers.workday import parse_workday
from careers.ashby import parse_ashby
from utils.urls import normalize_url, normalize_domain

logger=logging.getLogger(__name__)
UA="PostuleAgentCareerResearch/1.0 (+public career listings)"

class CareerScraper:
    def __init__(self, *, timeout=12, client=None):
        self.timeout=timeout; self.client=client; self.request_count=0
    def _get(self,url):
        self.request_count+=1
        response=(self.client.get(url,timeout=self.timeout,follow_redirects=True,headers={"User-Agent":UA}) if self.client else httpx.get(url,timeout=self.timeout,follow_redirects=True,headers={"User-Agent":UA}))
        response.raise_for_status(); return response
    def discover(self, website:str)->tuple[str,str]:
        base=normalize_url(website)
        if not base: raise ValueError("Site officiel invalide")
        home=self._get(base); home_url=str(home.url); origin=normalize_domain(home_url)
        content=home.headers.get("content-type","text/html").lower()
        if "html" not in content: raise ValueError("Le site officiel ne renvoie pas du HTML")
        links=career_links(home.text,home_url)
        # Some sites publish localized careers URLs in public bootstrap JSON
        # instead of anchor elements (for example their language selector).
        embedded=re.findall(r"https?:\\?/\\?/careers(?:\\?/|[.])[^\"'\\\s<>]{0,180}",home.text,re.I)
        for raw in embedded:
            candidate=raw.replace("\\/","/")
            if candidate.startswith("http"):
                links.append(candidate)
        same=[u for u in links if _same_site(u,origin)]
        external=[u for u in links if detect_ats(u)]
        # Explicit internal link wins; ATS only follows an explicit link from homepage.
        candidates=same+external
        candidates.extend(urljoin(home_url,path) for path in CAREER_PATHS)
        seen=set()
        for candidate in candidates:
            if candidate in seen:continue
            seen.add(candidate)
            if not _same_site(candidate,origin) and not detect_ats(candidate):continue
            try:
                page=self._get(candidate)
                if "html" not in page.headers.get("content-type","text/html").lower():continue
                soup=BeautifulSoup(page.text,"html.parser")
                if len(soup.get_text(" ",strip=True))<30:continue
                return str(page.url),page.text
            except (httpx.HTTPError,ValueError) as exc:
                logger.debug("[CAREER] Candidate unavailable (%s)",type(exc).__name__)
        raise LookupError("Aucune page carrière publique reconnue (404, JS-only, accès interdit ou page vide)")
    @staticmethod
    def _slug(url, host_suffix):
        path=urlsplit(url).path.strip("/").split("/")
        return path[0] if path and path[0] not in {"company","careers","jobs"} else (path[1] if len(path)>1 else "")
    def scan(self, website:str, career_url:str|None=None)->CareerScanResult:
        started=time.perf_counter(); before=self.request_count
        try:
            if career_url:
                page=self._get(career_url); resolved=str(page.url); html=page.text
            else: resolved,html=self.discover(website)
            ats=detect_ats(resolved); jobs=[]
            source_url=resolved
            source_html=html
            if not ats:
                soup=BeautifulSoup(html,"html.parser")
                linked_ats=next((urljoin(resolved,a.get("href","")) for a in soup.find_all("a",href=True) if detect_ats(urljoin(resolved,a["href"]))),None)
                if linked_ats:
                    try:
                        linked_page=self._get(linked_ats)
                        source_url=str(linked_page.url); source_html=linked_page.text; ats=detect_ats(source_url)
                        logger.info("[ATS] Detected %s from career page",ats)
                    except (httpx.HTTPError,ValueError):
                        source_url=linked_ats; ats=detect_ats(linked_ats)
                if not ats:
                    # Follow up to two explicitly labeled career/jobs pages,
                    # including official career subdomains (Danone pattern).
                    for linked_page_url in [u for u in career_links(html,resolved) if _same_site(u,normalize_domain(resolved))][:2]:
                        try:
                            linked_page=self._get(linked_page_url)
                            candidate_jobs=parse_jobs(linked_page.text,str(linked_page.url),"generic")
                            if candidate_jobs:
                                source_url=str(linked_page.url); source_html=linked_page.text; jobs=candidate_jobs
                                break
                        except (httpx.HTTPError,ValueError):
                            continue
            if ats=="greenhouse":
                slug=self._slug(source_url,"greenhouse")
                if slug:
                    try: jobs=parse_greenhouse(self._get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true").json(),slug,source_url)
                    except (httpx.HTTPError,ValueError,KeyError): jobs=[]
            elif ats=="lever":
                slug=self._slug(source_url,"lever")
                if slug:
                    api="https://api.eu.lever.co" if "jobs.eu.lever.co" in source_url else "https://api.lever.co"
                    try: jobs=parse_lever(self._get(f"{api}/v0/postings/{slug}?mode=json").json())
                    except (httpx.HTTPError,ValueError,KeyError): jobs=[]
            elif ats=="smartrecruiters":
                slug=self._slug(source_url,"smartrecruiters")
                if slug:
                    try: jobs=parse_smartrecruiters(self._get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100").json())
                    except (httpx.HTTPError,ValueError,KeyError): jobs=[]
            elif ats=="ashby":
                slug=self._slug(source_url,"ashby")
                if slug:
                    try: jobs=parse_ashby(self._get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json())
                    except (httpx.HTTPError,ValueError,KeyError): jobs=[]
            elif not jobs: jobs=parse_workday(source_html,source_url) if ats=="workday" else parse_jobs(source_html,source_url,ats or "generic")
            status="complete" if jobs else "empty"
            return CareerScanResult(career_url=resolved,ats_type=ats,jobs=jobs,status=status,requests=self.request_count-before,elapsed_seconds=time.perf_counter()-started)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return CareerScanResult(career_url=career_url,status="timeout",error=type(exc).__name__,requests=self.request_count-before,elapsed_seconds=time.perf_counter()-started)
        except httpx.HTTPStatusError as exc:
            status="forbidden" if exc.response.status_code in (401,403,429) else "http_error"
            return CareerScanResult(career_url=career_url,status=status,error=f"HTTP {exc.response.status_code}",requests=self.request_count-before,elapsed_seconds=time.perf_counter()-started)
        except (LookupError,ValueError) as exc:
            return CareerScanResult(career_url=career_url,status="unrecognized",error=str(exc),requests=self.request_count-before,elapsed_seconds=time.perf_counter()-started)

def _same_site(url, origin):
    domain=normalize_domain(url)
    return bool(domain and (domain==origin or domain.endswith("."+origin)))
