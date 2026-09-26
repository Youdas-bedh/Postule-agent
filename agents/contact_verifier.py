"""Conservative, non-intrusive validation and ranking for public contacts."""
from __future__ import annotations
import importlib.util, re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from database.models import Contact
from utils.urls import normalize_domain

EMAIL_RE=re.compile(r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+",re.I)
DISPOSABLE_DOMAINS={"mailinator.com","tempmail.com","guerrillamail.com","10minutemail.com","yopmail.com","trashmail.com","getnada.com"}
VerificationStatus=Literal["VERIFIED","PROBABLE","UNVERIFIED"]

class EmailVerification(BaseModel):
    model_config=ConfigDict(extra="forbid")
    email:str
    syntax_valid:bool
    domain_match:bool
    mx_status:str="unavailable"
    disposable:bool=False
    confidence_score:int=Field(ge=0,le=100)
    verification_status:VerificationStatus

def extract_emails(text:str)->list[str]:
    found=[]
    for match in EMAIL_RE.finditer(text or ""):
        address=match.group(0).rstrip(".,;:)").lower()
        if _valid_email(address):found.append(address)
    return list(dict.fromkeys(found))

def _valid_email(value:str)->bool:
    if not EMAIL_RE.fullmatch(value):return False
    local=value.rsplit("@",1)[0]
    return not (local.startswith(".") or local.endswith(".") or ".." in local)

def validate_email(email:str,company_domain:str,*,source_type:str="OFFICIAL_WEBSITE",confidence_score:int=100,check_mx:bool=True)->EmailVerification:
    normalized=(email or "").strip().lower(); valid=_valid_email(normalized)
    email_domain=normalized.rsplit("@",1)[-1] if "@" in normalized else ""
    company=normalize_domain(company_domain)
    domain_match=bool(company and (email_domain==company or email_domain.endswith("."+company)))
    disposable=email_domain in DISPOSABLE_DOMAINS
    mx="not_checked"
    if check_mx and valid and importlib.util.find_spec("dns"):
        try:
            import dns.resolver
            dns.resolver.resolve(email_domain,"MX",lifetime=2.0); mx="present"
        except Exception as exc:
            mx="missing" if type(exc).__name__ in {"NoAnswer","NXDOMAIN"} else "unavailable"
    score=max(0,min(100,int(confidence_score)))
    if not valid or not domain_match or disposable or source_type=="INFERRED_PATTERN" or mx=="missing":
        status="UNVERIFIED"
        if not valid or disposable or not domain_match or mx=="missing":score=0
    elif source_type.startswith("OFFICIAL_") and score>=80:
        status="VERIFIED"
    elif score>=50:
        status="PROBABLE"
    else:status="UNVERIFIED"
    return EmailVerification(email=normalized,syntax_valid=valid,domain_match=domain_match,mx_status=mx,disposable=disposable,confidence_score=score,verification_status=status)

def infer_email_pattern(name:str,company_domain:str)->EmailVerification|None:
    """Optional suggestion only. Discovery never calls this automatically."""
    parts=re.findall(r"[a-zA-ZÀ-ÿ]+",name or "")
    domain=normalize_domain(company_domain)
    if len(parts)<2 or not domain:return None
    local=".".join(part.encode("ascii","ignore").decode().lower() for part in (parts[0],parts[-1])).strip(".")
    if not local:return None
    return validate_email(f"{local}@{domain}",domain,source_type="INFERRED_PATTERN",confidence_score=30,check_mx=False)

def classify_contact_role(value:str|None)->str|None:
    text=(value or "").casefold()
    if any(t in text for t in ("talent acquisition","talent partner","talent manager")):return "Talent Acquisition"
    if any(t in text for t in ("recruiter","recruitment","recruteur","recrutement")):return "Recruiter"
    if any(t in text for t in ("human resources","hr business partner","people partner","people operations","ressources humaines")):return "HR"
    if any(t in text for t in ("engineering manager","ai manager","engineering director","head of engineering")):return "Engineering Manager"
    return None

def contact_priority(contact:Contact)->int:
    email=(contact.email or "").casefold(); role=(contact.role or "").casefold(); source=(contact.source_type or "").upper()
    if source=="INFERRED_PATTERN":return 6
    if email.startswith(("recruitment@","recruiting@","jobs@","careers@","career@","talent@","rh@","hr@")):return 0
    if "talent acquisition" in role:return 1
    if "recruit" in role:return 2
    if "hr" in role or "human resources" in role:return 3
    if "manager" in role:return 4
    return 5

def rank_contacts(contacts:list[Contact])->list[Contact]:
    return sorted(contacts,key=lambda c:(contact_priority(c),-int(c.confidence_score or 0),c.name or "",c.id or 0))

def parse_role_assessment(raw:str)->str|None:
    """Validate an optional LLM role classification; malformed output is ignored."""
    import json
    from pydantic import ValidationError
    class RoleOutput(BaseModel):
        model_config=ConfigDict(extra="ignore")
        role:str=Field(min_length=1,max_length=80)
    try:
        data=json.loads(raw.strip()); role=RoleOutput.model_validate(data).role
        return role if role in {"Talent Acquisition","Recruiter","HR","Engineering Manager","Other"} else None
    except (ValueError,ValidationError,TypeError):return None
