"""Étape 2 du pipeline : pose chaque question au modèle local et enregistre ses réponses (Bronze)."""

import argparse
import csv
import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import pandas as pd
from openai import OpenAI

from config import (
    AI_ANSWERS_RAW_CSV,
    BRONZE_DIR,
    LLM_BASE_URL,
    LLM_MODEL,
    latest_questions_csv,
)
from prompts import PROMPTS

LETTERS = "ABCD"

# Colonnes du CSV de réponses (couche Bronze)
COLUMNS = [
    "question_id",
    "model",
    "prompt_id",
    "prompt_text",
    "choices_order",
    "expected_letter",
    "raw_output",
    "ai_answer",
    "ai_correct",
    "response_time",
    "answered_at",
]

# Accepte "B", "B)", "(B)", "b", "Answer: B"... mais seulement en début de réponse
ANSWER_PATTERN = re.compile(r"^\s*(?:answer\s*:\s*)?\(?([A-D])\b", re.IGNORECASE)


def make_question_id(question, correct_answer):
    """Identifiant stable, convention commune avec dbt : MD5 de 'question|bonne réponse', 12 caractères."""
    return hashlib.md5(f"{question}|{correct_answer}".encode("utf-8")).hexdigest()[:12]


def load_questions():
    """Lit le CSV Bronze, décode les textes (url3986) et calcule le question_id."""
    df = pd.read_csv(latest_questions_csv())
    for column in ["category", "question", "correct_answer"]:
        df[column] = df[column].map(unquote)
    # incorrect_answers : liste JSON dont chaque élément est encodé
    df["incorrect_answers"] = df["incorrect_answers"].map(
        lambda text: [unquote(answer) for answer in json.loads(text)]
    )
    df["question_id"] = [
        make_question_id(q, c) for q, c in zip(df["question"], df["correct_answer"])
    ]
    # Les 2 doublons de la source : on ne pose chaque question qu'une fois
    return df.drop_duplicates("question_id").reset_index(drop=True)


def build_choices(row):
    """Renvoie la liste ordonnée des choix : mélangée mais reproductible pour les QCM."""
    if row["type"] == "boolean":
        return ["True", "False"]
    choices = [row["correct_answer"]] + list(row["incorrect_answers"])
    # Graine = question_id : même ordre pour tous les modèles et tous les runs
    random.Random(row["question_id"]).shuffle(choices)
    return choices


def build_prompt(prompt_id, row, choices):
    """Remplit le modèle de prompt. Renvoie (texte du prompt, lettre attendue)."""
    letters = LETTERS[: len(choices)]
    choices_text = "\n".join(
        f"{letter}) {choice}" for letter, choice in zip(letters, choices)
    )
    prompt = PROMPTS[prompt_id].format(
        question=row["question"],
        choices=choices_text,
        letters=", ".join(letters),
    )
    expected_letter = letters[choices.index(row["correct_answer"])]
    return prompt, expected_letter


def ask_model(client, model, prompt):
    """Envoie le prompt au modèle. Renvoie (réponse brute, temps de réponse en secondes)."""
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=8,
        reasoning_effort="none",  # pas de phase de réflexion : réponse directe
    )
    elapsed = time.perf_counter() - start
    return response.choices[0].message.content or "", elapsed


def parse_letter(raw_output, letters):
    """Extrait la lettre choisie par le modèle, ou None si la réponse est inexploitable."""
    match = ANSWER_PATTERN.match(raw_output)
    if match and match.group(1).upper() in letters:
        return match.group(1).upper()
    return None


def load_done():
    """Renvoie l'ensemble des (question_id, model, prompt_id) déjà présents dans le CSV de réponses."""
    if not AI_ANSWERS_RAW_CSV.exists() or AI_ANSWERS_RAW_CSV.stat().st_size == 0:
        return set()
    done = pd.read_csv(
        AI_ANSWERS_RAW_CSV, usecols=["question_id", "model", "prompt_id"]
    )
    return set(zip(done["question_id"], done["model"], done["prompt_id"]))


def parse_args():
    """Options en ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Benchmark d'un modèle local sur les questions OpenTDB."
    )
    parser.add_argument(
        "--model", default=LLM_MODEL, help="identifiant du modèle dans LM Studio"
    )
    parser.add_argument(
        "--prompt",
        nargs="+",  # un ou plusieurs prompts
        default=list(PROMPTS),  # par défaut : les 3 prompts
        choices=list(PROMPTS),
        help="prompt(s) à utiliser (par défaut : tous)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="nombre de questions (pour tester)"
    )
    return parser.parse_args()


def run_prompt(client, questions, model, prompt_id):
    """Pose toutes les questions au modèle avec un prompt donné, en sautant celles déjà faites."""
    # Reprise : on ne garde que les questions pas encore traitées pour ce modèle et ce prompt
    done = load_done()
    todo = questions[
        [(qid, model, prompt_id) not in done for qid in questions["question_id"]]
    ]
    print(
        f"Modèle : {model} | Prompt : {prompt_id} | "
        f"{len(questions) - len(todo)} déjà faites, {len(todo)} à faire"
    )
    if todo.empty:
        return

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    write_header = (
        not AI_ANSWERS_RAW_CSV.exists() or AI_ANSWERS_RAW_CSV.stat().st_size == 0
    )
    correct_count = 0
    start = time.perf_counter()

    # Mode "a" (ajout) : on complète le fichier sans jamais l'écraser
    with open(AI_ANSWERS_RAW_CSV, "a", newline="", encoding="utf-8") as file:
        # lineterminator="\n" : fins de ligne Unix partout (par défaut csv écrit "\r\n",
        # et un mélange des deux fait planter le read_csv de DuckDB)
        writer = csv.DictWriter(file, fieldnames=COLUMNS, lineterminator="\n")
        if write_header:
            writer.writeheader()

        for i, (_, row) in enumerate(todo.iterrows(), start=1):
            choices = build_choices(row)
            prompt, expected = build_prompt(prompt_id, row, choices)
            raw_output, elapsed = ask_model(client, model, prompt)
            answer = parse_letter(raw_output, LETTERS[: len(choices)])
            is_correct = answer == expected
            correct_count += is_correct

            writer.writerow(
                {
                    "question_id": row["question_id"],
                    "model": model,
                    "prompt_id": prompt_id,
                    "prompt_text": prompt,
                    "choices_order": json.dumps(choices, ensure_ascii=False),
                    "expected_letter": expected,
                    "raw_output": raw_output,
                    "ai_answer": answer,
                    "ai_correct": is_correct,
                    "response_time": round(elapsed, 4),
                    "answered_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
            )
            file.flush()  # écriture immédiate sur le disque : rien n'est perdu en cas de coupure

            # Progression toutes les 50 questions (et à la fin)
            if i % 50 == 0 or i == len(todo):
                remaining = (time.perf_counter() - start) / i * (len(todo) - i)
                print(
                    f"{i}/{len(todo)} | précision : {correct_count / i:.1%} | "
                    f"reste environ {remaining / 60:.0f} min"
                )

    print(f"Terminé : réponses enregistrées dans {AI_ANSWERS_RAW_CSV}")


def main():
    args = parse_args()
    client = OpenAI(base_url=LLM_BASE_URL, api_key="lm-studio")

    questions = load_questions()
    if args.limit:
        questions = questions.head(args.limit)

    # Un prompt après l'autre : chaque prompt est terminé avant de passer au suivant
    for prompt_id in args.prompt:
        run_prompt(client, questions, args.model, prompt_id)


if __name__ == "__main__":
    main()
