"""Generate short, source-backed application messages without sending them."""
from __future__ import annotations
import json,re,time
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from agents.job_matcher import SKILL_ALIASES
from database.models import CandidateProfile,Company,Contact,CVFile,JobOffer
from llm.base import LLMError,LLMProvider
from config import get_settings

class EmailDraft(BaseModel):
    model_config=ConfigDict(extra="forbid")
    subject:str=Field(min_length=4,max_length=180)
    body:str|None=Field(default=None,max_length=4000)
    short_message:str|None=None
    motivation_text:str|None=None

class EmailGenerator:
    def __init__(self,provider:LLMProvider|None=None):self.provider=provider
    @staticmethod
    def _facts(profile:CandidateProfile,company:Company,job:JobOffer|None,contact:Contact|None)->list[str]:
        facts=[]
        for line in profile.content.splitlines():
            cleaned=re.sub(r"^\s*(?:[-*>#]+\s*)+","",line).strip()
            if cleaned and len(cleaned)>18 and len(cleaned)<240 and any(word in cleaned.casefold() for word in ("expérience","experience","compétence","skill","formation","diplôme","disponib","python","pytorch","machine learning","deep learning","nlp","llm","générative","generative","data engineer","projet","project","stage")):
                facts.append(cleaned)
            if len(facts)>=7:break
        if company.description:facts.append(company.description[:240].strip())
        if company.sector:facts.append(f"Secteur indiqué pour {company.name} : {company.sector}.")
        if job:
            facts.append(f"Offre : {job.title}.")
            if job.department:facts.append(f"Équipe ou département : {job.department}.")
            if job.location:facts.append(f"Localisation de l’offre : {job.location}.")
        if contact and contact.name:facts.append(f"Nom public du contact : {contact.name}.")
        if contact and contact.role:facts.append(f"Rôle public du contact : {contact.role}.")
        return list(dict.fromkeys(facts))
    @staticmethod
    def _candidate_name(profile:CandidateProfile)->str|None:
        for line in profile.content.splitlines()[:15]:
            line=re.sub(r"^\s*#+\s*","",line).strip()
            if re.search(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]+\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]+\b",line):
                match=re.search(r"([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]+\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]+)",line)
                if match:return match.group(1)
        return None
    @staticmethod
    def _skills(text:str)->list[str]:
        folded=text.casefold();return [skill for skill,aliases in SKILL_ALIASES.items() if any(re.search(r"(?<![\w])"+re.escape(alias.strip().casefold())+r"(?![\w])",folded) for alias in aliases)]
    def _template(self,profile,company,job,contact,channel,facts):
        name=self._candidate_name(profile);role=job.title if job and job.title else "un poste en lien avec mon parcours"
        skill_lines=self._skills(profile.content)
        skills=", ".join(skill_lines[:5])
        salutation=f"Bonjour {contact.name.split()[0]}," if contact and contact.name else "Bonjour à l’équipe recrutement,"
        company_reason=(f"Je m’intéresse aux travaux de {company.name} dans le domaine {company.sector}." if company.sector else f"Je m’intéresse aux activités de {company.name}.")
        if channel in {"VERIFIED_EMAIL","RECRUITER_CONTACT"}:
            subject=f"Candidature – {role}"+(f" – {name}" if name else "")
            status = get_settings().candidate_status
            diploma = "Diplômé d'un Master en Intelligence Artificielle à Sorbonne Université, je recherche actuellement un CDI en IA ou Machine Learning." if status == "graduated" else "Je recherche actuellement un CDI en IA ou Machine Learning."
            experience = "Mon stage de fin d'études chez ALTEN portait notamment sur le NLP, l'IA générative et le traitement audio." if "alten" in profile.content.casefold() else (f"Mon parcours comprend notamment {skills}." if skills else "Mon parcours est décrit dans le CV joint.")
            body=(f"{salutation}\n\nJe vous contacte au sujet du poste de {role} chez {company.name}. "
                  f"{diploma} {experience} "
                  f"{company_reason} Je souhaitais savoir si vous aviez actuellement, ou prochainement, des besoins correspondant à ce profil. "
                  f"Je vous joins mon CV et reste disponible si vous souhaitez échanger.\n\nBien cordialement,\n{name or 'Candidat'}")
            return EmailDraft(subject=subject,body=body,short_message=f"Bonjour, je vous adresse ma candidature pour {role} chez {company.name}. Mon CV est joint à ce brouillon. Merci pour votre attention.")
        motivation=(f"Je souhaite proposer ma candidature pour {role} auprès de {company.name}. {company_reason} "
                    f"Mon profil fait état de compétences en {skills if skills else 'plusieurs domaines pertinents décrits dans mon profil'} ; je souhaite les mobiliser dans une équipe dont les missions correspondent à mon parcours. "
                    "Je serais heureux d’échanger sur les besoins actuels et sur les postes susceptibles de convenir. Mon CV est préparé pour accompagner cette candidature.")
        return EmailDraft(subject=f"Candidature – {role}"+(f" – {name}" if name else ""),motivation_text=motivation,short_message=motivation)
    @staticmethod
    def _valid(response:str,source_facts:list[str],profile:str,job:JobOffer|None)->EmailDraft|None:
        try:parsed=EmailDraft.model_validate_json(response)
        except (ValueError,ValidationError,TypeError):return None
        if not parsed.body:return None
        corpus=(profile+"\n"+(job.title+"\n"+(job.description or "") if job else "")).casefold()
        for number in re.findall(r"\b\d+(?:[.,]\d+)?\b",parsed.body):
            if number not in corpus:return None
        # Any named technology the draft attributes to the candidate must exist in the profile.
        body_skills=set(EmailGenerator._skills(parsed.body));profile_skills=set(EmailGenerator._skills(profile))
        if not body_skills<=profile_skills:return None
        if not source_facts:return None
        return parsed
    def generate(self,profile:CandidateProfile,company:Company,job:JobOffer|None,contact:Contact|None,channel:str)->tuple[EmailDraft,list[str],str,float]:
        facts=self._facts(profile,company,job,contact);start=time.perf_counter();template=self._template(profile,company,job,contact,channel,facts)
        if channel not in {"VERIFIED_EMAIL","RECRUITER_CONTACT"}:
            return template,facts,"template",time.perf_counter()-start
        if self.provider is None:return template,facts,"template_no_llm",time.perf_counter()-start
        request={"candidate_profile":profile.content[:14000],"company":{"name":company.name,"sector":company.sector,"description":company.description},"job":{"title":job.title,"description":(job.description or "")[:5000]} if job else None,"contact":{"name":contact.name,"role":contact.role} if contact else None,"source_facts":facts}
        prompt=("Write a professional, natural, concise French job application email, 70–130 words. Return only JSON with subject and body. "
                "Use only the provided candidate, company and job information. Do not invent technologies, projects, achievements, names or facts. If information is missing, omit it. "
                "Do not claim an attachment was actually sent; describe the CV as prepared for the draft. Avoid exaggerated or marketing language.\n"+json.dumps(request,ensure_ascii=False))
        for attempt in range(2):
            try:raw=self.provider.generate(prompt,system_prompt="Use only supplied source facts. If a detail is not present, leave it out.",max_tokens=500,json_mode=True)
            except (LLMError,TimeoutError,ConnectionError,RuntimeError,ValueError):break
            parsed=self._valid(raw,facts,profile.content,job)
            if parsed:
                words=len(re.findall(r"\b\w+\b",parsed.body))
                if 70<=words<=130:return parsed,facts,"local_llm",time.perf_counter()-start
        return template,facts,"template_invalid_or_unavailable_llm",time.perf_counter()-start
