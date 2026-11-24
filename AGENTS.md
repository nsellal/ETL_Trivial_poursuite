# AGENTS.md

Guide pour les agents IA (et les humains) qui travaillent sur ce dépôt.
À tenir à jour au fil du projet.

## Objectif du projet

Pipeline ETL qui mesure la capacité d'un LLM local à répondre à des
questions de Trivial Poursuite :

1. **Extraction** : récupérer l'intégralité des questions d'Open Trivia
   Database (OpenTDB, https://opentdb.com).
2. **Benchmark** : poser chaque question à un modèle local (LM Studio, API
   compatible OpenAI) avec plusieurs versions de prompt.
3. **Transformation** : nettoyer et modéliser questions et réponses avec dbt
   sur DuckDB (couches silver et gold).
4. **Restitution** : visualiser les résultats (Streamlit + Plotly).

## Architecture

**Pour tout ce qui concerne l'architecture, se référer à
[`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md)** :
couches bronze / silver / gold, flux de données, organisation dbt,
arborescence, formats des fichiers, **convention de jointure
`question_id`** et composants.

Toute modification de l'architecture (nouvelle couche, nouveau fichier de
données, changement de schéma, nouveau script) doit être reportée dans
`ARCHITECTURE.md`, pas dans ce fichier.

## Avancement

| Étape                                   | État                                    |
|-----------------------------------------|-----------------------------------------|
| Extraction bronze (`extract_bronze.py`) | Fait : 5 299 questions                  |
| Benchmark (`run_benchmark.py`)          | Fait : run Gemma 4 12B × `v2_strict` (5 297 réponses) |
| Autres runs (prompts, 2ᵉ modèle)        | À faire                                 |
| dbt silver (staging, intermediate)      | À faire                                 |
| dbt gold (marts)                        | À faire                                 |
| Application Streamlit                   | À faire                                 |
| README (méthodologie, setup)            | À faire                                 |

## Environnement

- Python 3.14 avec un virtualenv dans `.venv/` (ignoré par git).
- Installation : `pip install -r requirements.txt` (versions figées).
- Dépendances principales : `requests`, `pandas`, `openai`, `duckdb`,
  `dbt-core` + `dbt-duckdb` (dbt Core **1.x**, pas la v2), `streamlit`,
  `plotly`.
- **Toutes les commandes se lancent depuis la racine du projet**, sous la
  forme `python src/<script>.py`. Python ajoute automatiquement `src/` aux
  chemins d'import, donc `from config import ...` fonctionne.

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
- Les textes restent **encodés en `url3986`** dans le CSV (bronze brute).

## Benchmark LLM

### Configuration : `src/config.py`

- `BRONZE_DIR`, `latest_questions_csv()` : renvoie le CSV de questions le plus
  récent (tri sur le timestamp du nom).
- `AI_ANSWERS_RAW_CSV = bronze/ai_answers_raw.csv` : réponses brutes du
  modèle.
- Modèle local, surchargeable par variables d'environnement :
  - `LLM_BASE_URL` (défaut `http://localhost:1234/v1`, LM Studio ;
    Ollama : `http://localhost:11434/v1`)
  - `LLM_MODEL` (défaut `google/gemma-4-12b-qat`)

### Prompts : `src/prompts.py`

Prompts versionnés dans le dict `PROMPTS` ; chaque réponse conserve
l'identifiant **et** le texte du prompt utilisé.

| ID           | Description                                   |
|--------------|-----------------------------------------------|
| `v1_simple`  | Consigne minimale                             |
| `v2_strict`  | Réponse = une seule lettre, rien d'autre (**défaut**) |
| `v3_fewshot` | `v2_strict` + 2 exemples résolus              |

Emplacements : `{question}`, `{choices}` (une ligne par choix `A) ...`),
`{letters}` (`A, B, C, D` ou `A, B`).

**Ne jamais modifier un prompt existant** : créer une nouvelle version
(`v4_...`) pour garder les résultats comparables.

### Lancer le benchmark : `src/run_benchmark.py`

LM Studio doit tourner (onglet *Developer* → *Start Server*) avec le modèle
chargé.

```bash
python src/run_benchmark.py --limit 20                    # test rapide
caffeinate -i python src/run_benchmark.py                 # run complet (prompt par défaut)
caffeinate -i python src/run_benchmark.py --prompt v3_fewshot
caffeinate -i python src/run_benchmark.py --model <id du modèle dans LM Studio>
```

- `caffeinate -i` (macOS) empêche la mise en veille pendant le run.
- **Reprise automatique** : le script saute les triplets
  `(question_id, model, prompt_id)` déjà présents dans le CSV. Après une
  coupure, relancer la même commande. Un nouveau prompt ou un nouveau modèle
  **s'ajoute** au même fichier, sans rien écraser.
- Écriture ligne par ligne avec `flush()` : rien n'est perdu en cas d'arrêt.
- Le script lit le CSV bronze, **décode les textes en mémoire**
  (`urllib.parse.unquote`), calcule le `question_id` et retire les 2 doublons
  de la source : 5 297 questions posées. Le CSV bronze n'est pas modifié.

Choix techniques à respecter :

- QCM présenté avec des **lettres** ; le modèle doit répondre **une seule
  lettre**. `ai_correct` compare des lettres, jamais du texte libre.
- Ordre des choix **mélangé mais reproductible** :
  `random.Random(question_id).shuffle(...)`. Vrai/faux : toujours
  `A) True`, `B) False`.
- `temperature=0`, `max_tokens=8`, **`reasoning_effort="none"`** :
  Gemma 4 est un modèle à réflexion ; sans ce paramètre, il consomme ses
  tokens à « réfléchir » et renvoie une réponse vide.
- `parse_letter()` accepte `B`, `B)`, `(B)`, `Answer: B` en début de
  réponse ; sinon `ai_answer` est vide (réponse inexploitable).

### Test du modèle : `src/test_llm.py`

```bash
python src/test_llm.py
```

Envoie une question au modèle et affiche la réponse brute et le temps de
réponse. Utile pour vérifier que LM Studio répond avant un run.

## Règles git (commit et push)

Le dépôt est partagé à deux : ces règles évitent les historiques divergents
et le travail en double.

1. **`git pull` avant de commencer à travailler**, et à nouveau **juste
   avant chaque `git push`**.
2. **Committer petit et souvent** : un commit par étape validée (un script
   qui fonctionne, un modèle dbt qui passe ses tests, une page Streamlit qui
   s'affiche). Ne jamais accumuler une demi-journée de travail non commitée.
3. **Pousser après chaque commit** (ou au minimum en fin de session), pour
   que le binôme travaille toujours sur la dernière version.
4. **Ne committer que du code qui fonctionne** : tester avant de committer.
5. **Ne jamais committer un fichier de résultats incomplet** : attendre la
   fin d'un run de benchmark avant de committer `bronze/ai_answers_raw.csv`.
6. Messages de commit **en français**, au présent ou au nom, décrivant le
   *quoi* : `Ajout du modèle stg_questions`, `Run Gemma 4 12B, prompt
   v3_fewshot`.
7. **Pas de `git push --force`** sur `main`.
8. En cas de push refusé (`rejected … fetch first`) : `git pull --no-rebase
   --no-edit`, vérifier qu'il n'y a pas de conflit, puis `git push`.
9. **Annoncer au binôme** ce qu'on attaque avant de commencer une étape,
   pour ne pas coder deux fois la même chose.
10. Ne jamais committer de secrets, `.env`, `CLAUDE.md`, ni la base
    `*.duckdb` (reconstruite par `dbt run`).

## Conventions

- Code, commentaires, docstrings et messages de commit **en français**.
- Chemins construits à partir de la racine du projet (`Path(__file__)`),
  jamais de chemin absolu en dur.
- Ignorés par git (`.gitignore`) : `.venv/`, `__pycache__/`, `*.pyc`,
  `.DS_Store`, `*.duckdb*`, `target/`, `logs/`, `dbt_packages/`, `.env`,
  `CLAUDE.md`, `.user.yml` (identifiant anonyme créé par dbt).
- Ne jamais committer de secrets : utiliser `.env` / variables
  d'environnement. Attention à `profiles.yml`, qui est versionné.
- Dépôt GitHub : `nsellal/ETL_Trivial_poursuite`, branche `main`.
