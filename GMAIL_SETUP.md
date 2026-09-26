# Configuration Gmail (OAuth)

L'envoi reste verrouillé par défaut : `EMAIL_DRY_RUN=true`, `AUTO_SEND_ENABLED=false` et `GMAIL_ENABLED=false`. Les tests et le mode aperçu n'envoient jamais de message.

## Google Cloud

1. Dans un projet Google Cloud, active **Gmail API**.
2. Configure l'écran de consentement OAuth et ajoute ton compte comme utilisateur test tant que l'application est en mode test.
3. Crée un identifiant OAuth de type **Application de bureau** et télécharge le JSON client.
4. Copie ce fichier uniquement dans `secrets/gmail_credentials.json` (ou règle `GMAIL_CREDENTIALS_PATH`). Le dossier `secrets/` est ignoré par Git.
5. Garde `GMAIL_TOKEN_PATH` dans le même dossier privé. Le jeton est créé avec les droits du propriétaire du compte et n'est jamais imprimé dans les logs.

## RunPod / OAuth

L'OAuth utilise le flux officiel Installed App et le scope minimal `gmail.send`. Depuis ton poste, ouvre un tunnel SSH vers le pod :

```bash
ssh -L 8765:127.0.0.1:8765 root@ADRESSE_DU_POD
```

Dans le terminal du pod, depuis `/workspace/postule-agent` :

```bash
source .venv/bin/activate
python scripts/connect_gmail.py
```

Ouvre dans le navigateur local le lien Google affiché par le script et termine l'autorisation. Le tunnel ramène le callback OAuth du port 8765 vers le pod. Le script écrit le jeton dans `GMAIL_TOKEN_PATH` avec des permissions privées.

## Dry-run puis envoi réel

La page **Auto Send** permet d'inspecter le destinataire, le sujet, le message et le PDF joint. Avec `EMAIL_DRY_RUN=true`, le résultat est `WOULD_SEND` et aucun appel `messages.send` n'est effectué. La page **Gmail** indique si les identifiants et le jeton sont valides.

Après avoir vérifié plusieurs aperçus et l'adresse du compte, l'activation de l'envoi réel est une action manuelle :

```dotenv
GMAIL_ENABLED=true
AUTO_SEND_ENABLED=true
EMAIL_DRY_RUN=false
```

Redémarre ensuite Streamlit. La file conserve la limite quotidienne, l'intervalle minimal, un destinataire vérifié par entreprise, les contrôles anti-doublon et l'attachement du CV PDF. Ne mets jamais les fichiers OAuth dans un dépôt, un ticket ou un export.
