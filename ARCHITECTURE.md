# Architecture de `postule-agent`

## Composants

```text
app.py / ui/*
      │ appels synchrones aux agents/services
      ├── agents/* ── scrapers/*, careers/*, llm/*
      ├── services/*
      └── database/repositories.py ── SQLAlchemy ── SQLite
```

- **Interface** : `app.py` configure les ressources SQLAlchemy et la navigation. `ui/` rend les pages et exécute les actions explicites de l’utilisateur.
- **Agents** : `agents/` contient la découverte/analyse d’entreprises, les scans de carrières, le matching, l’analyse de contacts et la stratégie de candidature. Les résultats persistants sont des lignes SQLAlchemy.
- **Services** : `services/` gère l’import de profils/CV et les vues/export de dashboard.
- **Collecte** : `discovery/` et `scrapers/` interrogent des sources publiques ; `careers/` convertit plusieurs formats ATS en un modèle d’offre commun.
- **LLM** : `LLMProvider` définit l’interface de statut, génération et fermeture. `LocalVLLMProvider` et `OllamaProvider` sont locaux. `OpenAIProvider` est un choix désactivé sans appel réseau.
- **Persistance** : `database/models.py` décrit les tables. `database/database.py` crée les tables et applique des migrations SQLite additives avec `CREATE INDEX IF NOT EXISTS` / `ALTER TABLE`.

## Flux métier

1. Le profil Markdown et les CV sont importés et stockés localement.
2. La découverte ajoute les entreprises candidates ; l’analyse enrichit leur fiche à partir du site officiel et conserve des preuves vérifiables.
3. Le scan carrière reconnait les ATS pris en charge et normalise les offres enregistrées.
4. Le matching calcule d’abord des signaux déterministes, puis peut compléter l’analyse avec le LLM. Les éléments sémantiques sont contrôlés contre les textes source ; une sortie LLM invalide est retentée une fois puis ignorée au profit du résultat déterministe.
5. Le scan de contacts traite les coordonnées présentes sur des pages publiques et conserve la source. La vérification de domaine/MX reste un indice, pas une preuve de disponibilité d’une boîte.
6. La stratégie sélectionne une offre, un canal et un CV. La génération prépare un brouillon local ; les actions de validation humaine conservent un historique d’événements.
7. Le Dashboard lit les tables existantes et fabrique les exports CSV/XLSX à partir de listes de colonnes explicites. Il ne déclenche aucun agent.

Le ciblage par défaut est France. La découverte utilise « France » comme localisation initiale ; les vues Career & Jobs et Job Matching ne montrent que les offres déclarant la France ou une ville/région française reconnue. Le matching et la stratégie de candidature refusent également une offre étrangère ou géographiquement inconnue. Les offres brutes précédemment collectées restent en base et peuvent apparaître dans le Dashboard global/historique : aucune donnée existante n’est supprimée.

## Base de données

Tables : `candidate_profiles`, `cv_files`, `companies`, `job_offers`, `contacts`, `applications`, `application_events`, `emails` et `agent_runs`. Les offres et contacts sont reliés à une entreprise ; les candidatures référencent le profil et, si disponibles, une offre, un contact et un CV. Les événements d’application sont append-only via les services de workflow. Les décisions `APPROVED`, `REJECTED` et `IGNORED` sont locales ; la phase actuelle ne met aucun brouillon en `SENT`.

Les index couvrent les identifiants uniques de domaine et d’URL, les jointures par `company_id`, la déduplication ATS (`company_id`, `external_id`), les filtres sur score/contact/statut et les empreintes de cache. Les migrations sont additives pour préserver les lignes existantes. Après une migration ou restauration, vérifier avec `PRAGMA integrity_check` et `PRAGMA foreign_key_check`.

## LLM local

Les paramètres viennent de l’environnement ou de `.env` (voir `.env.example`). Le nom du modèle et les URLs ne sont pas encodés dans les agents. Le serveur vLLM est séparé de l’environnement applicatif afin d’isoler les dépendances CUDA. Les timeouts et retries sont bornés. Les logs indiquent le provider/modèle et les erreurs par type, sans contenu de prompt ni profil.

Les analyses structurées utilisent Pydantic et rejettent les citations invalides. Le matching accepte uniquement les compétences corroborées par les deux textes. L’analyse entreprise retente une sortie mal formée puis revient aux seuls éléments de page vérifiés. Les brouillons d’email utilisent un template local si le modèle est indisponible ou sa sortie ne passe pas la validation.

## Collecte web

HTTPX et BeautifulSoup lisent des pages publiques et les API publiques des ATS pris en charge. Les limites de pages et délais sont configurables. Les erreurs réseau, réponses HTTP non réussies, pages non interprétables et domaines non autorisés deviennent des états d’échec/absence plutôt que des données inventées. Aucun login, CAPTCHA ou mécanisme anti-bot n’est contourné. Les tests d’intégration peuvent solliciter des sites publics ; ils sont distincts des tests unitaires mockés.

## Interface et décisions humaines

Streamlit est une application multipage unique. Les tâches coûteuses (scan, matching, brouillon) dépendent d’un bouton explicite dans leur page. Le Dashboard et les exports sont en lecture seule. La page Applications peut enregistrer ou modifier les brouillons et leur statut de revue, mais ne possède pas d’action d’envoi. Les contrôles du workflow empêchent de régénérer silencieusement une candidature déjà approuvée.

## Sécurité et confidentialité

- Les secrets réels résident dans `.env`, jamais dans le dépôt ou les logs ; le fichier modèle ne contient que des valeurs vides ou locales.
- Les CV, profils, base SQLite, environnements virtuels, caches Hugging Face, clés SSH et artefacts d’exécution sont ignorés par Git.
- Les contenus du profil/CV et les textes de candidature ne sont pas inclus dans les exports de dashboard. Les feuilles n’exportent que des colonnes métier autorisées.
- Les URL d’API sont affichées sans identifiants. Les coordonnées affichées/exportées peuvent rester des données personnelles : garder l’instance et ses sauvegardes privées.
- Aucun envoi d’email, candidature ou formulaire n’est effectué par le projet.

## Décisions et limites

SQLite et un Streamlit local gardent le prototype facile à exploiter, mais n’apportent ni authentification applicative ni forte concurrence multi-utilisateur. Les résultats ATS dépendent des formats publics observés. Le scoring est heuristique et ne remplace pas une revue humaine. Le cache d’analyse/matching dépend des empreintes et dates enregistrées. La découverte ne garantit pas l’exhaustivité des entreprises ou contacts.

L’interface appelle encore certains agents directement et l’orchestration n’est pas un moteur de tâches asynchrones ; les tâches longues occupent donc une session Streamlit. `graph/` reste un espace réservé sans rôle dans le chemin d’exécution actuel. Ces éléments sont connus et n’ont pas été transformés en nouvelles fonctionnalités de stabilisation.
