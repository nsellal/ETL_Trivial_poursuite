-- mart_letter_bias : biais de position des réponses (axe exploratoire).
-- Question métier : le modèle choisit-il certaines lettres plus souvent qu'elles
-- ne sont la bonne réponse (ex. se rabattre sur « B » quand il hésite) ?
-- 1 ligne = 1 run (modèle × prompt) × 1 lettre (A à D).
-- C et D n'existent que pour les QCM (vrai/faux = A ou B).

with answers as (
    select
        *,
        model || '|' || prompt_id                   as run_id
    from {{ ref('int_answers') }}
),

-- Combien de fois chaque lettre est la bonne réponse
expected as (
    select
        run_id,
        model,
        prompt_id,
        expected_letter                             as letter,
        count(*)                                    as nb_expected,
        -- Rappel : quand la bonne réponse est cette lettre, le modèle la trouve-t-il ?
        avg(ai_correct::int)                        as recall
    from answers
    group by run_id, model, prompt_id, expected_letter
),

-- Combien de fois le modèle choisit chaque lettre (réponses exploitables seulement)
answered as (
    select
        run_id,
        ai_answer                                   as letter,
        count(*)                                    as nb_answered,
        -- Fiabilité : quand le modèle répond cette lettre, a-t-il raison ?
        avg(ai_correct::int)                        as precision
    from answers
    where ai_answer is not null
    group by run_id, ai_answer
)

select
    expected.run_id || '|' || expected.letter       as run_letter_id,
    expected.run_id,
    expected.model,
    expected.prompt_id,
    expected.letter,
    expected.nb_expected,
    coalesce(answered.nb_answered, 0)               as nb_answered,
    -- > 0 : le modèle choisit cette lettre plus souvent qu'elle n'est attendue
    coalesce(answered.nb_answered, 0) - expected.nb_expected  as over_answered,
    expected.recall,
    answered.precision
from expected
left join answered
    on expected.run_id = answered.run_id
   and expected.letter = answered.letter
order by expected.run_id, expected.letter
