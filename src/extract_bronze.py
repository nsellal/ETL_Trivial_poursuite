"""Couche BRONZE : scraping complet d'Open Trivia Database (OpenTDB).

Sortie : bronze/questions_raw_<timestamp>.csv
         <timestamp> = date du lancement en secondes (timestamp Unix), pour
         qu'une nouvelle exécution n'écrase jamais un fichier existant.

Stratégie pour récupérer l'intégralité du dataset :
  1. on demande un jeton de session : l'API ne renvoie alors jamais deux fois
     la même question ;
  2. pour chaque catégorie, on lit le nombre de questions par difficulté
     (api_count.php) ;
  3. pour chaque couple (catégorie, difficulté), on enchaîne des appels de
     50 questions maximum jusqu'à les avoir toutes ;
  4. on respecte la limite de l'API (1 appel toutes les 5 secondes) et on
     gère ses codes de réponse.

Encodage : on demande les textes en url3986 (encodage URL RFC 3986). Il est
sans perte et sans ambiguïté, et les champs ne contiennent plus ni virgule,
ni guillemet, ni retour à la ligne : le CSV ne peut pas être cassé. Les
données sont stockées TELLES QUELLES (encodées) ; le décodage
(urllib.parse.unquote) est le rôle de la couche silver.

Reprise : en cas de coupure, --resume <fichier> complète un fichier existant
en ne récupérant que les couples (catégorie, difficulté) incomplets.

Usage (depuis la racine du projet) :
    python src/extract_bronze.py
    python src/extract_bronze.py --categories 9 17     # test sur 2 catégories
    python src/extract_bronze.py --resume bronze/questions_raw_1795432000.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
BRONZE_DIR = ROOT_DIR / "bronze"

BASE_URL = "https://opentdb.com"
ENCODING = "url3986"
MIN_DELAY_S = 6.5  # limite de l'API : 1 appel / 5 s, avec une marge
MAX_AMOUNT = 50  # nombre maximum de questions par appel
MAX_RETRIES = 8
DIFFICULTIES = ("easy", "medium", "hard")

COLUMNS = [
    "category_id",
    "category",
    "type",
    "difficulty",
    "question",
    "correct_answer",
    "incorrect_answers",  # liste sérialisée en JSON (chaque élément encodé)
    "encoding",  # format d'encodage des textes, pour le décodage en silver
    "scraped_at",
]

# Codes de réponse de l'API
CODE_SUCCESS = 0  # résultats renvoyés
CODE_NO_RESULTS = 1  # pas assez de questions pour le nombre demandé
CODE_INVALID_PARAMETER = 2  # paramètre invalide
CODE_TOKEN_NOT_FOUND = 3  # jeton inexistant ou expiré
CODE_TOKEN_EMPTY = 4  # le jeton a déjà servi toutes les questions de ce filtre
CODE_RATE_LIMIT = 5  # trop de requêtes

log = logging.getLogger("bronze")


class OpenTDBClient:
    """Client HTTP qui respecte la limite de l'API et réessaie en cas d'erreur."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "trivial-etl-student-project/1.0"
        self.last_call = 0.0
        self.token: str | None = None

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        for attempt in range(1, MAX_RETRIES + 1):
            wait = MIN_DELAY_S - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)

            backoff = MIN_DELAY_S * 2 ** min(attempt, 4)  # 11 s, 22 s, 44 s, 88 s...
            try:
                resp = self.session.get(url, params=params, timeout=30)
                self.last_call = time.monotonic()
                if resp.status_code == 429:
                    log.warning("HTTP 429 (trop de requêtes), pause %.0f s", backoff)
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                self.last_call = time.monotonic()
                log.warning(
                    "Erreur (%s), tentative %d/%d, pause %.0f s",
                    exc,
                    attempt,
                    MAX_RETRIES,
                    backoff,
                )
                time.sleep(backoff)
                continue

            if data.get("response_code") == CODE_RATE_LIMIT:
                log.warning("Code 5 (rate limit), pause %.0f s", backoff)
                time.sleep(backoff)
                continue
            return data
        raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives : {url} {params}")

    def categories(self) -> list[dict]:
        return self.get("api_category.php")["trivia_categories"]

    def category_counts(self, category_id: int) -> dict:
        return self.get("api_count.php", {"category": category_id})[
            "category_question_count"
        ]

    def request_token(self) -> None:
        self.token = self.get("api_token.php", {"command": "request"})["token"]
        log.info("Jeton de session obtenu")

    def questions(self, amount: int, category_id: int, difficulty: str) -> dict:
        params = {
            "amount": amount,
            "category": category_id,
            "difficulty": difficulty,
            "token": self.token,
            "encode": ENCODING,
        }
        return self.get("api.php", params)


