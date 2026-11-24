-- Test générique : échoue si le modèle ne contient aucune ligne.
-- Utile pour answers_<modèle> : une faute de frappe dans le nom du modèle
-- d'IA donnerait une table vide sans aucune erreur.
{% test not_empty(model) %}
    select 1
    where (select count(*) from {{ model }}) = 0
{% endtest %}
