-- mart_accuracy_by_difficulty : précision par difficulté × type, pour chaque run.
-- Question métier : la difficulté OpenTDB et le type de question changent-ils la précision ?
-- 1 ligne = 1 run (modèle × prompt) × 1 difficulté × 1 type.
-- Le type est séparé car le hasard diffère : 50 % en vrai/faux, 25 % en QCM.

with answers as (
    select * from {{ ref('int_answers') }}
)

select
    model || '|' || prompt_id || '|' || difficulty || '|' || type  as run_difficulty_type_id,
    model || '|' || prompt_id                   as run_id,
    model,
    prompt_id,
    difficulty,
    -- Ordre logique pour les graphiques (sinon tri alphabétique : easy, hard, medium)
    case difficulty
        when 'easy'   then 1
        when 'medium' then 2
        when 'hard'   then 3
    end                                         as difficulty_order,
    type,
    count(*)                                    as nb_questions,
    avg(ai_correct::int)                        as accuracy,
    avg(random_baseline)                        as random_baseline,
    avg(ai_correct::int) - avg(random_baseline) as gain_over_random,
    sum(is_unparsable::int)                     as nb_unparsable
from answers
group by model, prompt_id, difficulty, type
order by run_id, difficulty_order, type
