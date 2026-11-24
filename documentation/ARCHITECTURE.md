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
                      │ (décodage, question_id, dédoublonnage, jointure)
                      ▼
          silver/obt_answers.parquet   (One Big Table : 1 ligne = 1 réponse)
                      │ dbt + DuckDB (src/models/)
                      ▼
          gold/warehouse.duckdb
            staging.stg_obt_answers → intermediate.int_answers → marts.*  [marts à faire]
                      │
                      ▼
          Streamlit + Plotly (restitution)              [à faire]
```

## Couches

| Couche   | Emplacement                          | Contenu                                                        | État        |
|----------|--------------------------------------|----------------------------------------------------------------|-------------|
| Bronze   | `bronze/` (CSV versionnés)           | Données brutes telles que renvoyées par les sources            | En place    |
| Silver   | `silver/obt_answers.parquet` | One Big Table : réponses des IA × questions, décodée, dédoublonnée | En place    |
| Gold     | `gold/warehouse.duckdb`      | Base DuckDB dbt : staging → intermediate → marts               | Staging et intermediate en place, marts à faire |

Règles :

- **Bronze reste brute** : aucune transformation dans les scripts qui
  écrivent en bronze. Les questions sont stockées telles que l'API les
  renvoie ; les réponses telles que le modèle les produit.
- **Silver** (`src/bronze_to_silver.py`) : **une seule table, l'OBT**
  (One Big Table) `silver/obt_answers.parquet`. Grain : **1 ligne = 1 réponse**
  (question × modèle × prompt), enrichie de toutes les informations de sa
  question (dénormalisation voulue : une question apparaît une fois par run).
  Étapes, dans l'ordre :
  - **dates** (`*_at`) → **timestamp Unix en secondes** (entier UTC) ;
  - **textes url3986 → UTF-8** (`unquote` strict + **vérification
    aller-retour**) ; `incorrect_answers` → liste ; colonne `encoding` supprimée ;
  - **`question_id`** calculé (voir convention ci-dessous) ;
  - **dédoublonnage** : questions sur `question_id`, réponses sur
    `(question_id, model, prompt_id)`, en gardant le plus récent ;
  - **jointure** `answers.merge(questions, validate="many_to_one")` :
    plante si une question est en double ; le script vérifie ensuite que
    **chaque réponse a trouvé sa question** (nb de lignes OBT = nb de réponses).
- **Pourquoi une OBT ?** Volume faible (~5 300 lignes par run), DuckDB et
  Parquet en colonnes (la redondance se compresse bien), usage uniquement
  analytique : les marts et Streamlit lisent une seule table, sans jointure.
  Comparer les modèles = un `GROUP BY model`, et ajouter un modèle ne demande
  aucun nouveau fichier.
- **Gold** : typage et nettoyage technique en staging, colonnes dérivées en
  intermediate, agrégats prêts à l'analyse et au dashboard en marts. **Pas
  de `url_decode` en staging** : les textes sont déjà décodés, un second
  décodage abîmerait les textes contenant `%` (8 questions, ex. « 78% »).

> **Emplacements retenus :**
>
> - `silver/obt_answers.parquet` : produit par `src/bronze_to_silver.py`
>   (questions du CSV le plus récent × toutes les réponses). C'est la
>   **seule source lue par dbt** (source `silver`, table `obt_answers` dans
>   `_sources.yml`, appelée avec `{{ source('silver', 'obt_answers') }}`) :
>   lancer ce script avant `dbt run`.
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
| Python (`bronze_to_silver.py`) | même formule, fonction `question_id()` : calculée en silver avant la jointure |

**Vérifié** sur les données réelles : les 10 594 réponses de
`ai_answers_raw.csv` (2 runs) trouvent toutes leur question lors de la
jointure dans `bronze_to_silver.py` (contrôle du nombre de lignes, aucune
réponse orpheline).

Points d'attention :

- Hacher le texte **décodé** (fait en silver par `bronze_to_silver.py`,
  `unquote` dans `run_benchmark.py`) ; hacher le texte encodé donnerait des
  identifiants différents.
- Le séparateur est `|`, sans espace.
- Le CSV source contient **2 doublons** (même question, même bonne réponse) :
  5 299 lignes mais 5 297 `question_id` distincts. `bronze_to_silver.py`
  dédoublonne **avant la jointure**, sinon chaque réponse à une question en
  double serait dupliquée.
- Aucun caractère `+` brut dans les textes encodés : le décodage ne dépend
  donc pas de l'interprétation de `+` (espace ou non).

## Transformation (dbt)

- Moteur : **DuckDB** via `dbt-duckdb`, dbt Core 1.x (fichiers `*.duckdb*`
  ignorés par git, la base est reconstruite par `dbt run`).
- Source unique : `silver/obt_answers.parquet`, lue avec `read_parquet`.
- Modèles dans `src/models/`, un schéma DuckDB par couche :
  - `staging/stg_obt_answers` (**vue**, schéma `staging`) : clé `answer_id`
    (`question_id|model|prompt_id`), `trim`, typage (`choices_order` JSON →
    liste), `category_group` (partie avant « : »), dates en timestamp Unix
    + colonnes `answered_at_repr` / `scraped_at_repr` au format
    `JJ/MM/AAAA HH:MM:SS`, heure de Paris ;
  - `intermediate/int_answers` (**table**, schéma `intermediate`) : colonnes
    dérivées `nb_choices`, `is_unparsable`, `random_baseline` (= 1 / nb
    choix), `question_length`, `output_length`. Table lue par les marts et
    Streamlit ;
  - `marts/` (tables, schéma `marts`) : classement modèle × prompt,
    précision par catégorie, par difficulté... — à faire.
- Tests : `unique` / `not_null` sur `answer_id`, `not_null` sur les
  colonnes clés et les dates, `accepted_values` sur `type`, `difficulty`,
  `expected_letter`, `ai_answer`, `nb_choices`, `not_empty` (macro
  `src/macros/test_not_empty.sql`) sur les deux modèles. Le contrôle
  « chaque réponse a sa question » (ex-test `relationships`) est fait en
  Python au moment de la jointure.

### Configuration dbt

`dbt_project.yml` et `profiles.yml` sont à la racine ; lancer dbt depuis la
racine avec `--profiles-dir .` (ex. `dbt debug --profiles-dir .`).

| Fichier           | Réglage                                                          |
|-------------------|------------------------------------------------------------------|
| `dbt_project.yml` | Projet `trivial_poursuite`, profil `trivial_pst`                 |
| `dbt_project.yml` | `staging` en **vues**, `intermediate` et `marts` en **tables**, chacun dans son schéma (`+schema`) |
| `profiles.yml`    | Profil `trivial_pst`, cible `dev` : DuckDB `gold/warehouse.duckdb`, 4 threads |

`dbt debug --profiles-dir .` passe (connexion DuckDB OK).

`model-paths` et `macro-paths` pointent vers `src/models` et `src/macros`
(les modèles et les macros sont rangés dans `src/`).

`generate_schema_name` (`src/macros/`) donne les schémas `staging`,
`intermediate`, `marts` tels quels (et non `main_staging`).

`dbt build --profiles-dir .` : **28 PASS** (staging + intermediate).

## Arborescence

```
.
├── AGENTS.md                    # guide pour les agents
├── documentation/
│   └── ARCHITECTURE.md          # ce fichier
├── bronze/                      # couche bronze (CSV bruts, versionnés)
│   ├── questions_raw_<ts>.csv   # sortie de extract_bronze.py
│   └── ai_answers_raw.csv       # sortie de run_benchmark.py
├── silver/
│   └── obt_answers.parquet      # One Big Table (sortie de bronze_to_silver.py)
├── gold/
│   └── warehouse.duckdb         # base DuckDB construite par dbt (non versionnée)
├── src/
│   ├── config.py                # chemins + configuration du LLM local
│   ├── bronze_to_silver.py      # CSV bronze → OBT silver (Parquet)
│   ├── extract_bronze.py        # scraper OpenTDB → bronze
│   ├── prompts.py               # prompts versionnés du benchmark
│   ├── run_benchmark.py         # questions → LLM → ai_answers_raw.csv
│   ├── test_llm.py              # test manuel d'un appel au modèle local
│   ├── macros/                  # macros dbt (schéma, test not_empty)
│   └── models/
│       ├── staging/             # stg_obt_answers
│       ├── intermediate/        # int_answers
│       └── marts/               # vide
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
fait une seule fois, en silver, par `src/bronze_to_silver.py`.

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

