"""Test : un premier appel au modèle local via LM Studio."""
import time

from openai import OpenAI

from config import LLM_BASE_URL, LLM_MODEL

# Client OpenAI pointé vers LM Studio (la clé n'est pas vérifiée en local)
client = OpenAI(base_url=LLM_BASE_URL, api_key="lm-studio")

prompt = """Answer with a single letter only.
Question: Harvard University is located in which city?
A) New York
B) Cambridge
C) Providence
D) Washington D.C.
Answer:"""

# On chronomètre uniquement l'appel au modèle
start = time.perf_counter()
response = client.chat.completions.create(
    model=LLM_MODEL,
    messages=[{"role": "user", "content": prompt}],
    temperature=0,
    max_tokens=8,
    reasoning_effort="none",  # désactive la phase de réflexion de Gemma 4 : réponse directe
)
elapsed = time.perf_counter() - start

print("Réponse brute :", repr(response.choices[0].message.content))
print(f"Temps de réponse : {elapsed:.2f} s")
