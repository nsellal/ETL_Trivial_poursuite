# AGENTS.md

Guide pour les agents IA (et les humains) qui travaillent sur ce dépôt.
À tenir à jour au fil du projet.

## Objectif du projet

Pipeline ETL qui mesure la capacité d'un LLM local à répondre à des
questions de Trivial poursuite :

1. **Extraction** : récupérer l'intégralité des questions d'Open Trivia
   Database (OpenTDB, https://opentdb.com).
2. **Benchmark** : poser chaque question à un modèle local (LM Studio, API
   compatible OpenAI) avec plusieurs versions de prompt.
3. **Transformation** : nettoyer et modéliser questions et réponses avec dbt
   sur DuckDB.
4. **Restitution** : visualiser les résultats (Streamlit + Plotly, prévu).

## Architecture

**Pour tout ce qui concerne l'architecture, se référer à
[`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md)** :
couches bronze / silver / gold, flux de données, organisation dbt,
arborescence, formats des fichiers et composants.

Toute modification de l'architecture (nouvelle couche, nouveau fichier de
données, changement de schéma, nouveau script) doit être reportée dans
`ARCHITECTURE.md`, pas dans ce fichier.

## Environnement

- Python avec un virtualenv dans `.venv/` (ignoré par git).
- Installation : `pip install -r requirements.txt`
- Dépendances principales : `requests`, `pandas`, `openai`, `duckdb`,
  `dbt-core` + `dbt-duckdb`, `streamlit`, `plotly`.
- Toutes les commandes se lancent **depuis la racine du projet**.

## Extraction bronze : `src/extract_bronze.py`

```bash
python src/extract_bronze.py                         # scraping complet
python src/extract_bronze.py --categories 9 17       # test sur 2 catégories
python src/extract_bronze.py --resume bronze/questions_raw_<ts>.csv   # reprise
```

Points importants :

- **Jeton de session** OpenTDB : l'API ne renvoie jamais deux fois la même
  question. Si le jeton expire (code 3), on en redemande un ; les doublons
  éventuels sont à supprimer en silver.
- Parcours par couple **(catégorie, difficulté)**, lots de 50 questions max,
  en s'appuyant sur `api_count.php` pour connaître le total attendu.
- **Limite de l'API : 1 appel / 5 s.** `MIN_DELAY_S = 6.5` pour garder une
  marge, avec backoff exponentiel sur HTTP 429 / code 5. Ne pas réduire ce
  délai.
- Sortie `bronze/questions_raw_<timestamp Unix>.csv` : un nouveau fichier par
  exécution, jamais d'écrasement. Écriture atomique (`.csv.tmp` puis
  renommage) et sauvegarde après chaque couple.
- `--resume` ne récupère que les couples incomplets.
- Format du CSV produit : voir `documentation/ARCHITECTURE.md`.

## Benchmark LLM

### Configuration : `src/config.py`

- `BRONZE_DIR`, `latest_questions_csv()` : renvoie le CSV de questions le plus
  récent (tri sur le timestamp du nom).
- `AI_ANSWERS_RAW_CSV = bronze/ai_answers_raw.csv` : réponses brutes du
  modèle.
- Modèle local, surchargeable par variables d'environnement :
  - `LLM_BASE_URL` (défaut `http://localhost:1234/v1`, LM Studio)
  - `LLM_MODEL` (défaut `google/gemma-4-12b-qat`)

### Prompts : `src/prompts.py`

Prompts versionnés dans le dict `PROMPTS` ; chaque réponse doit conserver
l'identifiant **et** le texte du prompt utilisé.

| ID           | Description                                   |
|--------------|-----------------------------------------------|
| `v1_simple`  | Consigne minimale                             |
| `v2_strict`  | Réponse = une seule lettre, rien d'autre (**défaut**) |
| `v3_fewshot` | `v2_strict` + 2 exemples résolus              |

Emplacements : `{question}`, `{choices}` (une ligne par choix `A) ...`),
`{letters}` (`A, B, C, D` ou `A, B`).

Ne jamais modifier un prompt existant : créer une nouvelle version (`v4_...`)
pour garder les résultats comparables.

### Test du modèle : `src/test_llm.py`

```bash
cd src && python test_llm.py
```

Envoie une question au modèle (`temperature=0`, `max_tokens=8`,
`reasoning_effort="none"` pour couper la phase de réflexion de Gemma 4) et
affiche la réponse brute et le temps de réponse. LM Studio doit tourner avec
le modèle chargé.

> `test_llm.py` fait `from config import ...` : il faut le lancer depuis
> `src/` (ou avec `src/` dans le `PYTHONPATH`).

## Reste à faire (constaté dans le code)

- [ ] `src/run_benchmark.py` : référencé par `config.py` et `prompts.py`,
      pas encore écrit. Doit produire `bronze/ai_answers_raw.csv`.
- [ ] Remplir `dbt_project.yml` et `profiles.yml` (dbt-duckdb).
- [ ] Modèles dbt : décodage url3986, dédoublonnage, jointure questions ×
      réponses, métriques gold.
- [ ] Application Streamlit de restitution.

## Conventions

- Code, commentaires, docstrings et messages de commit **en français**.
- Chemins construits à partir de la racine du projet (`Path(__file__)`),
  jamais de chemin absolu en dur.
- Ignorés par git (`.gitignore`) : `.venv/`, `__pycache__/`, `*.duckdb*`,
  `target/`, `logs/`, `dbt_packages/`, `.env`, `CLAUDE.md`.
- Ne jamais committer de secrets : utiliser `.env` / variables
  d'environnement. Attention à `profiles.yml`, qui est versionné.
- Dépôt GitHub : `nsellal/ETL_Trivial_poursuite`, branche `main`.
