# Rapport de validation finale

Date : 2026-09-26 · projet : `postule-agent` · Python applicatif : 3.11

## Architecture et portée

L’application reste un Streamlit local au-dessus de SQLAlchemy/SQLite. Les modules `agents/`, `scrapers/`, `careers/`, `llm/` et `services/` gardent leurs rôles séparés ; le Dashboard lit les données existantes et les exports s’appuient sur des listes de colonnes autorisées. Le serveur vLLM conserve son environnement Python isolé. Aucune fonction d’envoi, d’automatisation de formulaire ou de contact n’a été ajoutée.

Les changements de stabilisation portent sur les index SQLite additives et conditionnels, une seconde tentative JSON bornée dans Job Matching puis repli déterministe, la normalisation de la colonne de score Streamlit, des annotations publiques manquantes, l’ignore-list de sécurité et la documentation. Les pages et le workflow d’approbation n’ont pas été réécrits.

## Résultats des contrôles

| Contrôle | Résultat |
| --- | --- |
| Tests unitaires et d’interface | `104 passed, 2 deselected` avec `python -m pytest -q` |
| Tests intégration Internet | `2 passed, 104 deselected` avec `python -m pytest -q -m integration` |
| Compilation des sources du projet | Réussie avec `python -m compileall -q app.py config.py agents careers database discovery graph llm scrapers services ui utils scripts tests` |
| `pip check` environnement applicatif | Aucune dépendance cassée |
| `uv pip check` environnement GPU | 199 paquets vérifiés, compatibles |
| Streamlit | Les 8 pages passent dans AppTest sans exception ; `/_stcore/health` renvoie `ok` en HTTP 200 sur le port 8502 déjà actif |
| vLLM | API disponible ; modèle `Qwen/Qwen3.5-9B` listé ; smoke test réel répondu `Bonjour.` en 0,20 s |
| SQLite | `PRAGMA integrity_check` renvoie `ok` ; `PRAGMA foreign_key_check` ne trouve aucune violation |

La commande littérale `python -m compileall .` parcourt également `.venv-vllm` et échoue sur un fichier PyTorch optionnel, `torch/testing/_internal/py312_intrinsics.py`, qui utilise une syntaxe Python 3.12 alors que l’environnement GPU est Python 3.11. La compilation ciblée de tout le code du projet ci-dessus passe. Ce fichier tiers n’est pas importé par l’application.

## État des données

La base contient 3 entreprises — Danone, Mistral AI et OVHcloud —, 220 offres, 4 contacts, 1 candidature et 1 événement d’historique. Les statuts enregistrés sont : `DRAFT_READY=1`, `APPROVED=0`, `REJECTED=0`, `IGNORED=0`. Après le dry-run, l’application reste `DRAFT_READY` ; aucune ligne `SENT` n’a été créée.

La candidature Mistral AI existante est associée à l’offre **AI Scientist - Agentic Engineering**, score sauvegardé **80/100**, et reste un brouillon à valider. Le parcours de démonstration a réutilisé son analyse en cache, ses offres et contacts stockés, évalué la stratégie et le CV, recalculé un matching déterministe dans une transaction annulée, produit un aperçu de brouillon uniquement en mémoire, puis lu les métriques et exports. Aucune page externe n’a été visitée pendant ce dry-run et aucune donnée persistante de matching, de candidature ou de statut n’a été modifiée.

## Performance observée

Mesure locale sur cette base, hors attente humaine :

| Opération | Temps observé |
| --- | ---: |
| Lecture/cache d’analyse entreprise | 0,0001 s |
| Vue contacts depuis les données enregistrées | 0,0444 s |
| Calcul de stratégie et sélection CV | 0,0081 s |
| Aperçu déterministe de brouillon en mémoire | 0,0726 s |
| Matching déterministe d’une offre, transaction annulée | 0,2069 s |
| Lecture du Dashboard | 0,0152 s |
| Sérialisation des exports en mémoire | 0,0365 s |
| Génération smoke test vLLM | 0,20 s |

Ces valeurs représentent un seul poste et une seule offre de matching ; ce ne sont pas des mesures de débit multi-utilisateur. L’A40 indiquait 46 068 MiB total, 39 073 MiB utilisés, 6 416 MiB libres et 0 % d’utilisation GPU au moment du relevé. Le serveur vLLM était actif.

