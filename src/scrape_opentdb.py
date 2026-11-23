"""Étape 1 du pipeline : récupère toutes les questions d'OpenTDB (couche Bronze)."""
import base64
import hashlib
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from config import BRONZE_DIR, QUESTIONS_RAW_CSV

# Adresses de l'API OpenTDB
API_URL = "https://opentdb.com/api.php"
TOKEN_URL = "https://opentdb.com/api_token.php"
COUNT_URL = "https://opentdb.com/api_count_global.php"

# Règles de l'API : 50 questions max par appel, 1 appel toutes les 5 secondes
BATCH_SIZE = 50
PAUSE_SECONDS = 5

# Attente avant de réessayer en cas de coupure réseau ou de panne du serveur
NETWORK_RETRY_SECONDS = 30


def http_get(url, params=None):
    """Appel GET qui réessaie automatiquement en cas de coupure réseau ou de panne du serveur."""
    while True:
        try:
            response = requests.get(url, params=params, timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            print(f"Problème de connexion, nouvel essai dans {NETWORK_RETRY_SECONDS} s...")
            time.sleep(NETWORK_RETRY_SECONDS)
            continue

        # Erreur côté serveur (codes HTTP 500 et plus) : souvent temporaire, on réessaie
        if response.status_code >= 500:
            print(f"Erreur serveur {response.status_code}, nouvel essai dans {NETWORK_RETRY_SECONDS} s...")
            time.sleep(NETWORK_RETRY_SECONDS)
            continue

        return response


def get_token():
    """Demande un token de session à OpenTDB pour ne jamais recevoir deux fois la même question."""
    response = http_get(TOKEN_URL, {"command": "request"})
    response.raise_for_status()
    return response.json()["token"]


def get_expected_total():
    """Demande à l'API le nombre total de questions vérifiées (pour contrôler le résultat)."""
    response = http_get(COUNT_URL)
    response.raise_for_status()
    return response.json()["overall"]["total_num_of_verified_questions"]


def fetch_batch(token, amount):
    """Demande un lot de questions à l'API. Renvoie (response_code, liste de questions)."""
    params = {"amount": amount, "token": token, "encode": "base64"}

    while True:
        response = http_get(API_URL, params)

        # Trop de requêtes (signalé par HTTP 429) : on attend puis on réessaie
        if response.status_code == 429:
            print("Rate limit atteint, pause...")
            time.sleep(PAUSE_SECONDS)
            continue

        response.raise_for_status()
        data = response.json()

        # Trop de requêtes (signalé par le code 5) : même traitement
        if data["response_code"] == 5:
            print("Rate limit atteint, pause...")
            time.sleep(PAUSE_SECONDS)
            continue

        return data["response_code"], data.get("results", [])


def b64(text):
    """Décode un texte encodé en base64 (format renvoyé par l'API)."""
    return base64.b64decode(text).decode("utf-8")


def decode_question(raw):
    """Transforme une question brute de l'API en ligne propre pour le CSV."""
    question = b64(raw["question"])
    correct_answer = b64(raw["correct_answer"])
    incorrect_answers = [b64(answer) for answer in raw["incorrect_answers"]]

    # Identifiant stable : hash MD5 de la question + la bonne réponse (12 premiers caractères)
    question_id = hashlib.md5(f"{question}|{correct_answer}".encode("utf-8")).hexdigest()[:12]

    return {
        "question_id": question_id,
        "type": b64(raw["type"]),
        "difficulty": b64(raw["difficulty"]),
        "category": b64(raw["category"]),
        "question": question,
        "correct_answer": correct_answer,
        "incorrect_answers": json.dumps(incorrect_answers, ensure_ascii=False),
    }


def scrape_all():
    """Récupère toutes les questions jusqu'à épuisement du token."""
    token = get_token()
    rows = []
    amount = BATCH_SIZE

    while True:
        code, results = fetch_batch(token, amount)

        if code == 0:
            # Lot reçu : on décode chaque question et on note l'heure de collecte
            scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for raw in results:
                row = decode_question(raw)
                row["scraped_at"] = scraped_at
                rows.append(row)
            print(f"{len(rows)} questions récupérées")

        elif code in (1, 4) and amount > 1:
            # Il reste moins de questions que demandé (l'API renvoie 1 ou 4) : on réduit le lot
            amount = amount // 2
            print(f"Moins de questions restantes, lot réduit à {amount}")

        elif code in (1, 4):
            # Même une seule question est refusée : tout a été récupéré
            print("Toutes les questions ont été récupérées.")
            break

        else:
            raise RuntimeError(f"Code réponse inattendu de l'API : {code}")

        # On respecte la limite de l'API : 1 appel toutes les 5 secondes
        time.sleep(PAUSE_SECONDS)

    return rows


def save_csv(rows):
    """Enregistre les questions dans le CSV de la couche Bronze."""
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(QUESTIONS_RAW_CSV, index=False)
    print(f"{len(df)} questions enregistrées dans {QUESTIONS_RAW_CSV}")
    return df


def check_result(df, expected):
    """Compare le nombre de questions récupérées au total annoncé par l'API."""
    print("\n--- Contrôle ---")
    print(f"Questions attendues (API)   : {expected}")
    print(f"Questions récupérées        : {len(df)}")
    print(f"question_id uniques         : {df['question_id'].nunique()}")
    if len(df) == expected:
        print("OK : le dataset est complet.")
    else:
        print(f"ATTENTION : écart de {expected - len(df)} questions.")


if __name__ == "__main__":
    expected_total = get_expected_total()
    questions = scrape_all()
    questions_df = save_csv(questions)
    check_result(questions_df, expected_total)
