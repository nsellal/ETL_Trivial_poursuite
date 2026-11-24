-- stg_questions : questions OpenTDB décodées, identifiées et dédoublonnées.
-- 1 ligne = 1 question unique (clé : question_id).

with source as (
    select * from {{ source('bronze', 'questions_raw') }}
),

-- Les textes arrivent déjà décodés (url3986 -> UTF-8) par
-- src/bronze_to_silver.py, et incorrect_answers est déjà une liste.
-- Ne PAS rappeler url_decode : un texte contenant '%' serait abîmé.
-- (Sans trim ici : le hash doit être calculé sur le même texte que dans
-- run_benchmark.py)
decoded as (
    select
        cast(category_id as integer)        as category_id,
        category,
        type,
        difficulty,
        question,
        correct_answer,
        incorrect_answers,
        scraped_at                          -- timestamp Unix (secondes)
    from source
),

-- Identifiant de jointure, commun avec run_benchmark.py
identified as (
    select
        left(md5(question || '|' || correct_answer), 12) as question_id,
        *
    from decoded
)

select
    question_id,
    category_id,
    trim(category)                              as category,
    -- "Entertainment: Books" -> "Entertainment"
    trim(split_part(category, ':', 1))          as category_group,
    trim(type)                                  as type,
    trim(difficulty)                            as difficulty,
    trim(question)                              as question,
    trim(correct_answer)                        as correct_answer,
    incorrect_answers,
    len(incorrect_answers) + 1                  as nb_choices,
    scraped_at,                                 -- timestamp Unix (secondes, UTC)
    -- Représentation française, heure de Paris (heure d'été / d'hiver gérée)
    -- ex. 1795444434 -> '23/11/2026 15:33:54'
    strftime(
        timezone('Europe/Paris', to_timestamp(scraped_at)),
        '%d/%m/%Y %H:%M:%S'
    )                                           as scraped_at_repr
from identified
-- Dédoublonnage : on garde l'extraction la plus récente de chaque question
qualify row_number() over (
    partition by question_id order by scraped_at desc
) = 1
