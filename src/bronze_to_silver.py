"""Conversion bronze -> silver : chaque CSV de bronze/ devient un Parquet dans silver/.

Le contenu n'est pas modifié (textes toujours encodés url3986) :
seul le format change. Le nettoyage est fait ensuite par dbt (staging).
"""
import duckdb

from config import BRONZE_DIR, SILVER_DIR


def convert_csv_to_parquet(con, csv_path, parquet_path):
    """Lit un CSV (types détectés par DuckDB) et l'écrit en Parquet."""
    con.execute(
        "COPY (SELECT * FROM read_csv(?)) TO ? (FORMAT parquet)",
        [str(csv_path), str(parquet_path)],
    )
    # Vérification : même nombre de lignes avant et après
    nb_csv = con.execute("SELECT count(*) FROM read_csv(?)", [str(csv_path)]).fetchone()[0]
    nb_parquet = con.execute("SELECT count(*) FROM read_parquet(?)", [str(parquet_path)]).fetchone()[0]
    if nb_csv != nb_parquet:
        raise ValueError(f"{csv_path.name} : {nb_csv} lignes en CSV mais {nb_parquet} en Parquet")
    return nb_parquet


def main():
    SILVER_DIR.mkdir(exist_ok=True)
    csv_files = sorted(BRONZE_DIR.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"Aucun CSV dans {BRONZE_DIR}")

    # Connexion DuckDB en mémoire : on ne touche pas au warehouse
    con = duckdb.connect()
    for csv_path in csv_files:
        parquet_path = SILVER_DIR / f"{csv_path.stem}.parquet"
        nb = convert_csv_to_parquet(con, csv_path, parquet_path)
        print(f"{csv_path.name} -> silver/{parquet_path.name} ({nb} lignes)")
    con.close()


if __name__ == "__main__":
    main()
