"""Google installed-app OAuth helpers. Credential contents are never logged."""
from __future__ import annotations

import os
import logging
from pathlib import Path
from config import get_settings


SCOPES = ["https://www.googleapis.com/auth/gmail.send", "openid", "https://www.googleapis.com/auth/userinfo.email"]
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
logger = logging.getLogger(__name__)


class OAuthConfigurationError(RuntimeError):
    pass


def _scope_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    try:
        return [str(scope) for scope in value if scope]
    except TypeError:
        return []


def _scope_names(values) -> list[str]:
    names = []
    for scope in _scope_values(values):
        if scope == GMAIL_SEND_SCOPE:
            name = "gmail.send"
        elif scope == "https://www.googleapis.com/auth/userinfo.email" or scope == "email":
            name = "userinfo.email"
        elif scope == "openid":
            name = "openid"
        else:
            name = scope.rstrip("/").rsplit("/", 1)[-1]
        if name not in names:
            names.append(name)
    return sorted(names)


def _scope_report(credentials, response_scopes=None) -> tuple[bool, list[str]]:
    """Use the OAuth token response when present, then Google-auth grant metadata."""
    # The token endpoint's scope field is the authoritative result of this
    # interactive grant. Some google-auth versions expose granted_scopes as an
    # empty list even while the response contains the scopes that were granted.
    granted = response_scopes
    if granted is None:
        granted = getattr(credentials, "granted_scopes", None)
    if granted is not None:
        values = _scope_values(granted)
        allowed = GMAIL_SEND_SCOPE in values or "gmail.send" in values
        return allowed, _scope_names(values)
    checker = getattr(credentials, "has_scopes", None)
    try:
        allowed = bool(checker and checker([GMAIL_SEND_SCOPE]))
    except (AttributeError, TypeError, ValueError):
        allowed = False
    # In OAuth, an omitted scope response means the originally requested scopes
    # remain in force. Report only names, never token values.
    return allowed, _scope_names(getattr(credentials, "scopes", None))


def gmail_scope_status(credentials) -> tuple[bool, list[str]]:
    return _scope_report(credentials)


def gmail_credentials(*, allow_interactive: bool = False):
    cfg = get_settings()
    credentials_path, token_path = Path(cfg.gmail_credentials_path), Path(cfg.gmail_token_path)
    if not credentials_path.is_file():
        raise OAuthConfigurationError("Fichier OAuth Gmail absent ; configure GMAIL_CREDENTIALS_PATH.")
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES) if token_path.is_file() else None
    if credentials and credentials.valid:
        send_scope, names = _scope_report(credentials)
        if not send_scope:
            logger.info("OAuth terminé : oui ; gmail.send accordé : non ; scopes accordés : %s", ", ".join(names) or "aucun")
            if allow_interactive:
                logger.warning("Un ancien gmail_token.json ne contient pas gmail.send ; il sera remplacé uniquement après une nouvelle autorisation réussie.")
                credentials = None
            else:
                raise OAuthConfigurationError("Ancien gmail_token.json incomplet : le scope gmail.send n'est pas accordé.")
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            token_path.chmod(0o600)
        except Exception as exc: raise OAuthConfigurationError("Le jeton Gmail a expiré et son renouvellement a échoué.") from exc
    if (not credentials or not credentials.valid) and allow_interactive:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        # Google may return the equivalent `email` scope instead of the
        # requested `userinfo.email`; oauthlib treats that canonicalization as
        # a warning. Accept scope differences only after checking gmail.send.
        previous_relax = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        try:
            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=8765,
                open_browser=False,
                authorization_prompt_message="Ouvre manuellement cette URL Google dans ton navigateur : {url}",
            )
        except Exception:
            logger.info("OAuth terminé : non")
            raise
        finally:
            if previous_relax is None:
                os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
            else:
                os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous_relax
        logger.info("OAuth terminé : oui")
        response_scopes = flow.oauth2session.token.get("scope")
        send_scope, names = _scope_report(credentials, response_scopes)
        logger.info("gmail.send accordé : %s ; scopes accordés : %s", "oui" if send_scope else "non", ", ".join(names) or "aucun")
        if not send_scope:
            raise OAuthConfigurationError(
                "Google n'a pas accordé le scope Gmail d'envoi. "
                f"Scopes détectés : {', '.join(names) or 'aucun'}. "
                "Vérifie les autorisations Google et relance OAuth."
            )
        # Preserve the actual grant on the returned Credentials object too, so
        # callers use the same verified scope set after OAuth completes.
        try:
            credentials.granted_scopes = _scope_values(response_scopes)
        except (AttributeError, TypeError):
            pass
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        token_path.chmod(0o600)
    if not credentials or not credentials.valid:
        raise OAuthConfigurationError("Gmail n'est pas connecté ; utilise Connect Gmail pour autoriser l'accès.")
    if credentials and credentials.valid:
        send_scope, names = _scope_report(credentials)
        if not send_scope:
            logger.info("OAuth terminé : oui ; gmail.send accordé : non ; scopes accordés : %s", ", ".join(names) or "aucun")
            raise OAuthConfigurationError("gmail_token.json est incomplet : gmail.send n'est pas accordé. Relance l'autorisation Gmail.")
    return credentials


def gmail_oauth_status() -> dict[str, object]:
    cfg = get_settings()
    result = {"configured": Path(cfg.gmail_credentials_path).is_file(), "connected": False, "account": None, "token_valid": False, "error": None}
    try:
        creds = gmail_credentials()
        from googleapiclient.discovery import build
        profile = build("oauth2", "v2", credentials=creds, cache_discovery=False).userinfo().get().execute()
        result.update(connected=True, token_valid=bool(creds.valid), account=profile.get("email"))
    except Exception as exc:
        # Exception text from OAuth libraries may contain request data; expose only its type.
        result["error"] = f"{type(exc).__name__}: connexion Gmail indisponible."
    return result
