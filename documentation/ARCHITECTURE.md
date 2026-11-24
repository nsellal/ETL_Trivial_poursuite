# ARCHITECTURE.md

Architecture du projet ETL Trivial Poursuite. À tenir à jour à chaque
évolution de la structure, des couches ou des formats de données.

## Vue d'ensemble

Le pipeline suit une architecture **médaillon** (bronze → silver → gold) :

```
 OpenTDB (API)                         LLM local (LM Studio)
      │                                        ▲
      │ src/extract_bronze.py                  │ src/run_benchmark.py
      ▼                                        │
 bronze/questions_raw_<ts>.csv ────────────────┘
                                               │
                                               ▼
                               bronze/ai_answers_raw.csv
      │                                        │
      └───────────────┬────────────────────────┘
                      │ src/bronze_to_silver.py
                      ▼
          silver/*.parquet  (conversion typée des CSV)
                      │ dbt + DuckDB (src/models/)      [à faire]
                      ▼
          gold/warehouse.duckdb
            (staging → intermediate → marts)            [à faire]
                      │
                      ▼
          Streamlit + Plotly (restitution)              [à faire]
```

## Couches

| Couche   | Emplacement                          | Contenu                                                        | État        |
|----------|--------------------------------------|----------------------------------------------------------------|-------------|
| Bronze   | `bronze/` (CSV versionnés)           | Données brutes telles que renvoyées par les sources            | En place    |
| Silver   | `silver/` (parquet)          | Parquet typés, conversion des CSV bronze                       | À faire     |
| Gold     | `gold/warehouse.duckdb`      | Base DuckDB dbt : staging → intermediate → marts               | À faire     |

Règles :

- **Bronze reste brute** : aucune transformation dans les scripts qui
  écrivent en bronze. Les questions sont stockées telles que l'API les
  renvoie ; les réponses telles que le modèle les produit.
- **Silver** : conversion en Parquet (typé, compressé), sans modifier le
  contenu (textes encore encodés, doublons conservés).
- **Gold** : nettoyage en staging (décodage `url3986`, calcul du
  `question_id`, dédoublonnage, typage), jointures en intermediate, agrégats
  prêts à l'analyse et au dashboard en marts.

> **Emplacements retenus :**
>
> - `silver/*.parquet` : produits par `src/bronze_to_silver.py`, un Parquet
>   par CSV bronze (même nom, même contenu, mais typé et compressé). Ce sont
>   les **sources lues par dbt** (`read_parquet` dans `_sources.yml`) : lancer
>   ce script avant `dbt run`.
> - `gold/warehouse.duckdb` : base DuckDB construite par dbt (chemin fixé dans
>   `profiles.yml`, repris par `config.WAREHOUSE_PATH` pour Streamlit). Elle
>   contient les schémas `staging`, `intermediate` et `marts` (couche gold,
>   sur le modèle du cas Spotify). Ignorée par git, reconstruite par `dbt run`.

## Convention de jointure : `question_id`

Le CSV de questions n'a pas d'identifiant (l'API n'en fournit pas). Les
réponses du benchmark et les modèles dbt utilisent donc **le même
identifiant calculé** :

> `question_id` = les **12 premiers caractères** du **MD5** de
> `question + "|" + correct_answer`, calculé sur les textes **décodés**.

| Où                       | Calcul                                                                          |
|--------------------------|---------------------------------------------------------------------------------|
| Python (`run_benchmark.py`) | `hashlib.md5(f"{question}\|{correct_answer}".encode("utf-8")).hexdigest()[:12]` |
| SQL DuckDB (staging dbt)  | `left(md5(url_decode(question) \|\| '\|' \|\| url_decode(correct_answer)), 12)`   |

**Vérifié** sur les données réelles : les 10 594 réponses de
`ai_answers_raw.csv` (2 runs) se joignent toutes à un `question_id` calculé en SQL
depuis `questions_raw_1795440205.csv` (aucune réponse orpheline).

Points d'attention :

- Décoder **avant** de hacher (`url_decode` en SQL, `unquote` en Python) ;
  hacher le texte encodé donnerait des identifiants différents.
