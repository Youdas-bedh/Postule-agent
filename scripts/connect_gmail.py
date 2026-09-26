#!/usr/bin/env python
"""Run Gmail installed-app OAuth on a remote host through an SSH port tunnel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.oauth_service import OAuthConfigurationError, gmail_credentials, gmail_oauth_status, gmail_scope_status


def main() -> int:
    print("OAuth Gmail pour RunPod : le serveur local utilisera 127.0.0.1:8765.")
    print("Garde ce terminal et le tunnel SSH ouverts, puis ouvre manuellement l’URL Google affichée ci-dessous.")
    try:
        credentials = gmail_credentials(allow_interactive=True)
        send_granted, scope_names = gmail_scope_status(credentials)
        print("OAuth terminé : oui")
        print(f"gmail.send accordé : {'oui' if send_granted else 'non'}")
        print("Scopes accordés : " + (", ".join(scope_names) or "aucun"))
        if not send_granted:
            raise OAuthConfigurationError("Le jeton ne contient pas l'autorisation Gmail d'envoi.")
        status = gmail_oauth_status()
        if not status["connected"]:
            raise OAuthConfigurationError("Jeton créé, mais vérification du compte Gmail par l'API impossible.")
        print(f"Compte Gmail connecté : {status['account']}")
    except OAuthConfigurationError as exc:
        print(f"Connexion Gmail impossible : {exc}")
        return 1
    except Exception as exc:
        print(f"Connexion Gmail impossible ({type(exc).__name__}). Vérifie GMAIL_CREDENTIALS_PATH et le tunnel OAuth.")
        return 1
    print("Connexion Gmail enregistrée. Aucun jeton n'a été affiché.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
