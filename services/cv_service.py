"""Store uploaded CV originals unchanged and extract text for selection only."""
from __future__ import annotations
import io,uuid,zipfile
from pathlib import Path
from xml.etree import ElementTree
from pypdf import PdfReader
from sqlalchemy.orm import Session
from config import ROOT,get_settings
from database.models import CVFile,CandidateProfile

class CVImportError(ValueError):pass

def _extract(filename:str,data:bytes)->str:
    suffix=Path(filename).suffix.casefold()
    if suffix in {".txt",".md",".markdown"}:return data.decode("utf-8",errors="replace")[:500_000]
    if suffix==".pdf":
        try:return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)[:500_000]
        except Exception as exc:raise CVImportError("Le PDF ne peut pas être lu ; le fichier original n’a pas été modifié.") from exc
    if suffix==".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:xml=ElementTree.fromstring(archive.read("word/document.xml"))
            return "\n".join(node.text or "" for node in xml.iter() if node.tag.endswith("}t"))[:500_000]
        except (KeyError,zipfile.BadZipFile,ElementTree.ParseError) as exc:raise CVImportError("Le document DOCX est invalide.") from exc
    raise CVImportError("Formats acceptés : PDF, DOCX, TXT et Markdown.")

def import_cv(session:Session,profile:CandidateProfile,filename:str,data:bytes)->CVFile:
    clean=Path(filename).name
    if not clean or clean in {".",".."}:raise CVImportError("Nom de fichier invalide.")
    if len(data)>get_settings().max_cv_bytes:raise CVImportError("Le CV dépasse la limite configurée MAX_CV_BYTES.")
    text=_extract(clean,data)
    directory=ROOT/"data"/"cvs";directory.mkdir(parents=True,exist_ok=True)
    stored=directory/f"{uuid.uuid4().hex}{Path(clean).suffix.casefold()}"
    stored.write_bytes(data)
    try:
        row=CVFile(profile_id=profile.id,filename=clean,storage_path=str(stored),extracted_text=text)
        session.add(row);session.flush()
        return row
    except Exception:
        stored.unlink(missing_ok=True)
        raise
