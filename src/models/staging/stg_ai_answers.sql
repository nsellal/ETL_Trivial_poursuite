-- stg_ai_answers : réponses des LLM, typées et nettoyées.
-- 1 ligne = 1 réponse d'un modèle, avec un prompt, à une question.
-- Clé : answer_id (question_id + model + prompt_id).
-- Pas de décodage ici : run_benchmark.py a écrit les textes déjà décodés.

with source as (
    select * from {{ source('bronze', 'ai_answers_raw') }}
)

select
    -- Clé technique : une seule réponse par couple question × modèle × prompt
    question_id || '|' || model || '|' || prompt_id  as answer_id,
    trim(question_id)                               as question_id,
    trim(model)                                     as model,
    trim(prompt_id)                                 as prompt_id,
    prompt_text,                                    -- trace exacte du prompt envoyé
    -- liste JSON en texte -> vraie liste DuckDB
    cast(choices_order::json as varchar[])          as choices_order,
    trim(expected_letter)                           as expected_letter,
    raw_output,                                     -- texte brut du modèle (non modifié)
    trim(ai_answer)                                 as ai_answer,   -- NULL si pas de lettre reconnue
    ai_correct,
    cast(response_time as double)                   as response_time,
    answered_at,                                    -- timestamp Unix (secondes, UTC)
    -- Représentation française, heure de Paris (heure d'été / d'hiver gérée)
    -- ex. 1795444434 -> '23/11/2026 15:33:54'
    strftime(
        timezone('Europe/Paris', to_timestamp(answered_at)),
        '%d/%m/%Y %H:%M:%S'
    )                                               as answered_at_repr
from source
-- Dédoublonnage : si un couple a été rejoué, on garde la réponse la plus récente
qualify row_number() over (
    partition by question_id, model, prompt_id order by answered_at desc
) = 1
