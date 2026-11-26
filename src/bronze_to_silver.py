"""Bronze -> silver : fusionne questions et réponses des IA en une seule table
(One Big Table) écrite dans silver/obt_answers.parquet.

Grain : 1 ligne = 1 réponse d'un modèle, avec un prompt, à une question,
enrichie de toutes les informations de sa question.

Transformations appliquées (la couche bronze n'est jamais modifiée) :
  - dates (scraped_at, answered_at) -> timestamp Unix en secondes (entier) ;
  - textes des questions encodés url3986 -> texte UTF-8 lisible, décodage
    vérifié sans perte ; la colonne `encoding` est alors supprimée ;
  - incorrect_answers : liste JSON de textes encodés -> vraie liste de textes ;
  - question_id (même calcul que run_benchmark.py) et dédoublonnage ;
  - jointure réponses × questions, avec contrôle : aucune réponse perdue
    ni dupliquée.

Le typage métier et les colonnes dérivées sont faits ensuite par dbt (staging).
"""

import hashlib
import json
from urllib.parse import quote, unquote

import duckdb
import pandas as pd

from config import AI_ANSWERS_RAW_CSV, SILVER_DIR, latest_questions_csv

# Colonnes texte encodées en url3986 dans questions_raw_*.csv
URL3986_COLUMNS = ["category", "question", "correct_answer"]
URL3986_LIST_COLUMN = "incorrect_answers"  # liste JSON de textes encodés
ENCODING_COLUMN = "encoding"
EXPECTED_ENCODING = "url3986"

# Unique fichier de la couche silver
OBT_PARQUET = SILVER_DIR / "obt_answers.parquet"


def to_unix_seconds(dates):
    """Convertit une colonne de dates (ex. 2026-11-23T14:43:54.000Z) en
    timestamp Unix en secondes (entier, UTC). Les fractions de seconde sont
    tronquées ; les valeurs manquantes restent manquantes."""
    dates = pd.to_datetime(dates, utc=True)
    epoch = pd.Timestamp(0, tz="UTC")
    return ((dates - epoch) // pd.Timedelta(seconds=1)).astype("Int64")


def decode_url3986(text):
    """Décode un texte url3986 (RFC 3986) en UTF-8, sans perte.

    - errors="strict" : une séquence d'octets qui n'est pas de l'UTF-8 valide
      lève une erreur au lieu d'être remplacée silencieusement par '?' ;
    - vérification aller-retour : réencoder le texte décodé doit redonner
      exactement le texte d'origine, sinon on refuse le décodage.
    """
    if text is None or pd.isna(text):
        return None
    decoded = unquote(text, encoding="utf-8", errors="strict")
    # safe="" : tout est encodé sauf A-Z a-z 0-9 - _ . ~ (règle url3986)
    if quote(decoded, safe="") != text:
        raise ValueError(f"Décodage url3986 non réversible : {text!r}")
    return decoded


def decode_url3986_list(json_text):
    """Décode une liste JSON de textes url3986 en liste Python de textes."""
    if json_text is None or pd.isna(json_text):
        return None
    return [decode_url3986(item) for item in json.loads(json_text)]


def transform(df):
    """Applique les transformations silver à un DataFrame lu depuis bronze."""
    df = df.copy()

    # 1. Dates -> timestamp Unix (secondes)
    for column in df.columns:
        if column.endswith("_at"):
            df[column] = to_unix_seconds(df[column])

    # 2. Décodage url3986 (uniquement les fichiers qui ont une colonne encoding)
    if ENCODING_COLUMN in df.columns:
        encodings = set(df[ENCODING_COLUMN].dropna().unique())
        if encodings != {EXPECTED_ENCODING}:
            raise ValueError(
                f"Encodage inattendu : {encodings} (attendu : {EXPECTED_ENCODING})"
            )
        for column in URL3986_COLUMNS:
            df[column] = df[column].map(decode_url3986)
        df[URL3986_LIST_COLUMN] = df[URL3986_LIST_COLUMN].map(decode_url3986_list)
        # 3. Les textes sont décodés : la colonne encoding n'a plus de sens
        df = df.drop(columns=ENCODING_COLUMN)

    return df


def question_id(question, correct_answer):
    """Identifiant de question, MÊME calcul que run_benchmark.py :
    12 premiers caractères du MD5 de 'question|correct_answer' (textes décodés)."""
    return hashlib.md5(f"{question}|{correct_answer}".encode("utf-8")).hexdigest()[:12]


def prepare_questions(df):
    """Questions : décodage, question_id, puis dédoublonnage.
    En cas de doublon, on garde l'extraction la plus récente."""
    df = transform(df)
    df["question_id"] = [
        question_id(q, a) for q, a in zip(df["question"], df["correct_answer"])
    ]
    df = df.sort_values("scraped_at").drop_duplicates("question_id", keep="last")
    return df


def prepare_answers(df):
    """Réponses : dates converties, puis dédoublonnage.
    Si un couple question × modèle × prompt a été rejoué, on garde le plus récent."""
    df = transform(df)
    df = df.sort_values("answered_at").drop_duplicates(
        ["question_id", "model", "prompt_id"], keep="last"
    )
    return df


def build_obt(questions, answers):
    """One Big Table : chaque réponse enrichie des informations de sa question.
    Grain : 1 ligne = 1 réponse (question × modèle × prompt)."""
    # validate="many_to_one" : plante si une question est en double
    # (sinon la jointure dupliquerait des réponses)
    obt = answers.merge(
        questions, on="question_id", how="inner", validate="many_to_one"
    )

    # Chaque réponse doit avoir trouvé sa question (remplace le test relationships de dbt)
    if len(obt) != len(answers):
        raise ValueError(
            f"{len(answers) - len(obt)} réponses sans question correspondante"
        )
    return obt


def read_csv(con, csv_path):
    """Lit un CSV avec DuckDB (types détectés automatiquement)."""
    return con.execute("SELECT * FROM read_csv(?)", [str(csv_path)]).df()


def main():
    SILVER_DIR.mkdir(exist_ok=True)
    # Connexion DuckDB en mémoire : on ne touche pas au warehouse
    con = duckdb.connect()

    questions = prepare_questions(read_csv(con, latest_questions_csv()))
    answers = prepare_answers(read_csv(con, AI_ANSWERS_RAW_CSV))
    obt = build_obt(questions, answers)

    con.register("obt_df", obt)
    con.execute(f"COPY obt_df TO '{OBT_PARQUET}' (FORMAT parquet)")
    con.unregister("obt_df")

    # Vérification : même nombre de lignes en mémoire et dans le Parquet
    nb_parquet = con.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(OBT_PARQUET)]
    ).fetchone()[0]
    con.close()
    if nb_parquet != len(obt):
        raise ValueError(
            f"{len(obt)} lignes en mémoire mais {nb_parquet} dans le Parquet"
        )

    print(
        f"{len(questions)} questions × {len(answers)} réponses -> silver/{OBT_PARQUET.name} ({nb_parquet} lignes)"
    )


if __name__ == "__main__":
    main()