## Base, dépendances et sécurité

Les index ajoutés couvrent les contacts par entreprise/email/statut, les offres par score et les candidatures par statut et profil/entreprise. Les index sur domaine d’entreprise et URL d’offre sont déjà fournis par les contraintes uniques. La migration tolère les anciennes tables partielles en ignorant un index dont la colonne est absente. Les lignes existantes ont été conservées.

Le ciblage a ensuite été précisé à la demande de l’utilisateur : la recherche d’entreprises propose désormais la France entière par défaut ; Career & Jobs, Job Matching, la détection du canal et la stratégie de candidature filtrent strictement sur les offres marquées France ou localisées dans une ville/région française reconnue. Pays étrangers et localisations inconnues ne passent pas le matching. Ce filtre conservateur a classé 113 des 220 offres existantes en France (toutes à Paris chez Mistral AI dans le snapshot actuel) ; les 107 autres offres restent archivées et ne sont pas supprimées. Danone et OVHcloud n’ont actuellement aucune offre classifiable en France dans la base, ce qui explique pourquoi la candidature enregistrée concerne Mistral AI.

Depuis cette validation initiale, le statut de la candidature Mistral est devenu `APPROVED` ; un événement d’approbation est présent dans son historique (2026-09-26 16:18 UTC). Ce changement ne vient pas des contrôles en lecture seule de cette validation France.

Les paramètres de `Settings` sont tous présents dans `.env.example`; `HF_HOME` est documenté pour le serveur vLLM. Le fichier `.env` local existe et n’a pas été affiché ni inclus dans les exports. L’analyse de motifs de clés sur les sources du projet n’a trouvé aucun secret probable. `.gitignore` exclut les bases, CV PDF, profils privés, `.env`, environnements virtuels, caches HF, credentials et clés SSH.

Ce dossier de travail n’a pas de métadonnées `.git`. La politique d’exclusion est prête, mais il n’est donc pas possible de vérifier quels fichiers seraient déjà suivis dans un dépôt Git. `ruff` n’est pas installé ; aucun linter supplémentaire n’a été ajouté. Les environnements applicatif et vLLM sont vérifiés séparément, et `requirements-vllm.txt` reste indépendant.

## Problèmes rencontrés et corrections

- Des bases de test historiques ont une table `contacts` partielle : création conditionnelle des index uniquement quand les colonnes existent.
- Qwen peut produire un JSON de matching mal formé : une seule requête de réparation est tentée ; en cas de nouvel échec, le score déterministe est conservé. Le test vérifie le repli.
- Job Matching mélangeait des scores numériques et le texte « À calculer » dans une colonne, ce qui déclenchait une erreur de conversion Arrow : le score reste numérique et son état apparaît dans une colonne séparée.
- Streamlit signalait l’API `use_container_width` comme dépréciée : les pages concernées utilisent désormais `width="stretch"`, et la dépendance minimale Streamlit est alignée sur cette API.
- L’environnement vLLM nécessite les chemins CUDA embarqués pour certaines commandes CLI : `scripts/start_vllm.sh` les ajoute ; avec ces chemins, `vllm --version` fonctionne (vLLM 0.30.0) et le serveur déjà lancé répond correctement.

## Limites et points restants

- SQLite et Streamlit ne fournissent pas d’authentification ni de contrôle multi-utilisateur ; protéger le réseau et le volume RunPod.
- Les scrapers dépendent des pages publiques et formats ATS, qui peuvent évoluer. Les scores restent des aides heuristiques à relire.
- Aucune analyse forcée, rescan, nouvelle découverte, soumission ou prise de contact n’a été faite dans cette validation.
- Le serveur Streamlit préexistant sur le port 8502 a été contrôlé plutôt que remplacé ; son endpoint santé répond normalement.
- Le dépôt Git doit être initialisé séparément si une livraison versionnée est souhaitée.

## Commandes de lancement

Application :

```bash
cd /workspace/postule-agent
source .venv/bin/activate
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --browser.gatherUsageStats false
```

Serveur GPU dans un autre terminal :

```bash
cd /workspace/postule-agent
VLLM_BIN="$PWD/.venv-vllm/bin/vllm" bash scripts/start_vllm.sh
```

Contrôles :

```bash
source .venv/bin/activate
python scripts/test_llm.py
python -m pytest -q
python -m pytest -q -m integration
python -m pip check
```
