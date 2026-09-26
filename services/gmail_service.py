"""Minimal Gmail API sender using official Google client libraries."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from services.oauth_service import gmail_credentials


class GmailSendError(RuntimeError):
    pass


class GmailService:
    def __init__(self, *, credentials=None, service=None):
        self._credentials = credentials
        self._service = service

    def _api(self):
        if self._service is None:
            creds = self._credentials or gmail_credentials()
            from googleapiclient.discovery import build
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    @staticmethod
    def build_message(to: str, subject: str, body: str, attachment_path: str | Path) -> dict:
        path = Path(attachment_path)
        if path.suffix.casefold() != ".pdf" or not path.is_file():
            raise GmailSendError("Le CV PDF est absent ou invalide.")
        if path.stat().st_size <= 0 or path.stat().st_size > 15_000_000:
            raise GmailSendError("La taille du CV dépasse la limite autorisée.")
        message = EmailMessage()
        message["To"], message["Subject"] = to, subject
        message.set_content(body)
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return {"raw": raw}

    def send(self, *, to: str, subject: str, body: str, attachment_path: str | Path) -> dict:
        try:
            payload = self.build_message(to, subject, body, attachment_path)
            return self._api().users().messages().send(userId="me", body=payload).execute()
        except GmailSendError:
            raise
        except Exception as exc:
            raise GmailSendError(f"L'envoi Gmail a échoué ({type(exc).__name__}).") from exc
