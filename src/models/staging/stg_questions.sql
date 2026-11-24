-- stg_questions : questions OpenTDB décodées, identifiées et dédoublonnées.
-- 1 ligne = 1 question unique (clé : question_id).

with source as (
    select * from {{ source('bronze', 'questions_raw') }}
),

-- Décodage url3986 des textes (sans trim : le hash doit être calculé
-- sur le même texte que dans run_benchmark.py)
decoded as (
    select
        cast(category_id as integer)        as category_id,
        url_decode(category)                as category,
        type,
        difficulty,
        url_decode(question)                as question,
        url_decode(correct_answer)          as correct_answer,
        -- liste JSON de textes encodés -> liste de textes décodés
        list_transform(
            cast(incorrect_answers::json as varchar[]),
            lambda x: url_decode(x)
        )                                   as incorrect_answers,
        scraped_at
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
    scraped_at
from identified
-- Dédoublonnage : on garde l'extraction la plus récente de chaque question
qualify row_number() over (
    partition by question_id order by scraped_at desc
) = 1
