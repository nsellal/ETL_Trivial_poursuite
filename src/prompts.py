"""Prompts versionnés du benchmark : chaque réponse garde l'identifiant et le texte du prompt utilisé."""

# Emplacements remplis par run_benchmark.py :
#   {question} : texte de la question
#   {choices}  : les choix, une ligne par choix ("A) ...")
#   {letters}  : lettres autorisées ("A, B, C, D" ou "A, B")

PROMPTS = {
    # Consigne minimale
    "v1_simple": (
        "Question: {question}\n"
        "{choices}\n"
        "Answer with the letter of the correct choice."
    ),
    # Consigne stricte sur le format de la réponse
    "v2_strict": (
        "You are answering a multiple-choice quiz.\n"
        "Reply with exactly one letter among {letters}, and nothing else: "
        "no word, no punctuation, no explanation.\n\n"
        "Question: {question}\n"
        "{choices}\n"
        "Answer:"
    ),
}

# Prompt utilisé par défaut
DEFAULT_PROMPT_ID = "v2_strict"
