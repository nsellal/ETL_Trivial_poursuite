"""Configuration centrale du projet : chemins des fichiers et modèle local."""
import os
from pathlib import Path

# Racine du projet (le dossier parent de src/)
ROOT_DIR = Path(__file__).resolve().parent.parent

# Dossiers de données
DATA_DIR = ROOT_DIR / "data"
BRONZE_DIR = DATA_DIR / "bronze"

# Fichiers de la couche Bronze
QUESTIONS_RAW_CSV = BRONZE_DIR / "questions_raw.csv"

# Serveur du modèle local (LM Studio par défaut, API compatible OpenAI)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-4-12b-qat")
