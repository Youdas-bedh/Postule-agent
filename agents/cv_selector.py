"""Select an existing CV using deterministic, explainable evidence."""
from __future__ import annotations
import re
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from database.models import CVFile,CandidateProfile,Company,JobOffer
from agents.job_matcher import SKILL_ALIASES

class CVSelection(BaseModel):
    selected_cv:int|None=None
    cv_match_score:int=Field(ge=0,le=100)
    cv_selection_reason:str
    filename:str|None=None

def _corpus(cv:CVFile)->str:
    return " ".join((cv.filename or "",cv.extracted_text or "")).casefold()

def _skills(text:str)->set[str]:
    folded=text.casefold();found=set()
    for skill,aliases in SKILL_ALIASES.items():
        if any(re.search(r"(?<![\w])"+re.escape(alias.strip().casefold())+r"(?![\w])",folded) for alias in aliases):found.add(skill)
    return found

class CVSelector:
    def select(self,session:Session,profile:CandidateProfile,company:Company,job:JobOffer|None=None)->CVSelection:
        cvs=list(session.scalars(select(CVFile).where(CVFile.profile_id==profile.id).order_by(CVFile.id)))
        if not cvs:return CVSelection(selected_cv=None,cv_match_score=0,cv_selection_reason="Aucun CV importé pour le profil candidat actif.")
        if len(cvs)==1:
            cv=cvs[0]
            return CVSelection(selected_cv=cv.id,cv_match_score=100,cv_selection_reason="Seul CV importé pour ce profil ; sélection automatique.",filename=cv.filename)
        target=" ".join((company.name,company.sector or "",company.description or "",job.title if job else "",job.description if job else "",job.requirements if job else "",job.skills if job else ""))
        wanted=_skills(target);profile_skills=_skills(profile.content)
        scored=[]
        for cv in cvs:
            corpus=_corpus(cv);cv_skills=_skills(corpus);overlap=wanted&cv_skills
            profile_overlap=cv_skills&profile_skills
            filename=(cv.filename or "").casefold()
            title_terms={x for x in (job.title if job else "").casefold().split() if len(x)>3}
            title_overlap=sum(term in filename for term in title_terms)
            score=min(100,round((55*len(overlap)/max(1,len(wanted)))+(35*len(profile_overlap)/max(1,len(profile_skills)))+min(10,title_overlap*3)))
            scored.append((score,len(overlap),cv.id,cv,overlap))
        score,_,_,chosen,overlap=max(scored,key=lambda row:(row[0],row[1],-row[2]))
        matched=", ".join(sorted(overlap)) or "aucune compétence technique commune détectée"
        return CVSelection(selected_cv=chosen.id,cv_match_score=score,cv_selection_reason=f"CV le plus proche du poste et du domaine ; signaux communs : {matched}.",filename=chosen.filename)
