-- int_answers : OBT enrichie des colonnes dérivées utiles à l'analyse.
-- 1 ligne = 1 réponse (question × modèle × prompt). Clé : answer_id.
-- Table de référence lue par les marts (gold) et le dashboard Streamlit.

with answers as (
    select * from {{ ref('stg_obt_answers') }}
),

enriched as (
    select
        *,
        len(incorrect_answers) + 1      as nb_choices      -- 4 (QCM) ou 2 (vrai/faux)
    from answers
)

select
    *,
    ai_answer is null                   as is_unparsable,   -- aucune lettre reconnue
    1.0 / nb_choices                    as random_baseline, -- chance de réussir au hasard
    length(question)                    as question_length, -- nb de caractères de la question
    length(raw_output)                  as output_length    -- nb de caractères de la réponse brute
from enriched
