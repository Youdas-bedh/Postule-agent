"""Canonicalization helpers for public job posting URLs."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
TRACKING={"fbclid","gclid","source","src","ref","referrer","gh_src","lever-source"}
def normalize_job_url(value:str)->str:
 p=urlsplit(value.strip())
 if p.scheme not in {"http","https"} or not p.hostname: raise ValueError("URL d'offre invalide")
 host=p.hostname.lower().removeprefix("www."); port=f":{p.port}" if p.port and p.port not in (80,443) else ""
 query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
 return urlunsplit(("https",host+port,p.path.rstrip("/") or "/",urlencode(query,doseq=True),""))