- Le séparateur est `|`, sans espace.
- Le CSV source contient **2 doublons** (même question, même bonne réponse) :
  5 299 lignes mais 5 297 `question_id` distincts. La transformation en vers la couche silver doit
  dédoublonner (`qualify row_number() over (partition by question_id order
  by scraped_at) = 1`) avant de tester `unique` sur `question_id`.
- Aucun caractère `+` brut dans les textes encodés : `url_decode` (DuckDB) et
  `unquote` (Python) donnent donc exactement le même texte.

## Transformation (dbt) — à faire

- Moteur : **DuckDB** via `dbt-duckdb`, dbt Core 1.x (fichiers `*.duckdb*`
  ignorés par git, la base est reconstruite par `dbt run`).
- Modèles dans `src/models/` (à déclarer via `model-paths` dans
  `dbt_project.yml`), structure classique :
  - `staging/` : une vue par source bronze (décodage, typage, `question_id`,
    dédoublonnage) ;
  - `intermediate/` : jointure réponses × questions et colonnes dérivées
    (ex. `random_baseline` = 1 / nombre de choix, `is_unparsable`) ;
  - `marts/` : tables finales gold (classement modèle × prompt, précision par
    catégorie, par difficulté...).
- Sources : les Parquet de `silver/` (conversion des CSV bronze par
  `src/bronze_to_silver.py`), lus avec `read_parquet`.
- Tests prévus : `unique` / `not_null` sur `question_id`, `accepted_values`
  sur `difficulty` et `type`, `relationships` réponses → questions.
### Configuration dbt

`dbt_project.yml` et `profiles.yml` sont à la racine ; lancer dbt depuis la
racine avec `--profiles-dir .` (ex. `dbt debug --profiles-dir .`).

| Fichier           | Réglage                                                          |
|-------------------|------------------------------------------------------------------|
| `dbt_project.yml` | Projet `trivial_poursuite`, profil `trivial_pst`                 |
| `dbt_project.yml` | Modèles de `marts/` matérialisés en **tables** (`+materialized: table`) |
| `profiles.yml`    | Profil `trivial_pst`, cible `dev` : DuckDB `gold/warehouse.duckdb`, 4 threads |

`dbt debug --profiles-dir .` passe (connexion DuckDB OK).

`model-paths` et `macro-paths` pointent vers `src/models` et `src/macros`
(les modèles et la macro `generate_schema_name` sont rangés dans `src/`).

## Arborescence

```
.
├── AGENTS.md                    # guide pour les agents
├── documentation/
│   └── ARCHITECTURE.md          # ce fichier
├── bronze/                      # couche bronze (CSV bruts, versionnés)
│   ├── questions_raw_<ts>.csv   # sortie de extract_bronze.py
│   └── ai_answers_raw.csv       # sortie de run_benchmark.py
├── silver/                      # Parquet produits par bronze_to_silver.py
├── gold/
│   └── warehouse.duckdb         # base DuckDB construite par dbt (non versionnée)
├── src/
│   ├── config.py                # chemins + configuration du LLM local
│   ├── bronze_to_silver.py      # CSV bronze → Parquet silver
│   ├── extract_bronze.py        # scraper OpenTDB → bronze
│   ├── prompts.py               # prompts versionnés du benchmark
│   ├── run_benchmark.py         # questions → LLM → ai_answers_raw.csv
│   ├── test_llm.py              # test manuel d'un appel au modèle local
│   └── models/                  # modèles dbt (staging / intermediate / marts), vides
├── dbt_project.yml              # projet dbt (profil trivial_pst)
├── profiles.yml                 # connexion DuckDB → gold/warehouse.duckdb
└── requirements.txt
```

## Formats de données

### `bronze/questions_raw_<timestamp>.csv`

Produit par `src/extract_bronze.py`. `<timestamp>` = timestamp Unix (s) du
lancement : un nouveau fichier par exécution, jamais d'écrasement.
`config.latest_questions_csv()` renvoie le plus récent.

| Colonne             | Description                                              |
|---------------------|----------------------------------------------------------|
| `category_id`       | ID de catégorie OpenTDB                                  |
| `category`          | Nom de la catégorie (encodé)                             |
| `type`              | `multiple` (4 choix) ou `boolean` (Vrai/Faux)            |
| `difficulty`        | `easy`, `medium` ou `hard`                               |
| `question`          | Texte de la question (encodé)                            |
| `correct_answer`    | Bonne réponse (encodée)                                  |
| `incorrect_answers` | Liste JSON des mauvaises réponses (chaque élément encodé) |
| `encoding`          | Encodage des textes (`url3986`)                          |
| `scraped_at`        | Horodatage UTC ISO 8601 de l'extraction                  |