def fetch_bucket(
    client: OpenTDBClient, category_id: int, difficulty: str, expected: int
) -> list[dict]:
    """Récupère toutes les questions d'un couple (catégorie, difficulté)."""
    results: list[dict] = []
    amount = MAX_AMOUNT
    while len(results) < expected:
        amount = max(1, min(amount, expected - len(results)))
        data = client.questions(amount, category_id, difficulty)
        code = data["response_code"]

        if code == CODE_SUCCESS:
            results.extend(data["results"])
        elif code == CODE_NO_RESULTS:
            # Moins de questions restantes que demandé : on réduit la taille du lot
            if amount == 1:
                break
            amount //= 2
        elif code == CODE_TOKEN_EMPTY:
            break
        elif code == CODE_TOKEN_NOT_FOUND:
            # Jeton expiré : on en demande un nouveau. D'éventuels doublons
            # seront supprimés en silver.
            client.request_token()
        else:
            raise RuntimeError(f"Réponse inattendue de l'API : {data}")
    return results


def to_rows(results: list[dict], category_id: int) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [
        {
            "category_id": category_id,
            "category": q["category"],
            "type": q["type"],
            "difficulty": q["difficulty"],
            "question": q["question"],
            "correct_answer": q["correct_answer"],
            "incorrect_answers": json.dumps(q["incorrect_answers"]),
            "encoding": ENCODING,
            "scraped_at": scraped_at,
        }
        for q in results
    ]


def new_output_path() -> Path:
    """bronze/questions_raw_<timestamp Unix en secondes>.csv"""
    return BRONZE_DIR / f"questions_raw_{int(time.time())}.csv"


def read_existing(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    encodings = {r.get("encoding") for r in rows}
    if rows and encodings != {ENCODING}:
        raise SystemExit(
            f"{path.name} n'est pas encodé en {ENCODING} ({encodings}) : reprise impossible."
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)  # écriture atomique : jamais de CSV à moitié écrit


def run(category_filter: list[int] | None = None, resume: Path | None = None) -> None:
    output_csv = resume or new_output_path()
    rows = read_existing(resume) if resume else []
    already = Counter((int(r["category_id"]), r["difficulty"]) for r in rows)
    if rows:
        log.info(
            "Reprise : %d questions déjà présentes dans %s", len(rows), output_csv.name
        )
    log.info("Fichier de sortie : %s", output_csv)

    client = OpenTDBClient()
    categories = client.categories()
    if category_filter:
        categories = [c for c in categories if c["id"] in category_filter]
    log.info("%d catégories à traiter", len(categories))

    report = []
    for cat in categories:
        counts = client.category_counts(cat["id"])
        for difficulty in DIFFICULTIES:
            expected = int(counts[f"total_{difficulty}_question_count"])
            key = (cat["id"], difficulty)
            if expected == 0:
                continue
            if already[key] >= expected:
                report.append((cat["name"], difficulty, expected, already[key]))
                continue

            # Couple incomplet : on le récupère entièrement avec le jeton
            if client.token is None:
                client.request_token()
            rows = [r for r in rows if (int(r["category_id"]), r["difficulty"]) != key]
            results = fetch_bucket(client, cat["id"], difficulty, expected)
            rows.extend(to_rows(results, cat["id"]))
            write_csv(output_csv, rows)  # sauvegarde après chaque couple
            report.append((cat["name"], difficulty, expected, len(results)))
            log.info(
                "%-45s %-6s %4d / %4d", cat["name"], difficulty, len(results), expected
            )

    write_csv(output_csv, rows)
    total_expected = sum(r[2] for r in report)
    log.info(
        "Terminé : %d questions dans %s (attendu : %d)",
        len(rows),
        output_csv,
        total_expected,
    )
    for name, difficulty, expected, got in report:
        if got < expected:
            log.warning(
                "Incomplet : %s / %s : %d sur %d", name, difficulty, got, expected
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--categories",
        type=int,
        nargs="*",
        help="IDs de catégories (par défaut : toutes)",
    )
    parser.add_argument("--resume", type=Path, help="CSV bronze existant à compléter")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    run(args.categories, args.resume)


if __name__ == "__main__":
    main()
