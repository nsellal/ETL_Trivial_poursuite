"""Dashboard Streamlit du benchmark Trivial Poursuite.

Lit la base DuckDB construite par dbt (gold/warehouse.duckdb) en lecture seule :
- les marts (gold) pour tous les graphiques : aucun calcul n'est refait ici ;
- intermediate.int_answers seulement pour le détail ligne à ligne
  (distribution des temps de réponse et explorateur des erreurs).
"""
import duckdb
import plotly.express as px
import streamlit as st

from config import WAREHOUSE_PATH

st.set_page_config(page_title="Benchmark Trivial Poursuite", layout="wide")

# Colonnes de int_answers utiles au dashboard (inutile de charger le reste)
ANSWER_COLUMNS = """
    model, prompt_id, model || '|' || prompt_id as run_id,
    category_group, category, difficulty, type,
    question, correct_answer, choices_order, expected_letter,
    ai_answer, raw_output, ai_correct, is_unparsable, response_time
"""


@st.cache_data
def load_query(query):
    """Exécute une requête en lecture seule (ne bloque pas dbt) ; résultat gardé en cache."""
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        return con.sql(query).df()


def load_table(table):
    """Lit une table entière de la base."""
    return load_query(f"select * from {table}")


def keep_runs(df, run_ids):
    """Ne garde que les lignes des runs sélectionnés dans les filtres."""
    return df[df["run_id"].isin(run_ids)]


# ---------------------------------------------------------------------------
# Chargement des données
# ---------------------------------------------------------------------------
leaderboard = load_table("marts.mart_leaderboard")
by_category = load_table("marts.mart_accuracy_by_category")
by_difficulty = load_table("marts.mart_accuracy_by_difficulty")
letter_bias = load_table("marts.mart_letter_bias")
answers = load_query(f"select {ANSWER_COLUMNS} from intermediate.int_answers")

st.title("Benchmark de LLM locaux sur Open Trivia Database")
st.caption(
    f"{answers['question'].nunique():,} questions de culture générale (QCM et vrai/faux) "
    "posées à des modèles locaux via LM Studio. Pipeline : OpenTDB → bronze (CSV) → "
    "silver (Parquet) → dbt + DuckDB (staging → intermediate → marts) → Streamlit."
)

# ---------------------------------------------------------------------------
# Filtres (barre latérale) : par défaut, tout est sélectionné
# ---------------------------------------------------------------------------
st.sidebar.header("Filtres")
all_models = sorted(leaderboard["model"].unique())
all_prompts = sorted(leaderboard["prompt_id"].unique())
models = st.sidebar.multiselect("Modèles", all_models, default=all_models)
prompts = st.sidebar.multiselect("Prompts", all_prompts, default=all_prompts)
st.sidebar.markdown(
    "**Prompts**\n"
    "- `v1_simple` : consigne minimale\n"
    "- `v2_strict` : une seule lettre, rien d'autre"
)

runs = leaderboard[
    leaderboard["model"].isin(models) & leaderboard["prompt_id"].isin(prompts)
]
if runs.empty:
    st.warning("Aucun run ne correspond aux filtres.")
    st.stop()
run_ids = runs["run_id"].tolist()

# ---------------------------------------------------------------------------
# KPIs : on met en avant le meilleur run de la sélection
# ---------------------------------------------------------------------------
best = runs.sort_values("accuracy", ascending=False).iloc[0]
col1, col2, col3, col4 = st.columns(4)
col1.metric("Runs sélectionnés", len(runs))
col2.metric("Meilleure précision", f"{best['accuracy']:.1%}")
col3.metric("Hasard pur", f"{best['random_baseline']:.1%}")
col4.metric("Temps médian du meilleur run", f"{best['median_response_time']:.2f} s")
st.caption(f"Meilleur run : {best['model']} × {best['prompt_id']}")

tabs = st.tabs(
    [
        "Classement",
        "Prompts",
        "Catégories",
        "Difficulté",
        "Temps de réponse",
        "Biais de lettre",
        "Explorateur des erreurs",
    ]
)