### `silver/obt_answers.parquet`

Produit par `src/bronze_to_silver.py`. **1 ligne = 1 réponse** (question ×
modèle × prompt), clé `(question_id, model, prompt_id)`. 19 colonnes :
toutes celles de `ai_answers_raw.csv` + celles de `questions_raw_<ts>.csv`
(sauf `encoding`), jointes sur `question_id`.

Différences avec le bronze : textes décodés en UTF-8, `incorrect_answers`
en liste (`VARCHAR[]`), `answered_at` et `scraped_at` en timestamp Unix
(secondes, `BIGINT`), doublons retirés.

Contenu actuel : **10 594 lignes** (5 297 questions × 2 runs).

## Composants

| Fichier                 | Rôle                                                          |
|-------------------------|---------------------------------------------------------------|
| `src/config.py`         | Chemins (`BRONZE_DIR`, `AI_ANSWERS_RAW_CSV`, `latest_questions_csv()`, `SILVER_DIR`, `GOLD_DIR`, `WAREHOUSE_PATH`) et config du LLM |
| `src/bronze_to_silver.py` | CSV bronze → OBT `silver/obt_answers.parquet` : décodage url3986 vérifié, dates en timestamp Unix, `question_id`, dédoublonnage, jointure contrôlée |
| `src/extract_bronze.py` | Scraping complet d'OpenTDB vers la couche bronze              |
| `src/prompts.py`        | Prompts versionnés (`PROMPTS`, `DEFAULT_PROMPT_ID`)           |
| `src/run_benchmark.py`  | Questions → LLM → `ai_answers_raw.csv` (reprise automatique)  |
| `src/test_llm.py`       | Test manuel d'un appel au modèle local                        |

Sources externes :

- **OpenTDB** (https://opentdb.com) : limite 1 appel / 5 s, jeton de session.
- **LM Studio** : API compatible OpenAI, `LLM_BASE_URL` / `LLM_MODEL`
  (défauts `http://localhost:1234/v1`, `google/gemma-4-12b-qat`).
