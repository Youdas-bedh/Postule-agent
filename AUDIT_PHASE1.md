# Audit de la phase 1 — 26 septembre 2026

Phase 1 validée après corrections. La phase 2 n'a pas été commencée.

## Périmètre et architecture

La séparation est cohérente : `app.py` assemble l'interface ; `ui/` gère les interactions ; `services/` valide les fichiers ; `database/repositories.py` réalise les opérations métier de stockage ; `database/database.py` fournit les connexions et transactions ; `database/models.py` définit le schéma ; `config.py` centralise la configuration.

Les modules des phases suivantes restent vides intentionnellement. Aucun LLM, scraping, import PDF, workflow LangGraph ou envoi n'est implémenté. Le schéma des futures fonctionnalités est présent sans prétendre réaliser leurs traitements.

## Inventaire complet des fichiers du projet

« Modifié » et « Ajouté » désignent les changements de cet audit. « Existant » désigne les fichiers créés lors de la livraison initiale. Il n'y a pas de dépôt Git dans ce répertoire ; ces indications décrivent les opérations de la session, pas un diff Git.

| Fichier | État pendant l'audit | Rôle |
| --- | --- | --- |
| `app.py` | Modifié | Point d'entrée Streamlit, navigation, cache de connexion et gestion de l'échec d'initialisation. |
| `config.py` | Modifié | Configuration `.env` typée ; taille d'import strictement positive. |
| `database/__init__.py` | Existant | Déclare le package de stockage. |
| `database/database.py` | Modifié | Moteur SQLite, clés étrangères, sessions, création du schéma et masquage des paramètres SQL. |
| `database/models.py` | Existant | Huit modèles SQLAlchemy et statuts de candidature. |
| `database/repositories.py` | Existant | Enregistrement, déduplication atomique et lecture des profils ; audit d'import. |
| `services/__init__.py` | Existant | Déclare le package des services. |
| `services/profile_service.py` | Modifié | Vérifie extension, taille et UTF-8 ; conserve les espaces du profil. |
| `ui/__init__.py` | Existant | Déclare le package de l'interface. |
| `ui/candidate.py` | Modifié | Téléversement, aperçu, sauvegarde, sélection persistante et gestion des erreurs de stockage. |
| `agents/__init__.py` | Existant | Réserve le package des futurs agents. |
| `graph/__init__.py` | Existant | Réserve le package du futur workflow. |
| `llm/__init__.py` | Existant | Réserve le package des futurs fournisseurs LLM. |
| `scrapers/__init__.py` | Existant | Réserve le package des futurs extracteurs web. |
| `utils/__init__.py` | Existant | Réserve le package des utilitaires partagés. |
| `scripts/.gitkeep` | Ajouté | Conserve le dossier de scripts vide dans une future version Git. |
| `data/.gitkeep` | Existant | Conserve le dossier de données sans versionner son contenu privé. |
| `examples/profile.md` | Existant | Profil fictif utilisable pour vérifier l'import. |
| `tests/test_phase1.py` | Existant | Quatre tests initiaux : validation, persistance, transactions et interface. |
| `tests/test_audit_phase1.py` | Ajouté | Onze tests de régression : import complet, navigation, concurrence, erreurs et configuration. |
| `pytest.ini` | Existant | Configure la découverte des tests et les imports. |
| `.env.example` | Existant | Exemple de configuration sans secret. |
| `.gitignore` | Modifié | Exclut données, environnements, CV, secrets Streamlit et clés privées. |
| `requirements.txt` | Existant | Dépendances directes avec bornes de versions. |
| `requirements.lock.txt` | Existant | Versions exactes de l'environnement testé. |
| `README.md` | Modifié | Installation, utilisation, limites et vérifications ; lien vers cet audit. |
| `AUDIT_PHASE1.md` | Ajouté | Inventaire et résultats de l'audit. |

Fichier d'exécution existant : `data/postule.db`, base réelle SQLite, examinée en lecture pendant l'audit. Les huit tables sont vides : aucune donnée personnelle n'y est présente. Aucun fichier `.env` réel n'existe au moment de l'audit.

Fichiers générés et non livrés comme sources : `.venv/` (interpréteur et dépendances), `__pycache__/` (bytecode) et `.pytest_cache/` (cache des tests). Le script temporaire `/tmp/build_postule.py` utilisé pour la création initiale est extérieur au projet et n'est pas nécessaire au lancement.

## Défauts corrigés

