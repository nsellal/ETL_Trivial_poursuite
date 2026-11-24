-- stg_obt_answers : OBT silver nettoyée et typée.
-- 1 ligne = 1 réponse d'un modèle, avec un prompt, à une question.
-- Clé : answer_id (question_id + model + prompt_id).
-- Pas de décodage ni de dédoublonnage ici : bronze_to_silver.py les a faits.

with source as (
    select * from {{ source('silver', 'obt_answers') }}
)

select
    -- Identifiants
    question_id || '|' || model || '|' || prompt_id  as answer_id,
    trim(question_id)                               as question_id,
    trim(model)                                     as model,
    trim(prompt_id)                                 as prompt_id,
    prompt_text,                                    -- trace exacte du prompt envoyé

    -- Question
    cast(category_id as integer)                    as category_id,
    trim(category)                                  as category,
    -- "Entertainment: Books" -> "Entertainment"
    trim(split_part(category, ':', 1))              as category_group,
    trim(type)                                      as type,
    trim(difficulty)                                as difficulty,
    trim(question)                                  as question,
    trim(correct_answer)                            as correct_answer,
    incorrect_answers,                              -- déjà une liste

    -- Réponse du modèle
    cast(choices_order::json as varchar[])          as choices_order, -- texte JSON -> liste
    trim(expected_letter)                           as expected_letter,
    raw_output,                                     -- texte brut du modèle (non modifié)
    trim(ai_answer)                                 as ai_answer,     -- NULL si pas de lettre reconnue
    ai_correct,
    response_time,                                  -- secondes (time.perf_counter)

    -- Dates : timestamp Unix (UTC) + représentation française, heure de Paris
    answered_at,
    strftime(timezone('Europe/Paris', to_timestamp(answered_at)),
             '%d/%m/%Y %H:%M:%S')                   as answered_at_repr,
    scraped_at,
    strftime(timezone('Europe/Paris', to_timestamp(scraped_at)),
             '%d/%m/%Y %H:%M:%S')                   as scraped_at_repr

from source
