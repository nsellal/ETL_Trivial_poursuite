# ARCHITECTURE.md

Architecture du projet ETL Trivial Poursuite. À tenir à jour à chaque
évolution de la structure, des couches ou des formats de données.

## Vue d'ensemble

Le pipeline suit une architecture **médaillon** (bronze → silver → gold) :

```
 OpenTDB (API)                         LLM local (LM Studio)
      │                                        ▲
      │ src/extract_bronze.py                  │ src/run_benchmark.py (à écrire)
      ▼                                        │
 bronze/questions_raw_<ts>.csv ────────────────┘
                                               │
                                               ▼
                               bronze/ai_answers_raw.csv
      │                                        │
      └───────────────┬────────────────────────┘
                      │ dbt + DuckDB (src/models/)
                      ▼
          silver/  (staging → intermediate)
                      │
                      ▼
          gold/    (marts : métriques du benchmark)
                      │
                      ▼
          Streamlit + Plotly (restitution, prévu)
```

## Couches

| Couche   | Dossier    | Contenu                                                        | État        |
|----------|------------|----------------------------------------------------------------|-------------|
| Bronze   | `bronze/`  | Données brutes telles que renvoyées par les sources (CSV)       | En place    |
| Silver   | `silver/`  | Données décodées, dédoublonnées, typées                         | Vide        |
| Gold     | `gold/`    | Tables d'analyse (scores par catégorie, difficulté, prompt...)  | Vide        |

Règles :

- **Bronze reste brute** : aucune transformation dans les scripts
  d'extraction, les données sont stockées telles que l'API les renvoie.
- **Silver** : décodage `url3986`, suppression des doublons (possibles après
  un renouvellement du jeton OpenTDB), typage.
- **Gold** : agrégats prêts à l'analyse.

## Transformation (dbt)

- Moteur : **DuckDB** via `dbt-duckdb` (fichiers `*.duckdb*` ignorés par git).
- Modèles dans `src/models/`, structure classique :
  - `staging/` : une vue par source bronze, nettoyage minimal ;
  - `intermediate/` : jointures et logique métier ;
  - `marts/` : tables finales (gold).
- `dbt_project.yml` et `profiles.yml` à la racine : encore vides.

## Arborescence

```
.
├── AGENTS.md                    # guide pour les agents
├── documentation/
│   └── ARCHITECTURE.md          # ce fichier
├── bronze/                      # couche bronze (CSV bruts, versionnés)
│   └── questions_raw_<ts>.csv   # sortie de extract_bronze.py
├── silver/  gold/               # couches suivantes (vides)
├── src/
│   ├── config.py                # chemins + configuration du LLM local
│   ├── extract_bronze.py        # scraper OpenTDB → bronze
│   ├── prompts.py               # prompts versionnés du benchmark
│   ├── test_llm.py              # test manuel d'un appel au modèle local
│   └── models/                  # modèles dbt (staging / intermediate / marts)
├── dbt_project.yml              # vide pour l'instant
├── profiles.yml                 # vide pour l'instant
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
en silver avec `urllib.parse.unquote`.

Dernière extraction (`questions_raw_1795440205.csv`) : **5 299 questions**,
24 catégories ; 4 510 `multiple` / 789 `boolean` ;
1 770 easy / 2 424 medium / 1 105 hard.

### `bronze/ai_answers_raw.csv`

Réponses brutes du modèle, produites par `run_benchmark.py` (à écrire).
Chaque réponse doit conserver l'identifiant **et** le texte du prompt utilisé
(voir `src/prompts.py`). Schéma à définir.

## Composants

| Fichier                 | Rôle                                                          |
|-------------------------|---------------------------------------------------------------|
| `src/config.py`         | Chemins (`BRONZE_DIR`, `AI_ANSWERS_RAW_CSV`) et config du LLM |
| `src/extract_bronze.py` | Scraping complet d'OpenTDB vers la couche bronze              |
| `src/prompts.py`        | Prompts versionnés (`PROMPTS`, `DEFAULT_PROMPT_ID`)           |
| `src/test_llm.py`       | Test manuel d'un appel au modèle local                        |
| `src/run_benchmark.py`  | *À écrire* : questions → LLM → `ai_answers_raw.csv`           |

Sources externes :

- **OpenTDB** (https://opentdb.com) : limite 1 appel / 5 s, jeton de session.
- **LM Studio** : API compatible OpenAI, `LLM_BASE_URL` / `LLM_MODEL`
  (défauts `http://localhost:1234/v1`, `google/gemma-4-12b-qat`).
