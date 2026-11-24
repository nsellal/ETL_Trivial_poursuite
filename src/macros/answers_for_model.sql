-- answers_for_model : réponses d'UN modèle d'IA, jointes à leurs questions.
-- Utilisée par les modèles intermediate/answers_<modèle>.sql pour éviter de
-- recopier la même requête pour chaque IA.
--
-- Paramètre : model_name = identifiant exact du modèle dans LM Studio
--             (colonne `model` de stg_ai_answers), ex. 'google/gemma-4-12b-qat'
--
-- 1 ligne = 1 réponse de ce modèle, avec un prompt, à une question.

{% macro answers_for_model(model_name) %}

with answers as (
    select * from {{ ref('stg_ai_answers') }}
    where model = '{{ model_name }}'
),

questions as (
    select * from {{ ref('stg_questions') }}
)

select
    -- Identifiants
    answers.answer_id,
    answers.question_id,
    answers.model,
    answers.prompt_id,

    -- Question (depuis stg_questions)
    questions.category,
    questions.category_group,
    questions.type,
    questions.difficulty,
    questions.question,
    questions.correct_answer,
    questions.nb_choices,

    -- Réponse du modèle
    answers.choices_order,
    answers.expected_letter,
    answers.raw_output,
    answers.ai_answer,
    answers.ai_correct,
    answers.ai_answer is null                   as is_unparsable,   -- pas de lettre reconnue
    1.0 / questions.nb_choices                  as random_baseline, -- chance de réussir au hasard

    -- Temps et date
    answers.response_time,
    answers.answered_at,
    answers.answered_at_repr

from answers
inner join questions
    on answers.question_id = questions.question_id

{% endmacro %}