# ---------------------------------------------------------------------------
# 1. Classement : tableau + nuage précision / vitesse
# ---------------------------------------------------------------------------
with tabs[0]:
    st.subheader("Classement des runs (modèle × prompt)")
    st.dataframe(
        runs[
            [
                "rank", "model", "prompt_id", "accuracy", "gain_over_random",
                "format_rate", "nb_unparsable", "median_response_time",
                "p95_response_time",
            ]
        ],
        column_config={
            "rank": "Rang",
            "model": "Modèle",
            "prompt_id": "Prompt",
            "accuracy": st.column_config.NumberColumn("Précision", format="percent"),
            "gain_over_random": st.column_config.NumberColumn(
                "Gain sur le hasard", format="percent"
            ),
            "format_rate": st.column_config.NumberColumn(
                "Format respecté", format="percent"
            ),
            "nb_unparsable": "Inexploitables",
            "median_response_time": st.column_config.NumberColumn(
                "Temps médian (s)", format="%.2f"
            ),
            "p95_response_time": st.column_config.NumberColumn(
                "Temps p95 (s)", format="%.2f"
            ),
        },
        width="stretch",
        hide_index=True,
    )

    st.subheader("Précision contre vitesse")
    fig = px.scatter(
        runs,
        x="median_response_time",
        y="accuracy",
        color="model",
        symbol="prompt_id",
        text="prompt_id",
        labels={
            "median_response_time": "Temps médian de réponse (s)",
            "accuracy": "Précision",
            "model": "Modèle",
            "prompt_id": "Prompt",
        },
    )
    fig.update_traces(marker_size=14, textposition="top center")
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "En haut à gauche = précis et rapide. Les temps dépendent de la machine qui "
        "a fait tourner LM Studio : ils ne sont comparables qu'entre runs faits sur "
        "la même machine."
    )

# ---------------------------------------------------------------------------
# 2. Prompts : effet de la consigne sur la précision et le respect du format
# ---------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Effet du prompt : v1_simple contre v2_strict")
    left, right = st.columns(2)
    for column, metric, title in [
        (left, "accuracy", "Précision"),
        (right, "format_rate", "Réponses au bon format (une lettre)"),
    ]:
        fig = px.bar(
            runs,
            x="model",
            y=metric,
            color="prompt_id",
            barmode="group",
            text_auto=".1%",
            title=title,
            labels={"model": "Modèle", metric: title, "prompt_id": "Prompt"},
        )
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
        column.plotly_chart(fig, width="stretch")
    st.caption(
        "Une réponse hors format (phrase au lieu d'une lettre, ou coupée par la limite "
        "de tokens) est comptée fausse : une consigne floue fait perdre des points "
        "même quand le modèle « sait » la réponse."
    )

# ---------------------------------------------------------------------------
# 3. Catégories : carte de chaleur catégorie × run
# ---------------------------------------------------------------------------
with tabs[2]:
    st.subheader("Précision par catégorie")
    cat = keep_runs(by_category, run_ids)
    heat = cat.pivot_table(index="category", columns="run_id", values="accuracy")
    heat = heat.loc[heat.mean(axis=1).sort_values(ascending=False).index]
    fig = px.imshow(
        heat,
        text_auto=".0%",
        color_continuous_scale="RdYlGn",
        zmin=0,
        zmax=1,
        aspect="auto",
        labels={"x": "Run", "y": "Catégorie", "color": "Précision"},
    )
    fig.update_layout(height=max(400, 28 * len(heat)))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Poids de chaque catégorie dans le dataset")
    # Le nombre de questions par catégorie est le même pour tous les runs
    sizes = (
        cat[cat["run_id"] == run_ids[0]][["category", "nb_questions", "share_of_run"]]
        .sort_values("nb_questions", ascending=False)
    )
    st.dataframe(
        sizes,
        column_config={
            "category": "Catégorie",
            "nb_questions": "Questions",
            "share_of_run": st.column_config.NumberColumn("Part", format="percent"),
        },
        width="stretch",
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# 4. Difficulté × type de question, comparé au hasard
# ---------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Précision par difficulté et par type de question")
    diff = keep_runs(by_difficulty, run_ids).sort_values("difficulty_order")
    fig = px.bar(
        diff,
        x="difficulty",
        y="accuracy",
        color="run_id",
        barmode="group",
        facet_col="type",
        category_orders={"type": ["multiple", "boolean"]},
        text_auto=".0%",
        labels={"difficulty": "Difficulté", "accuracy": "Précision", "run_id": "Run"},
    )
    # Ligne pointillée = score obtenu en répondant au hasard
    fig.add_hline(y=0.25, line_dash="dash", col=1, annotation_text="hasard (1/4)")
    fig.add_hline(y=0.5, line_dash="dash", col=2, annotation_text="hasard (1/2)")
    fig.for_each_annotation(
        lambda a: a.update(
            text=a.text.replace("type=multiple", "QCM (4 choix)").replace(
                "type=boolean", "Vrai / faux"
            )
        )
    )
    fig.update_yaxes(tickformat=".0%", range=[0, 1])
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "QCM et vrai/faux sont séparés : le hasard donne 25 % sur un QCM mais 50 % "
        "sur un vrai/faux, les précisions brutes ne sont donc pas comparables."
    )

