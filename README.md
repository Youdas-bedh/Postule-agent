# postule-agent

Application locale de préparation et de suivi de candidatures. Elle organise les informations publiques d’entreprises et d’offres, les compare à un profil candidat, prépare des brouillons et laisse les décisions à une personne. Elle n’envoie pas de candidature et ne contacte pas de recruteur.

## Fonctionnalités

- Import et conservation locale d’un profil Markdown et de CV (PDF, DOCX, TXT, Markdown).
- Découverte et analyse d’entreprises, détection de pages carrière et extraction d’offres de plusieurs ATS.
- Matching déterministe enrichi par Qwen local, avec citations et compétences contrôlées contre les sources.
- Découverte de coordonnées publiées et vérifications syntaxiques, de domaine et DNS.
- Ciblage France par défaut : les offres étrangères ou dont le lieu ne permet pas de confirmer la France sont écartées des vues d’offres, du matching et de la stratégie de candidature.
- Préparation d’un brouillon associé à une offre, un contact, un canal et un CV ; validation humaine avant toute décision.
- Dashboard en lecture seule, historique des décisions et exports CSV/XLSX filtrés par colonnes explicites.
- Fournisseurs LLM modulaires : vLLM et Ollama locaux. Le fournisseur OpenAI reste désactivé.

## Architecture

```text
Profil + CV ──► Entreprises ──► Sites carrière / offres
     │                                  │
     └────────────► Matching ◄─────────┘
                          │
                Contacts publics
                          │
                Brouillon local
                          │
             Validation humaine
                          │
                Dashboard / export
```

Streamlit fournit l’interface. Les agents coordonnent des services Python simples. SQLAlchemy persiste les données dans SQLite. Les scrapers HTTP récupèrent uniquement des pages publiques et des endpoints ATS accessibles sans authentification. Un provider abstrait permet d’appeler vLLM ou Ollama ; le calcul déterministe reste disponible sans serveur LLM. Voir [ARCHITECTURE.md](ARCHITECTURE.md) pour les flux et limites détaillés.

## Stack

Python 3.11, Streamlit, SQLAlchemy 2, SQLite, Pydantic, HTTPX, BeautifulSoup, dnspython, pypdf, openpyxl, pytest. Le serveur GPU utilise vLLM dans un environnement virtuel séparé de l’application.

## Installation de l’application

Ubuntu avec Python 3.11 :

```bash
cd /workspace/postule-agent
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock.txt
cp .env.example .env  # à faire une seule fois ; garder .env privé
python -m database.database
```

Configure les valeurs nécessaires dans `.env`. `MODEL_NAME` est l’identifiant de modèle utilisé par le serveur et le client ; il n’est pas codé dans l’application. Aucun secret n’est fourni par défaut. Dans Company Discovery, le bouton « Trouver des entreprises et chercher leurs contacts » demande au LLM local des entreprises nommées, vérifie leurs sites officiels avec DDGS, puis scanne les offres et contacts publics. Les noms anonymes et les sites non confirmés sont écartés. Une source `COMPANY_SEARCH_API_URL` ou une liste publique reste possible en complément.

Paramètres importants : `DATABASE_URL`, `MAX_PROFILE_BYTES`, `MAX_CV_BYTES`, `LLM_PROVIDER`, `VLLM_BASE_URL`, `OLLAMA_BASE_URL`, `MODEL_NAME`, `LLM_TIMEOUT`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `GPU_MEMORY_UTILIZATION`, `MAX_MODEL_LEN`, `HF_HOME`, les limites de scan `CONTACT_SCAN_*` et les seuils de stratégie. La liste complète et ses valeurs sûres sont dans [.env.example](.env.example).

## RunPod et vLLM

Configuration GPU validée : RunPod Ubuntu, Python 3.11, A40 46 Go, Qwen3.5-9B (`Qwen/Qwen3.5-9B`), format du dépôt officiel sans quantification, contexte maximal 16 384 et `GPU_MEMORY_UTILIZATION=0.85`. Les paramètres peuvent être ajustés dans `.env`. Le cache Hugging Face doit être situé sur un volume persistant suffisamment grand.

Le serveur est volontairement isolé des dépendances Streamlit :

```bash
cd /workspace/postule-agent
python3.11 -m pip install uv
uv venv --python 3.11 .venv-vllm
uv pip install --python .venv-vllm/bin/python -r requirements-vllm.txt --torch-backend=auto
VLLM_BIN="$PWD/.venv-vllm/bin/vllm" bash scripts/start_vllm.sh
```

Le serveur expose l’API compatible OpenAI sur `http://127.0.0.1:8000/v1` par défaut. Garder le port local sauf besoin explicite d’accès distant ; ne jamais exposer le serveur sans authentification et filtrage réseau adaptés. Test de connexion et génération courte :

