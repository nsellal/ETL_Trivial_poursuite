"""Conversion bronze -> silver : chaque CSV de bronze/ devient un Parquet dans silver/,
sans le mot 'raw' dans son nom (ex. ai_answers_raw.csv -> ai_answers.parquet).

Transformations appliquées (la couche bronze n'est jamais modifiée) :
  - dates (scraped_at, answered_at) -> timestamp Unix en secondes (entier) ;
  - textes des questions encodés url3986 -> texte UTF-8 lisible, décodage
    vérifié sans perte ; la colonne `encoding` est alors supprimée ;
  - incorrect_answers : liste JSON de textes encodés -> vraie liste de textes.

Le reste du nettoyage (typage métier, question_id, dédoublonnage) est fait
ensuite par dbt (staging).
"""

import json
import re
from urllib.parse import quote, unquote

import duckdb
import pandas as pd

from config import BRONZE_DIR, SILVER_DIR

# Colonnes texte encodées en url3986 dans questions_raw_*.csv
URL3986_COLUMNS = ["category", "question", "correct_answer"]
URL3986_LIST_COLUMN = "incorrect_answers"  # liste JSON de textes encodés
ENCODING_COLUMN = "encoding"
EXPECTED_ENCODING = "url3986"


def silver_name(csv_path):
    """Nom du Parquet silver : celui du CSV bronze sans le mot 'raw'
    (les données ne sont plus brutes).
    ex. questions_raw_1795440205.csv -> questions_1795440205.parquet
        ai_answers_raw.csv           -> ai_answers.parquet
    """
    stem = re.sub(r"_raw(?=_|$)", "", csv_path.stem)
    return f"{stem}.parquet"


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
            raise ValueError(f"Encodage inattendu : {encodings} (attendu : {EXPECTED_ENCODING})")
        for column in URL3986_COLUMNS:
            df[column] = df[column].map(decode_url3986)
        df[URL3986_LIST_COLUMN] = df[URL3986_LIST_COLUMN].map(decode_url3986_list)
        # 3. Les textes sont décodés : la colonne encoding n'a plus de sens
        df = df.drop(columns=ENCODING_COLUMN)

    return df


def convert_csv_to_parquet(con, csv_path, parquet_path):
    """Lit un CSV (types détectés par DuckDB), le transforme et l'écrit en Parquet."""
    df = con.execute("SELECT * FROM read_csv(?)", [str(csv_path)]).df()
    silver = transform(df)
    con.register("silver_df", silver)
    con.execute(f"COPY silver_df TO '{parquet_path}' (FORMAT parquet)")
    con.unregister("silver_df")

    # Vérification : même nombre de lignes avant et après
    nb_parquet = con.execute("SELECT count(*) FROM read_parquet(?)", [str(parquet_path)]).fetchone()[0]
    if len(df) != nb_parquet:
        raise ValueError(f"{csv_path.name} : {len(df)} lignes en CSV mais {nb_parquet} en Parquet")
    return nb_parquet


def main():
    SILVER_DIR.mkdir(exist_ok=True)
    csv_files = sorted(BRONZE_DIR.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"Aucun CSV dans {BRONZE_DIR}")

    # Connexion DuckDB en mémoire : on ne touche pas au warehouse
    con = duckdb.connect()
    for csv_path in csv_files:
        parquet_path = SILVER_DIR / silver_name(csv_path)
        nb = convert_csv_to_parquet(con, csv_path, parquet_path)
        print(f"{csv_path.name} -> silver/{parquet_path.name} ({nb} lignes)")
    con.close()


if __name__ == "__main__":
    main()