# ---------------------------------------------------------------------------
# 5. Distribution des temps de réponse
# ---------------------------------------------------------------------------
with tabs[4]:
    st.subheader("Distribution des temps de réponse")
    times = keep_runs(answers, run_ids)
    # Au-delà du 99e centile : quelques valeurs extrêmes qui écrasent le graphique
    cap = times["response_time"].quantile(0.99)
    fig = px.box(
        times[times["response_time"] <= cap],
        x="run_id",
        y="response_time",
        color="model",
        points=False,
        labels={"run_id": "Run", "response_time": "Temps de réponse (s)", "model": "Modèle"},
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Valeurs au-delà du 99e centile ({cap:.2f} s) masquées pour la lisibilité.")

    st.subheader("Le temps de réponse dépend-il de la réussite ?")
    by_outcome = (
        times.groupby(["run_id", "ai_correct"])["response_time"].median().reset_index()
    )
    by_outcome["ai_correct"] = by_outcome["ai_correct"].map(
        {True: "bonne réponse", False: "mauvaise réponse"}
    )
    fig = px.bar(
        by_outcome,
        x="run_id",
        y="response_time",
        color="ai_correct",
        barmode="group",
        text_auto=".2f",
        labels={"run_id": "Run", "response_time": "Temps médian (s)", "ai_correct": ""},
    )
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# 6. Biais de position : le modèle préfère-t-il certaines lettres ?
# ---------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Biais de lettre")
    run_choice = st.selectbox("Run", run_ids, key="bias_run")
    bias = letter_bias[letter_bias["run_id"] == run_choice].sort_values("letter")
    bars = bias.melt(
        id_vars="letter",
        value_vars=["nb_expected", "nb_answered"],
        var_name="série",
        value_name="nombre",
    )
    bars["série"] = bars["série"].map(
        {"nb_expected": "bonne réponse attendue", "nb_answered": "réponse du modèle"}
    )
    fig = px.bar(
        bars,
        x="letter",
        y="nombre",
        color="série",
        barmode="group",
        text_auto=True,
        labels={"letter": "Lettre", "nombre": "Nombre de réponses", "série": ""},
    )
    st.plotly_chart(fig, width="stretch")
    st.dataframe(
        bias[["letter", "nb_expected", "nb_answered", "over_answered", "recall", "precision"]],
        column_config={
            "letter": "Lettre",
            "nb_expected": "Attendues",
            "nb_answered": "Répondues",
            "over_answered": "Écart",
            "recall": st.column_config.NumberColumn("Rappel", format="percent"),
            "precision": st.column_config.NumberColumn("Fiabilité", format="percent"),
        },
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Rappel = part des questions dont la bonne réponse est cette lettre et que le "
        "modèle a trouvées. Fiabilité = part des fois où le modèle répond cette lettre "
        "et a raison. Une lettre sur-répondue a un bon rappel mais une fiabilité faible."
    )

# ---------------------------------------------------------------------------
# 7. Explorateur des erreurs : les réponses une par une
# ---------------------------------------------------------------------------
with tabs[6]:
    st.subheader("Explorateur des erreurs")
    c1, c2, c3 = st.columns(3)
    run_choice = c1.selectbox("Run", run_ids, key="errors_run")
    groups = ["Toutes"] + sorted(answers["category_group"].unique())
    group = c2.selectbox("Groupe de catégories", groups)
    kind = c3.radio(
        "Réponses affichées",
        ["Mauvaises réponses", "Hors format uniquement", "Toutes"],
        horizontal=True,
    )

    rows = answers[answers["run_id"] == run_choice]
    if group != "Toutes":
        rows = rows[rows["category_group"] == group]
    if kind == "Mauvaises réponses":
        rows = rows[~rows["ai_correct"]]
    elif kind == "Hors format uniquement":
        rows = rows[rows["is_unparsable"]]

    st.write(f"{len(rows):,} réponses")
    st.dataframe(
        rows[
            [
                "category", "difficulty", "question", "correct_answer",
                "expected_letter", "ai_answer", "raw_output", "choices_order",
            ]
        ],
        column_config={
            "category": "Catégorie",
            "difficulty": "Difficulté",
            "question": "Question",
            "correct_answer": "Bonne réponse",
            "expected_letter": "Lettre attendue",
            "ai_answer": "Lettre du modèle",
            "raw_output": "Sortie brute",
            "choices_order": "Ordre des choix",
        },
        width="stretch",
        hide_index=True,
    )