```bash
source .venv/bin/activate
python scripts/test_llm.py
```

Ollama est une alternative locale : définir `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL` et le nom exact d’un modèle déjà installé. L’option OpenAI n’effectue pas de requêtes payantes.

## Lancement de Streamlit

Dans un autre terminal :

```bash
cd /workspace/postule-agent
source .venv/bin/activate
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --browser.gatherUsageStats false
```

Pages disponibles : Profil candidat, Dashboard, **Campagne**, LLM Status, Company Discovery, Career & Jobs, Job Matching, Contacts, Applications, Auto Send et Gmail. La page Campagne regroupe sous un bouton central la recherche LLM d'entreprises françaises nommées, la vérification de leur site officiel, le scan des offres et contacts publics, le matching, le choix du CV et la préparation d'un email personnalisé. Les contacts doivent être vérifiés et les offres localisées en France. Avec `EMAIL_DRY_RUN=true` (configuration actuelle par défaut), les messages sont présentés en aperçu et aucun email n'est envoyé. Le Dashboard n'effectue que des lectures et des exports.

## Demo workflow

Candidate profile and PDF CV
→ Campagne (« Lancer les candidatures »)
→ Official French company sites and career pages
→ France-only job matching and public verified contact discovery
→ Personalized email draft with selected CV
→ Dry-run preview (`WOULD_SEND`) or configured automatic Gmail send

Chaque étape conserve ses résultats localement. Le clic sur le bouton Campagne déclenche le pipeline ; aucun envoi réel n'a lieu tant que `EMAIL_DRY_RUN=false`, `AUTO_SEND_ENABLED=true` et `GMAIL_ENABLED=true` ne sont pas réunis. En mode réel, chaque message passe les contrôles de contact, CV, doublon, contenu et limites d'envoi avant l'approbation automatique et la transmission Gmail. Voir [GMAIL_SETUP.md](GMAIL_SETUP.md) avant d'activer l'envoi.

## Tests et contrôles

```bash
source .venv/bin/activate
python -m pytest -q
python -m pytest -q -m integration  # tests réseau vers des sites publics
python -m pip check
python -m compileall -q app.py config.py agents careers database discovery graph llm scrapers services ui utils scripts tests
curl --fail http://127.0.0.1:8501/_stcore/health
```

La suite normale utilise des mocks pour HTTP et LLM et ne nécessite pas de GPU. Les tests marqués `integration` nécessitent Internet. Le test `scripts/test_llm.py` demande un serveur local actif. Les résultats de la validation finale se trouvent dans [FINAL_VALIDATION.md](FINAL_VALIDATION.md).

## Structure

```text
agents/       workflows déterministes et agents d’analyse
careers/      adaptateurs ATS et schémas d’offres
database/     modèles SQLAlchemy, SQLite et migrations additives
discovery/    sources publiques de découverte
llm/          interface et providers vLLM/Ollama/OpenAI désactivé
scrapers/     collecte publique bornée
services/     profil, CV, dashboard et exports
ui/           composants Streamlit
scripts/      démarrage vLLM et test du provider
tests/        tests unitaires, interface et intégration optionnelle
data/         base et documents locaux ignorés par Git
```

## Sécurité, limites et roadmap

`.env`, bases, profils locaux, CV, caches de modèles, environnements virtuels, secrets OAuth et clés SSH sont exclus par `.gitignore`. Les logs n’incluent pas le contenu du profil, du CV, des prompts ni des brouillons. Les exports n’incluent que des colonnes autorisées ; ils contiennent néanmoins des coordonnées professionnelles publiques si elles sont enregistrées. Protéger le volume RunPod et l’accès à Streamlit : l’application n’intègre pas d’authentification.

Les sites peuvent bloquer ou modifier leur structure ; aucune connexion, CAPTCHA ou protection n’est contourné. Les signaux de validation DNS ne prouvent pas qu’une boîte est encore utilisée. Les scores de matching et d’entreprise sont des aides à la décision, pas des garanties. Le filtre géographique est volontairement conservateur : une offre sans pays ni ville française reconnue est masquée, même si elle pourrait être en France. Les scans conservent les résultats bruts ; le Dashboard global et ses exports peuvent donc inclure d’anciennes offres hors France. La génération locale doit être relue. La base SQLite convient à un usage personnel ou de démonstration, pas à un service multi-utilisateur fortement concurrent.

L’envoi automatique Gmail est implémenté derrière des validations, quotas et anti-doublons. Le mode réel reste désactivé par défaut ; `GMAIL_SETUP.md` décrit la connexion OAuth et l’activation manuelle.
