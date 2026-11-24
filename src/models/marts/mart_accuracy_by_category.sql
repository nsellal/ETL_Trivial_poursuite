-- mart_accuracy_by_category : précision par catégorie, pour chaque run.
-- Question métier : sur quels thèmes les IA sont-elles fortes ou faibles ?
-- 1 ligne = 1 run (modèle × prompt) × 1 catégorie.

with answers as (
    select * from {{ ref('int_answers') }}
),

per_category as (
    select
        model || '|' || prompt_id                   as run_id,
        model,
        prompt_id,
        category_group,
        category,
        count(*)                                    as nb_questions,
        avg(ai_correct::int)                        as accuracy,
        avg(random_baseline)                        as random_baseline
    from answers
    group by model, prompt_id, category_group, category
),

-- Précision globale et taille de chaque run, pour comparer chaque catégorie
per_run as (
    select
        model || '|' || prompt_id                   as run_id,
        count(*)                                    as run_nb_questions,
        avg(ai_correct::int)                        as run_accuracy
    from answers
    group by model, prompt_id
)

select
    per_category.run_id || '|' || category          as run_category_id,
    per_category.*,
    accuracy - random_baseline                      as gain_over_random,
    accuracy - run_accuracy                         as gap_vs_run,      -- > 0 : mieux que sa moyenne
    nb_questions / run_nb_questions                 as share_of_run     -- poids de la catégorie
from per_category
inner join per_run
    on per_category.run_id = per_run.run_id
order by run_id, accuracy desc