Les textes sont en **`url3986`** (RFC 3986) : sans perte, sans virgule,
guillemet ni retour à la ligne, donc le CSV ne peut pas être cassé. Décodage
avec `url_decode` (DuckDB) ou `urllib.parse.unquote` (Python).

Dernière extraction (`questions_raw_1795440205.csv`) : **5 299 questions**
(5 297 `question_id` distincts), 24 catégories ; 4 510 `multiple` /
789 `boolean` ; 1 770 easy / 2 424 medium / 1 105 hard.

### `bronze/ai_answers_raw.csv`

Produit par `src/run_benchmark.py`. **Une ligne = la réponse d'un modèle à
une question avec un prompt donné.** Clé unique :
`(question_id, model, prompt_id)`. Les runs successifs s'ajoutent à la suite
dans le même fichier.

| Colonne           | Type      | Description                                                        |
|-------------------|-----------|--------------------------------------------------------------------|
| `question_id`     | texte     | Identifiant de la question (voir convention ci-dessus)             |
| `model`           | texte     | Identifiant du modèle dans LM Studio (ex. `google/gemma-4-12b-qat`) |
| `prompt_id`       | texte     | Version du prompt (`v1_simple`, `v2_strict`, `v3_fewshot`)          |
| `prompt_text`     | texte     | Texte exact envoyé au modèle (multi-lignes, entre guillemets dans le CSV) |
| `choices_order`   | JSON      | Liste des choix dans l'ordre présenté (A, B, C, D)                 |
| `expected_letter` | texte     | Lettre de la bonne réponse                                          |
| `raw_output`      | texte     | Réponse brute du modèle, telle quelle                              |
| `ai_answer`       | texte     | Lettre extraite de `raw_output` ; **vide** si inexploitable        |
| `ai_correct`      | booléen   | `True` si `ai_answer` = `expected_letter`                          |
| `response_time`   | décimal   | Temps de génération en secondes (`time.perf_counter`)              |
| `answered_at`     | horodatage | Date/heure UTC ISO 8601 de la réponse                             |

Contenu actuel : **10 594 réponses**, 2 runs complets de 5 297 réponses
(aucun doublon sur la clé unique) :

| Modèle                   | Prompt      | Réponses | Précision | Inexploitables | Temps médian | Date du run |
|--------------------------|-------------|---------:|----------:|---------------:|-------------:|-------------|
| `google/gemma-4-12b-qat` | `v2_strict` |    5 297 |    72,4 % |              7 |       0,83 s | 2026-11-23  |
| `prism-ml/bonsai-27b`    | `v2_strict` |    5 297 |    59,5 % |             46 |       0,37 s | 2026-11-24  |

> Les temps de réponse dépendent de la machine qui fait tourner LM Studio :
> ils ne sont comparables qu'entre runs faits sur la même machine.

## Composants

| Fichier                 | Rôle                                                          |
|-------------------------|---------------------------------------------------------------|
| `src/config.py`         | Chemins (`BRONZE_DIR`, `AI_ANSWERS_RAW_CSV`, `latest_questions_csv()`, `SILVER_DIR`, `GOLD_DIR`, `WAREHOUSE_PATH`) et config du LLM |
| `src/bronze_to_silver.py` | Conversion des CSV bronze en Parquet dans `silver/`         |
| `src/extract_bronze.py` | Scraping complet d'OpenTDB vers la couche bronze              |
| `src/prompts.py`        | Prompts versionnés (`PROMPTS`, `DEFAULT_PROMPT_ID`)           |
| `src/run_benchmark.py`  | Questions → LLM → `ai_answers_raw.csv` (reprise automatique)  |
| `src/test_llm.py`       | Test manuel d'un appel au modèle local                        |

Sources externes :

- **OpenTDB** (https://opentdb.com) : limite 1 appel / 5 s, jeton de session.
- **LM Studio** : API compatible OpenAI, `LLM_BASE_URL` / `LLM_MODEL`
  (défauts `http://localhost:1234/v1`, `google/gemma-4-12b-qat`).
