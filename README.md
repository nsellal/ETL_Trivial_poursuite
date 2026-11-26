# Jouons au Trivial Poursuite : benchmark de LLM locaux

Projet ETL du M1 Big Data & IA (Sup de Vinci Bordeaux), réalisé en binôme
par Nassim Sellal et Noah.

**Question posée :** des modèles de langage qui tournent en local sur un
ordinateur portable sont-ils bons en culture générale ? Et qu'est-ce qui pèse
le plus sur le résultat : le modèle, sa taille, ou la façon de lui poser la
question ?

Pour y répondre, on a construit un pipeline de data ingénierie complet :

1. collecte de **l'intégralité** des questions d'Open Trivia Database
   (5 297 questions uniques) ;
2. enrichissement par IA : chaque question est posée à plusieurs modèles
   locaux, avec plusieurs versions de prompt (**26 485 réponses**) ;
3. modélisation en **architecture médaillon** (bronze → silver → gold) avec
   **dbt** et **DuckDB** ;
4. restitution dans un **dashboard Streamlit**.

---

## Sommaire

- [Résultats clés](#résultats-clés)
- [Architecture](#architecture)
- [Méthodologie](#méthodologie)
- [Organisation du dépôt](#organisation-du-dépôt)
- [Installation et exécution](#installation-et-exécution)
- [Limites](#limites)

---

## Résultats clés

5 runs complets (modèle × prompt), 5 297 questions chacun :

| Rang | Modèle | Prompt | Précision | Gain sur le hasard | Format respecté | Temps médian |
|---:|---|---|---:|---:|---:|---:|
| 1 | Gemma 4 12B (`google/gemma-4-12b-qat`) | `v2_strict` | **72,4 %** | +43,7 pts | 99,9 % | 0,83 s |
| 2 | Qwen 3.5 9B (`qwen/qwen3.5-9b`) | `v2_strict` | **71,7 %** | +43,0 pts | 99,9 % | 0,37 s |
| 3 | Qwen 3.5 9B | `v1_simple` | 65,6 % | +36,9 pts | 90,3 % | 0,40 s |
| 4 | Gemma 4 12B | `v1_simple` | 62,0 % | +33,3 pts | 81,5 % | 0,90 s |
| 5 | Bonsai 27B (`prism-ml/bonsai-27b`) | `v2_strict` | 59,5 % | +30,8 pts | 99,1 % | 0,37 s |

Répondre au hasard donnerait **28,7 %** sur ce dataset (25 % sur un QCM à
4 choix, 50 % sur un vrai/faux).

Ce qu'on en retient :

- **Le prompt compte presque autant que le modèle.** Passer d'une consigne
  minimale (`v1_simple`) à une consigne stricte (`v2_strict`) fait gagner
  **+10,4 pts à Gemma** et **+6,1 pts à Qwen**. Avec la consigne floue, les
  modèles répondent par des phrases au lieu d'une lettre : Gemma sort du format
  dans 980 réponses sur 5 297.
- **La taille ne fait pas tout.** Bonsai 27B, le plus gros modèle testé, est
  le moins bon (−12,9 pts par rapport à Gemma 12B). Qwen 9B fait presque jeu
  égal avec Gemma 12B en répondant deux fois plus vite.
- **La difficulté OpenTDB est cohérente avec les résultats.** Gemma passe de
  82,0 % (QCM faciles) à 65,9 % (QCM difficiles).
- **Les vrais/faux difficiles sont presque du hasard** : Gemma n'y fait que
  +9,8 pts au-dessus des 50 % du hasard, contre +40,9 pts sur les QCM
  difficiles.
- **Le dataset est dominé par le divertissement.** Les catégories
  *Entertainment* représentent 53 % des questions, et ce sont les plus
  difficiles : *Video Games* (22 % du dataset) n'est réussie qu'à 58,1 % par
  Gemma, contre 96,6 % en *Art*.
- **Biais de position (axe exploratoire).** Gemma répond « B » plus souvent
  qu'elle ne le devrait (1 763 fois pour 1 454 bonnes réponses « B »). Résultat :
  quand elle répond « B », elle n'a raison que dans 66,0 % des cas, contre
  79,5 % quand elle répond « A ». Qwen ne présente pas ce biais.

Tous ces chiffres se retrouvent, filtrables, dans le dashboard.

---

## Architecture

```
          Open Trivia Database (API publique)
                      │ src/extract_bronze.py
                      ▼
   BRONZE   bronze/questions_raw_<timestamp>.csv     questions brutes (encodées url3986)
                      │ src/run_benchmark.py  ◄──── LM Studio (API compatible OpenAI)
                      ▼
            bronze/ai_answers_raw.csv               1 ligne = 1 réponse (question × modèle × prompt)
                      │ src/bronze_to_silver.py
                      ▼
   SILVER   silver/obt_answers.parquet              One Big Table : décodée, dédoublonnée, jointe
                      │ dbt + DuckDB
                      ▼
   GOLD     gold/warehouse.duckdb
              staging.stg_obt_answers       (vue)    typage, nettoyage
                      ▼
              intermediate.int_answers      (table)  colonnes dérivées
                      ▼
              marts.mart_*                  (tables) 4 marts, 1 question métier chacun
                      │
                      ▼
            src/app.py  (Streamlit + Plotly)
```

| Couche | Contenu | Format |
|---|---|---|
| **Bronze** | Données brutes, telles que renvoyées par l'API et par le modèle. Aucune transformation. | CSV |
| **Silver** | Observations propres et exploitables : textes décodés, `question_id` calculé, doublons retirés, questions et réponses réunies dans une seule table. | Parquet |
| **Gold** | Base DuckDB construite par dbt : `staging` → `intermediate` → `marts`. Chaque mart répond directement à une question métier. | DuckDB |

Le détail (formats de chaque fichier, colonnes, convention de jointure) est
dans [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md).

---

## Méthodologie

### 1. Collecte du dataset (bronze)

[`src/extract_bronze.py`](src/extract_bronze.py) récupère **toutes** les
questions d'OpenTDB. L'API impose plusieurs contraintes :

- **50 questions maximum par appel** et **1 appel toutes les 5 secondes** par
  adresse IP. Le script attend 6,5 s entre deux appels et réessaie avec un
  délai croissant en cas de refus.
- **Un jeton de session** garantit que l'API ne renvoie jamais deux fois la
  même question.
- **Parcours par couple (catégorie, difficulté)**, en comparant le nombre de
  questions reçues au total annoncé par `api_count.php`. On s'assure ainsi de
  ne rien oublier. Piège rencontré : avec un jeton, l'API renvoie le code 4
  (« jeton épuisé ») dès qu'il reste moins de questions que demandé. Le script
  réduit alors la taille du lot au lieu de s'arrêter.
- **Textes demandés en encodage `url3986`** : un encodage sans perte qui ne
  contient ni virgule ni guillemet, donc qui ne peut pas casser le CSV. Les
  textes restent encodés en bronze ; le décodage est le rôle de la couche
  silver.
- **Robustesse** : écriture atomique (fichier temporaire puis renommage),
  sauvegarde après chaque couple, option `--resume` pour reprendre après une
  coupure réseau.

Résultat : **5 299 questions**, dont 2 doublons retirés en silver, soit
**5 297 questions uniques** dans 24 catégories (4 508 QCM et 789 vrai/faux).

**Identifiant de question.** L'API ne fournit pas d'identifiant. On en crée
un : `question_id` = les 12 premiers caractères du hash MD5 de
`question|bonne réponse` (textes décodés). Il est stable : la même question
obtient toujours le même identifiant, quel que soit le script qui le calcule.

### 2. Enrichissement IA (benchmark)

[`src/run_benchmark.py`](src/run_benchmark.py) pose chaque question à un
modèle servi par **LM Studio**, via la librairie `openai` (API compatible
OpenAI, `http://localhost:1234/v1`). Pour chaque réponse, on stocke
notamment :

| Colonne | Contenu |
|---|---|
| `ai_answer` | lettre donnée par le modèle (vide si la réponse est inexploitable) |
| `ai_correct` | `True` si cette lettre est la bonne |
| `response_time` | temps de génération en secondes (`time.perf_counter()`) |
| `model`, `prompt_id`, `prompt_text` | modèle et **texte exact du prompt envoyé** (trace du prompt dans le dataset) |
| `raw_output`, `expected_letter`, `choices_order`, `answered_at` | sortie brute, lettre attendue, ordre des choix présentés, horodatage |

Choix faits pour que la mesure soit **fiable et comparable** :

- **Réponse par lettre, pas en texte libre.** Les choix sont présentés
  `A) … B) … C) … D) …` (ou `A) True / B) False`) et le modèle doit répondre
  une lettre. `ai_correct` compare deux lettres : pas de comparaison de texte
  approximative.
- **Ordre des choix mélangé mais reproductible** :
  `random.Random(question_id).shuffle(...)`. La bonne réponse n'est pas
  toujours à la même place, mais chaque modèle voit exactement le même ordre,
  à chaque run.
- **Paramètres figés** : `temperature=0` (réponse déterministe),
  `max_tokens=8`, `reasoning_effort="none"`. Sans ce dernier paramètre, Gemma 4
  (modèle « à réflexion ») consomme ses tokens à réfléchir et renvoie une
  réponse vide.
- **Prompts versionnés** dans [`src/prompts.py`](src/prompts.py), jamais
  modifiés une fois utilisés :
  - `v1_simple` : la question, les choix, puis « Answer with the letter of
    the correct choice. » ;
  - `v2_strict` : consigne stricte, « exactement une lettre parmi A, B, C, D,
    et rien d'autre ».
- **Script reprenable** : chaque réponse est écrite immédiatement dans le CSV,
  et les triplets (question, modèle, prompt) déjà présents sont sautés. Une
  coupure ne fait rien perdre.

Modèles testés : **Gemma 4 12B** et **Bonsai 27B** (MacBook Air M5),
**Qwen 3.5 9B** (machine de Noah). Un run complet dure de 40 à 75 minutes
selon le modèle et le prompt.

### 3. Transformation (silver et gold)

**Silver.** [`src/bronze_to_silver.py`](src/bronze_to_silver.py) décode les
textes, calcule le `question_id`, retire les doublons et joint questions et
réponses en une **One Big Table** (`silver/obt_answers.parquet`, 1 ligne =
1 réponse). Le script vérifie que chaque réponse retrouve sa question.

**Gold.** dbt construit trois couches dans `gold/warehouse.duckdb` :

| Couche dbt | Modèle | Rôle |
|---|---|---|
| staging | `stg_obt_answers` (vue) | lit le Parquet silver, type et nettoie les colonnes |
| intermediate | `int_answers` (table) | ajoute les colonnes dérivées : `nb_choices`, `random_baseline` (1 / nombre de choix), `is_unparsable`, longueurs de la question et de la réponse |
| marts | `mart_leaderboard` | classement des runs : précision, gain sur le hasard, % de format respecté, temps moyen / médian / p95 |
| marts | `mart_accuracy_by_category` | précision par catégorie, poids de chaque catégorie |
| marts | `mart_accuracy_by_difficulty` | précision par difficulté, QCM et vrai/faux séparés (le hasard n'y vaut pas la même chose) |
| marts | `mart_letter_bias` | axe exploratoire : le modèle préfère-t-il certaines lettres ? |

**Qualité des données : 49 tests dbt** (`unique` et `not_null` sur les clés,
`accepted_values` sur la difficulté et le type, test `not_empty` maison sur
les marts). `dbt build` construit les 6 modèles et lance les tests : 55 étapes,
toutes au vert.

La base DuckDB n'est pas versionnée : elle est **jetable et reproductible**.
La source de vérité, ce sont les fichiers bronze et silver et le code dbt.

### 4. Restitution (Streamlit)

[`src/app.py`](src/app.py) ouvre la base **en lecture seule** (elle ne bloque
pas `dbt build`) et lit les marts : **aucun calcul n'est refait** dans le
dashboard. Les lectures sont mises en cache (`@st.cache_data`), car Streamlit
relance le script à chaque clic.

- **Filtres** par modèle, prompt, difficulté, type et groupe de catégories ;
  indicateurs clés en tête (meilleure précision, hasard, temps médian).
- **8 onglets**, chacun répondant à une question métier : classement, effet du
  prompt, catégories, difficulté × type, temps de réponse, biais de lettre,
  explorateur des erreurs (les réponses une par une, avec la sortie brute du
  modèle) et données des marts (tables gold téléchargeables).
- **Même structure sur chaque onglet** : la question en titre, une phrase
  « À retenir » calculée sur les données filtrées, un seul graphique principal,
  puis les vues secondaires et tableaux dans un bloc repliable « Aller plus loin ».
- **Charte graphique fixe** (`.streamlit/config.toml` + palette dans `app.py`) :
  thème clair, une couleur par run identique d'un onglet à l'autre, cartes de
  chaleur centrées sur la moyenne (rouge = sous la moyenne, marine = au-dessus).

---

## Organisation du dépôt

```
.
├── README.md                       ce fichier
├── AGENTS.md                       règles de travail et règles git du binôme
├── documentation/
│   └── ARCHITECTURE.md             architecture détaillée, formats, convention question_id
├── requirements.txt                dépendances Python (versions figées)
├── dbt_project.yml                 configuration du projet dbt
├── profiles.yml                    connexion dbt → gold/warehouse.duckdb
├── bronze/
│   ├── questions_raw_<ts>.csv      questions OpenTDB brutes
│   └── ai_answers_raw.csv          réponses brutes des modèles
├── silver/
│   └── obt_answers.parquet         One Big Table
├── gold/
│   └── warehouse.duckdb            base dbt (non versionnée, reconstruite par dbt build)
└── src/
    ├── config.py                   chemins et paramètres du modèle local
    ├── extract_bronze.py           OpenTDB → bronze
    ├── prompts.py                  prompts versionnés
    ├── test_llm.py                 test rapide de la connexion au modèle
    ├── run_benchmark.py            questions → modèle → bronze/ai_answers_raw.csv
    ├── bronze_to_silver.py         bronze → silver (Parquet)
    ├── app.py                      dashboard Streamlit
    ├── macros/                     macros dbt (noms de schémas, test not_empty)
    └── models/
        ├── staging/                stg_obt_answers + déclaration de la source silver
        ├── intermediate/           int_answers
        └── marts/                  4 marts
```

---

## Installation et exécution

Toutes les commandes se lancent **depuis la racine du projet**.

### Prérequis

- **Python 3.14** (testé avec 3.14.4)
- **Git**
- Pour relancer le benchmark seulement : **LM Studio** (ou Ollama) avec un
  modèle téléchargé

### 1. Récupérer le projet et installer les dépendances

```bash
git clone https://github.com/nsellal/ETL_Trivial_poursuite.git
```

```bash
cd ETL_Trivial_poursuite
```

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

Sous Windows, l'activation du venv se fait avec `.venv\Scripts\activate`.

### 2. Voir les résultats tout de suite (sans LLM)

Les données bronze et silver sont versionnées : pas besoin de refaire le
scraping ni le benchmark pour explorer les résultats.

Construire la base gold et lancer les tests :

```bash
dbt build --profiles-dir .
```

Lancer le dashboard (il s'ouvre sur http://localhost:8501) :

```bash
streamlit run src/app.py
```

### 3. Rejouer tout le pipeline

**Étape A : collecter les questions** (environ 10 minutes, à cause de la
limite de l'API). Un nouveau fichier `bronze/questions_raw_<timestamp>.csv`
est créé ; les scripts suivants prennent automatiquement le plus récent.

```bash
python src/extract_bronze.py
```

**Étape B : préparer le modèle local.** Dans LM Studio, charger un modèle,
puis onglet *Developer* → *Start Server*. Vérifier que le modèle répond :

```bash
python src/test_llm.py
```

**Étape C : lancer le benchmark.** Sans option `--prompt`, le script enchaîne
`v1_simple` puis `v2_strict`. Test rapide sur 20 questions :

```bash
python src/run_benchmark.py --model google/gemma-4-12b-qat --limit 20
```

Run complet (`caffeinate -i` empêche le Mac de se mettre en veille) :

```bash
caffeinate -i python src/run_benchmark.py --model google/gemma-4-12b-qat
```

Un seul prompt :

```bash
python src/run_benchmark.py --model qwen/qwen3.5-9b --prompt v2_strict
```

Le nom du modèle est celui affiché dans LM Studio. Pour utiliser Ollama,
définir `LLM_BASE_URL=http://localhost:11434/v1` avant de lancer le script.

**Étape D : reconstruire silver, gold et relancer le dashboard.**

```bash
python src/bronze_to_silver.py
```

```bash
dbt build --profiles-dir .
```

```bash
streamlit run src/app.py
```

### Problèmes fréquents

| Symptôme | Cause et solution |
|---|---|
| `Could not set lock on file ... warehouse.duckdb` | Un autre programme a ouvert la base en écriture (par exemple le CLI `duckdb`). Le fermer, ou l'ouvrir en lecture seule : `duckdb -readonly gold/warehouse.duckdb`. |
| Le modèle renvoie des réponses vides | Modèle « à réflexion » : le script envoie déjà `reasoning_effort="none"`. Vérifier que la version de LM Studio le prend en charge. |
| Le dashboard montre d'anciennes tables ou d'anciens chiffres | dbt ne supprime pas les tables des modèles retirés : supprimer `gold/warehouse.duckdb` puis relancer `dbt build --profiles-dir .`. |

---

## Limites

- **Temps de réponse** : ils dépendent de la machine. Gemma et Bonsai ont
  tourné sur un MacBook Air M5, Qwen sur une autre machine : la comparaison
  des temps entre modèles n'est qu'indicative.
- **Bonsai 27B** n'a été évalué qu'avec `v2_strict`. Avec `v1_simple`, il
  rédige des phrases jusqu'à la limite de tokens et le run complet aurait
  duré plus de deux heures.
- **Réponses hors format comptées fausses** : c'est un choix assumé (on
  mesure aussi le respect de la consigne), mais il pénalise fortement
  `v1_simple`. Le pourcentage de format respecté est affiché à côté de la
  précision pour en tenir compte.
- **Dataset déséquilibré** : 53 % de questions *Entertainment*. La précision
  globale reflète surtout la culture « jeux vidéo, films, anime » des
  modèles ; les résultats par catégorie sont plus parlants.
- **Questions publiques** : OpenTDB est en ligne depuis des années, certaines
  questions ont pu être vues par les modèles pendant leur entraînement.
