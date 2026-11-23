"""Configuration centrale du projet : chemins des fichiers et modèle local."""
import os
from pathlib import Path

# Racine du projet (le dossier parent de src/)
ROOT_DIR = Path(__file__).resolve().parent.parent

# Couche Bronze (arborescence de Noah : dossier bronze/ à la racine)
BRONZE_DIR = ROOT_DIR / "bronze"

# Réponses brutes du modèle (produites par run_benchmark.py)
AI_ANSWERS_RAW_CSV = BRONZE_DIR / "ai_answers_raw.csv"


def latest_questions_csv():
    """Renvoie le CSV de questions le plus récent (le nom contient un timestamp)."""
    files = sorted(BRONZE_DIR.glob("questions_raw_*.csv"))
    if not files:
        raise FileNotFoundError(f"Aucun fichier questions_raw_*.csv dans {BRONZE_DIR}")
    return files[-1]

# Serveur du modèle local (LM Studio par défaut, API compatible OpenAI)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-4-12b-qat")