1. **Sélection perdue après navigation** : reproduit avec deux profils ; l'ancien code revenait au profil le plus récent. La sélection utilise maintenant un identifiant stable et une valeur de session conservée entre les pages. Un import réussi active le profil enregistré.
2. **Risque de contenu privé dans les traces SQL** : les exceptions SQLAlchemy pouvaient inclure les paramètres d'une requête. Le moteur masque ces paramètres et l'interface journalise seulement le type d'erreur. Un test simule une erreur contenant un profil fictif et vérifie l'absence du contenu dans les logs.
3. **Erreurs de lecture ou d'initialisation non prises en charge** : l'interface affiche maintenant une erreur compréhensible sans trace technique contenant les données de configuration.
4. **Espaces du Markdown supprimés** : `strip()` modifiait le contenu alors que la documentation annonçait une conservation intégrale. Seul le BOM est désormais retiré ; la validation des fichiers blancs est conservée.
5. **Limite d'import invalide acceptée** : une taille nulle ou négative est rejetée par la configuration.
6. **Exclusions de secrets incomplètes** : ajout de `.pem`, `.key` et `.streamlit/secrets.toml`.

## Résultats de vérification

- Python utilisé : 3.11 ; SQLAlchemy installé : 2.1.1 ; SQLite : 3.45.1 ; Streamlit : 1.64.0.
- `pytest -q` : **15 tests réussis**.
- `pip check` : aucune incompatibilité de dépendances déclarée.
- Compilation des modules Python : réussie.
- Base réelle : `PRAGMA integrity_check` retourne `ok` ; `foreign_keys` vaut `1` ; `foreign_key_check` retourne zéro violation.
- Tables présentes : `companies`, `contacts`, `job_offers`, `applications`, `emails`, `candidate_profiles`, `cv_files`, `agent_runs`.
- Tests isolés : création idempotente, réouverture après écriture, déduplication et journal unique, imports concurrents, rejet d'une référence étrangère invalide et annulation complète de la transaction.
- `examples/profile.md` : lu depuis le disque puis importé et enregistré via le widget et le bouton Streamlit dans une base temporaire. Le texte complet est conservé.
- Nouveau processus Streamlit sur `127.0.0.1:8502` : démarrage réussi, page d'accueil HTTP 200, `/_stcore/health` HTTP 200 avec `ok`, aucune trace d'erreur au démarrage. Ce processus temporaire a été arrêté.
- Serveur existant sur 8501 : santé HTTP 200 avec `ok`.
- Les parcours interactifs sont vérifiés avec Streamlit AppTest ; il ne s'agit pas d'une validation visuelle dans un navigateur distant RunPod.

## Secrets et données sensibles

Relecture du code, de la configuration et des exemples, complétée par une recherche de formats de jetons, clés privées, mots de passe littéraux et identifiants dans les URL : **aucun secret hardcodé détecté**. Le profil d'exemple est explicitement fictif. Les données des tests sont synthétiques et conservées dans des bases temporaires.

Les règles `.gitignore` ont été vérifiées dans un dépôt temporaire : `.env`, `.env.production`, `credentials.json`, `tokens.json`, `data/profile.md`, `data/postule.db`, `CV.pdf`, `.streamlit/secrets.toml` et `private.key` sont exclus ; `.env.example` et `examples/profile.md` restent versionnables. Conserver les véritables profils Markdown dans `data/`, pas dans `examples/`.

Ce contrôle porte sur les fichiers du projet, pas sur un historique Git inexistant ni sur un audit de vulnérabilités des dépendances. La base SQLite n'est pas chiffrée et l'interface n'a pas d'authentification intégrée. Ces limites étaient déjà présentes ; l'accès au serveur et au volume doit rester maîtrisé. L'initialisation `create_all` ne remplace pas des migrations de schéma pour les phases suivantes.

## Lancement manuel

```bash
cd /workspace/postule-agent
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
if [ ! -f .env ]; then cp .env.example .env; fi
python -m database.database
python -m pytest -q
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --browser.gatherUsageStats false
```

Un serveur est déjà actif sur 8501 dans cet environnement. Pour lancer une autre instance sans conflit, remplacer le port par 8502 ; sur RunPod, exposer le port choisi. Dans un second terminal, contrôler le même port :

```bash
curl --fail http://127.0.0.1:8501/_stcore/health
```

Pour les lancements suivants, seuls `cd`, l'activation du venv et la commande Streamlit sont nécessaires. `Ctrl+C` arrête le serveur lancé au premier plan.
