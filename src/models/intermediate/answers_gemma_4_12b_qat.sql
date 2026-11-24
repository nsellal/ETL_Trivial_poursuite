-- Réponses de google/gemma-4-12b-qat, jointes à leurs questions.
{{ config(materialized='table') }}

{{ answers_for_model('google/gemma-4-12b-qat') }}
