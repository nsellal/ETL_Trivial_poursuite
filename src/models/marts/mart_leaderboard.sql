-- mart_leaderboard : classement des runs (modèle × prompt).
-- Question métier : quel couple modèle × prompt répond le mieux, et à quelle vitesse ?
-- 1 ligne = 1 run. Les taux sont des fractions entre 0 et 1 (arrondis dans Streamlit).

with answers as (
    select * from {{ ref('int_answers') }}
),

per_run as (
    select
        model || '|' || prompt_id                   as run_id,
        model,
        prompt_id,
        count(*)                                    as nb_questions,

        -- Précision : les réponses inexploitables comptent comme fausses
        avg(ai_correct::int)                        as accuracy,
        -- Précision qu'on aurait en répondant au hasard (mélange QCM / vrai-faux)
        avg(random_baseline)                        as random_baseline,
        -- Part des réponses où une lettre a pu être extraite
        avg((not is_unparsable)::int)               as format_rate,
        sum(is_unparsable::int)                     as nb_unparsable,

        -- Vitesse (secondes)
        avg(response_time)                          as avg_response_time,
        median(response_time)                       as median_response_time,
        quantile_cont(response_time, 0.95)          as p95_response_time
    from answers
    group by model, prompt_id
)

select
    rank() over (order by accuracy desc)            as rank,
    *,
    accuracy - random_baseline                      as gain_over_random
from per_run
order by rank
