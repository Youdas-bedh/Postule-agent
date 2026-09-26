from pathlib import PurePosixPath
from config import get_settings


def read_profile(filename: str, data: bytes) -> tuple[str, str]:
    safe_name = PurePosixPath(filename.replace("\\", "/")).name
    if not safe_name.lower().endswith((".md", ".markdown")):
        raise ValueError("Le profil doit être un fichier Markdown (.md ou .markdown).")
    if len(data) > get_settings().max_profile_bytes:
        raise ValueError("Le profil dépasse la taille maximale autorisée.")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Le fichier doit être encodé en UTF-8.") from exc
    if not content.strip():
        raise ValueError("Le profil est vide.")
    return safe_name, content
