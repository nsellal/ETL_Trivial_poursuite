"""Dashboard Streamlit du benchmark Trivial Poursuite.

Lit la base DuckDB construite par dbt (gold/warehouse.duckdb) en lecture seule :
- les 4 marts (gold) pour les graphiques : aucun calcul métier n'est refait ici ;
- intermediate.int_answers pour le détail ligne à ligne
  (distribution des temps de réponse et explorateur des erreurs).

Barre latérale : filtres (runs, difficulté, type, catégories) et paramètres
d'affichage (indicateur, couleurs, thème, valeurs, hauteur).
Chaque onglet propose plusieurs vues du même mart.
"""
import duckdb
import plotly.express as px
import streamlit as st

from config import WAREHOUSE_PATH

st.set_page_config(
    page_title="Benchmark Trivial Poursuite", page_icon="🎲", layout="wide"
)

# ---------------------------------------------------------------------------
# Constantes d'affichage
# ---------------------------------------------------------------------------
# Palettes de couleurs proposées (une couleur par série)
PALETTES = {
    "Vive": px.colors.qualitative.Vivid,
    "Contrastée": px.colors.qualitative.Bold,
    "Pastel": px.colors.qualitative.Pastel,
    "Accessible (daltonisme)": px.colors.qualitative.Safe,
    "Plotly": px.colors.qualitative.Plotly,
}
# Dégradés pour les cartes de chaleur et le treemap
SCALES = {
    "Rouge → vert": "RdYlGn",
    "Viridis": "Viridis",
    "Bleus": "Blues",
    "Plasma": "Plasma",
}
THEMES = {
    "Clair": "plotly_white",
    "Sombre": "plotly_dark",
    "Seaborn": "seaborn",
    "ggplot": "ggplot2",
}
# Indicateur affiché dans les graphiques (colonne présente dans les marts)
METRICS = {"Précision": "accuracy", "Gain sur le hasard": "gain_over_random"}
# Colonne utilisée pour colorer les séries
COLOR_BY = {"Run (modèle × prompt)": "run", "Modèle": "model_name", "Prompt": "prompt_id"}

DIFFICULTY_LABELS = {"easy": "facile", "medium": "moyen", "hard": "difficile"}
TYPE_LABELS = {"multiple": "QCM (4 choix)", "boolean": "Vrai / faux"}

# Colonnes de int_answers utiles au dashboard (inutile de charger le reste)
ANSWER_COLUMNS = """
    model, prompt_id, category_group, category, difficulty, type,
    question, correct_answer, choices_order, expected_letter,
    ai_answer, raw_output, ai_correct, is_unparsable, response_time
"""


# ---------------------------------------------------------------------------
# Lecture des données
# ---------------------------------------------------------------------------
@st.cache_data
def load_query(query):
    """Exécute une requête en lecture seule (ne bloque pas dbt) ; résultat gardé en cache."""
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        return con.sql(query).df()


def add_labels(df):
    """Ajoute des libellés courts et lisibles : nom du modèle, run, difficulté, type."""
    df = df.copy()
    df["model_name"] = df["model"].str.split("/").str[-1]
    df["run"] = df["model_name"] + " · " + df["prompt_id"]
    if "difficulty" in df:
        df["difficulty_label"] = df["difficulty"].map(DIFFICULTY_LABELS)
    if "type" in df:
        df["type_label"] = df["type"].map(TYPE_LABELS)
    return df


leaderboard = add_labels(load_query("select * from marts.mart_leaderboard"))
by_category = add_labels(load_query("select * from marts.mart_accuracy_by_category"))
by_difficulty = add_labels(load_query("select * from marts.mart_accuracy_by_difficulty"))
letter_bias = add_labels(load_query("select * from marts.mart_letter_bias"))
answers = add_labels(
    load_query(f"select {ANSWER_COLUMNS} from intermediate.int_answers")
)

# ---------------------------------------------------------------------------
# Barre latérale : filtres
# ---------------------------------------------------------------------------
st.sidebar.title("🎛️ Paramètres")

st.sidebar.header("Runs")
all_models = sorted(leaderboard["model_name"].unique())
all_prompts = sorted(leaderboard["prompt_id"].unique())
models = st.sidebar.multiselect("Modèles", all_models, default=all_models)
prompts = st.sidebar.multiselect("Prompts", all_prompts, default=all_prompts)
with st.sidebar.expander("Que disent les prompts ?"):
    st.markdown(
        "- `v1_simple` : la question, les choix, « Answer with the letter of the "
        "correct choice. »\n"
        "- `v2_strict` : « exactement une lettre, et rien d'autre »"
    )

st.sidebar.header("Questions")
difficulties = st.sidebar.multiselect(
    "Difficulté",
    list(DIFFICULTY_LABELS),
    default=list(DIFFICULTY_LABELS),
    format_func=DIFFICULTY_LABELS.get,
)
types = st.sidebar.multiselect(
    "Type de question", list(TYPE_LABELS), default=list(TYPE_LABELS),
    format_func=TYPE_LABELS.get,
)
all_groups = sorted(by_category["category_group"].unique())
groups = st.sidebar.multiselect(
    "Groupes de catégories", all_groups, default=all_groups
)
st.sidebar.caption(
    "Les filtres « Questions » s'appliquent aux onglets qui ont ce détail "
    "(catégories, difficulté, temps, explorateur). Le classement porte "
    "toujours sur le dataset complet."
)

# ---------------------------------------------------------------------------
# Barre latérale : affichage
# ---------------------------------------------------------------------------
st.sidebar.header("Affichage")
metric_label = st.sidebar.radio("Indicateur", list(METRICS), horizontal=True)
metric = METRICS[metric_label]
color_label = st.sidebar.selectbox("Colorer par", list(COLOR_BY))
color_col = COLOR_BY[color_label]
palette = PALETTES[st.sidebar.selectbox("Palette", list(PALETTES))]
scale = SCALES[st.sidebar.selectbox("Dégradé (cartes de chaleur)", list(SCALES))]
theme = THEMES[st.sidebar.selectbox("Thème des graphiques", list(THEMES))]
show_values = st.sidebar.toggle("Afficher les valeurs", value=True)
height = st.sidebar.slider("Hauteur des graphiques", 300, 900, 450, step=50)

# Thème et palette appliqués à tous les graphiques Plotly
px.defaults.template = theme
px.defaults.color_discrete_sequence = palette

# Couleur fixe par série, calculée sur TOUS les runs : une couleur ne change
# pas quand on filtre, et reste la même d'un onglet à l'autre
color_values = sorted(leaderboard[color_col].unique())
color_map = {v: palette[i % len(palette)] for i, v in enumerate(color_values)}

# Format des étiquettes de valeurs
pct_text = ".0%" if show_values else False
is_pct = metric == "accuracy"


def series(df_has_prompt=True):
    """Arguments Plotly communs aux graphiques à plusieurs séries.

    Si on colore par modèle (ou par prompt), deux runs peuvent partager une
    couleur : on les distingue alors par un motif de hachures.
    """
    kwargs = {"color": color_col, "color_discrete_map": color_map}
    if color_col == "model_name" and df_has_prompt:
        kwargs["pattern_shape"] = "prompt_id"
    elif color_col == "prompt_id":
        kwargs["pattern_shape"] = "model_name"
    return kwargs


def finish(fig, percent=True):
    """Mise en forme commune : hauteur, axe en pourcentage, légende en haut."""
    fig.update_layout(height=height, legend_title_text="", margin={"t": 60})
    if percent:
        fig.update_yaxes(tickformat=".0%")
    return fig


LABELS = {
    "accuracy": "Précision",
    "gain_over_random": "Gain sur le hasard",
    "run": "Run",
    "model_name": "Modèle",
    "prompt_id": "Prompt",
    "category": "Catégorie",
    "category_group": "Groupe",
    "difficulty_label": "Difficulté",
    "type_label": "Type",
    "nb_questions": "Questions",
    "median_response_time": "Temps médian (s)",
    "response_time": "Temps de réponse (s)",
    "letter": "Lettre",
}

# ---------------------------------------------------------------------------
# Application des filtres
# ---------------------------------------------------------------------------
runs = leaderboard[
    leaderboard["model_name"].isin(models) & leaderboard["prompt_id"].isin(prompts)
].sort_values("rank")
if runs.empty or not difficulties or not types or not groups:
    st.warning("Aucune donnée ne correspond aux filtres : élargis la sélection à gauche.")
    st.stop()
run_ids = runs["run_id"].tolist()

cat = by_category[
    by_category["run_id"].isin(run_ids) & by_category["category_group"].isin(groups)
]
diff = by_difficulty[
    by_difficulty["run_id"].isin(run_ids)
    & by_difficulty["difficulty"].isin(difficulties)
    & by_difficulty["type"].isin(types)
].sort_values("difficulty_order")
bias = letter_bias[letter_bias["run_id"].isin(run_ids)]
ans = answers[
    answers["model_name"].isin(models)
    & answers["prompt_id"].isin(prompts)
    & answers["difficulty"].isin(difficulties)
    & answers["type"].isin(types)
    & answers["category_group"].isin(groups)
]

# ---------------------------------------------------------------------------
# En-tête et KPIs
# ---------------------------------------------------------------------------
st.title("🎲 Benchmark de LLM locaux sur Open Trivia Database")
st.caption(
    f"{answers['question'].nunique():,} questions de culture générale posées à des "
    "modèles locaux via LM Studio · OpenTDB → bronze (CSV) → silver (Parquet) → "
    "dbt + DuckDB (staging → intermediate → marts) → Streamlit"
)

best = runs.sort_values("accuracy", ascending=False).iloc[0]
fastest = runs.sort_values("median_response_time").iloc[0]
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Runs sélectionnés", len(runs))
k2.metric(
    "Meilleure précision",
    f"{best['accuracy']:.1%}",
    f"{best['gain_over_random'] * 100:+.1f} pts vs hasard",
)
k3.metric("Hasard pur", f"{best['random_baseline']:.1%}")
k4.metric("Le plus rapide (médiane)", f"{fastest['median_response_time']:.2f} s")
k5.metric("Réponses filtrées", f"{len(ans):,}".replace(",", " "))
st.caption(f"🏆 Meilleur run : **{best['run']}** · ⚡ plus rapide : **{fastest['run']}**")

tabs = st.tabs(
    [
        "🏆 Classement",
        "✍️ Prompts",
        "🗂️ Catégories",
        "📶 Difficulté",
        "⏱️ Temps de réponse",
        "🔤 Biais de lettre",
        "🔍 Explorateur",
        "🗄️ Données des marts",
    ]
)

# ---------------------------------------------------------------------------
# 1. Classement (mart_leaderboard)
# ---------------------------------------------------------------------------
with tabs[0]:
    view = st.radio(
        "Vue",
        ["Barres", "Précision contre vitesse", "Radar multi-critères", "Tableau"],
        horizontal=True,
        key="view_rank",
    )
    if view == "Barres":
        fig = px.bar(
            runs.sort_values(metric),
            x=metric,
            y="run",
            orientation="h",
            text_auto=pct_text,
            labels=LABELS,
            title=f"{metric_label} par run",
            **series(),
        )
        if is_pct:
            fig.add_vline(
                x=best["random_baseline"], line_dash="dash",
                annotation_text="hasard",
            )
        finish(fig, percent=False).update_xaxes(tickformat=".0%")
        st.plotly_chart(fig, width="stretch")

    elif view == "Précision contre vitesse":
        fig = px.scatter(
            runs,
            x="median_response_time",
            y=metric,
            color=color_col,
            color_discrete_map=color_map,
            symbol="prompt_id",
            size="nb_questions",
            size_max=22,
            hover_name="run",
            text="run" if show_values else None,
            labels=LABELS,
            title=f"{metric_label} contre temps médian de réponse",
        )
        fig.update_traces(textposition="top center")
        st.plotly_chart(finish(fig), width="stretch")
        st.caption(
            "En haut à gauche = précis et rapide. Les temps dépendent de la machine : "
            "comparables seulement entre runs faits sur la même machine."
        )

    elif view == "Radar multi-critères":
        # Chaque critère est ramené entre 0 et 1 (1 = meilleur)
        radar = runs[["run", "accuracy", "format_rate", "gain_over_random"]].copy()
        radar["vitesse"] = (
            runs["median_response_time"].min() / runs["median_response_time"]
        )
        radar = radar.rename(
            columns={
                "accuracy": "précision",
                "format_rate": "format respecté",
                "gain_over_random": "gain sur le hasard",
            }
        ).melt(id_vars="run", var_name="critère", value_name="score")
        fig = px.line_polar(
            radar,
            r="score",
            theta="critère",
            color="run",
            line_close=True,
            range_r=[0, 1],
            title="Profil de chaque run (1 = meilleur)",
        )
        fig.update_traces(fill="toself", opacity=0.6)
        st.plotly_chart(finish(fig, percent=False), width="stretch")
        st.caption("Vitesse = temps médian du run le plus rapide / temps médian du run.")

    else:
        st.dataframe(
            runs[
                [
                    "rank", "run", "accuracy", "gain_over_random", "format_rate",
                    "nb_unparsable", "avg_response_time", "median_response_time",
                    "p95_response_time",
                ]
            ],
            column_config={
                "rank": "Rang",
                "run": "Run",
                "accuracy": st.column_config.ProgressColumn(
                    "Précision", format="percent", min_value=0, max_value=1
                ),
                "gain_over_random": st.column_config.NumberColumn(
                    "Gain sur le hasard", format="percent"
                ),
                "format_rate": st.column_config.ProgressColumn(
                    "Format respecté", format="percent", min_value=0, max_value=1
                ),
                "nb_unparsable": "Hors format",
                "avg_response_time": st.column_config.NumberColumn("Temps moyen (s)", format="%.2f"),
                "median_response_time": st.column_config.NumberColumn("Temps médian (s)", format="%.2f"),
                "p95_response_time": st.column_config.NumberColumn("Temps p95 (s)", format="%.2f"),
            },
            width="stretch",
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# 2. Prompts (mart_leaderboard) : effet de la consigne
# ---------------------------------------------------------------------------
with tabs[1]:
    view = st.radio(
        "Vue",
        ["Barres groupées", "Pente v1 → v2", "Écarts par modèle"],
        horizontal=True,
        key="view_prompt",
    )
    if view == "Barres groupées":
        left, right = st.columns(2)
        for column, col, title in [
            (left, metric, metric_label),
            (right, "format_rate", "Réponses au bon format (une lettre)"),
        ]:
            fig = px.bar(
                runs,
                x="model_name",
                y=col,
                color="prompt_id",
                barmode="group",
                text_auto=pct_text,
                title=title,
                labels={**LABELS, "format_rate": title},
            )
            column.plotly_chart(finish(fig), width="stretch")

    elif view == "Pente v1 → v2":
        fig = px.line(
            runs.sort_values("prompt_id"),
            x="prompt_id",
            y=metric,
            color="model_name",
            markers=True,
            text=metric if show_values else None,
            labels=LABELS,
            title=f"{metric_label} : de la consigne minimale à la consigne stricte",
        )
        fig.update_traces(marker_size=12, texttemplate="%{y:.1%}", textposition="top center")
        st.plotly_chart(finish(fig), width="stretch")

    else:
        gaps = runs.pivot_table(
            index="model_name", columns="prompt_id", values=["accuracy", "format_rate"]
        )
        gaps.columns = [f"{m}|{p}" for m, p in gaps.columns]
        if {"accuracy|v1_simple", "accuracy|v2_strict"} <= set(gaps.columns):
            gaps["gain précision (v2 − v1)"] = (
                gaps["accuracy|v2_strict"] - gaps["accuracy|v1_simple"]
            )
            gaps["gain format (v2 − v1)"] = (
                gaps["format_rate|v2_strict"] - gaps["format_rate|v1_simple"]
            )
        st.dataframe(
            gaps.reset_index(),
            column_config={
                c: st.column_config.NumberColumn(c, format="percent")
                for c in gaps.columns
            },
            width="stretch",
            hide_index=True,
        )
    st.caption(
        "Une réponse hors format (phrase au lieu d'une lettre, ou coupée par la limite "
        "de tokens) est comptée fausse : une consigne floue fait perdre des points "
        "même quand le modèle « sait » la réponse."
    )

# ---------------------------------------------------------------------------
# 3. Catégories (mart_accuracy_by_category)
# ---------------------------------------------------------------------------
with tabs[2]:
    view = st.radio(
        "Vue",
        ["Carte de chaleur", "Barres", "Écart à la moyenne du run", "Treemap", "Tableau"],
        horizontal=True,
        key="view_cat",
    )
    if cat.empty:
        st.info("Aucune catégorie pour ces filtres.")

    elif view == "Carte de chaleur":
        heat = cat.pivot_table(index="category", columns="run", values=metric)
        heat = heat.loc[heat.mean(axis=1).sort_values(ascending=False).index]
        fig = px.imshow(
            heat,
            text_auto=".0%" if show_values else False,
            color_continuous_scale=scale,
            zmin=0 if is_pct else None,
            zmax=1 if is_pct else None,
            aspect="auto",
            labels={"x": "Run", "y": "Catégorie", "color": metric_label},
            title=f"{metric_label} par catégorie et par run",
        )
        fig.update_layout(height=max(height, 28 * len(heat)))
        fig.update_coloraxes(colorbar_tickformat=".0%")
        st.plotly_chart(fig, width="stretch")

    elif view == "Barres":
        order = (
            cat.groupby("category")[metric].mean().sort_values().index.tolist()
        )
        fig = px.bar(
            cat,
            x=metric,
            y="category",
            orientation="h",
            barmode="group",
            category_orders={"category": order[::-1]},
            text_auto=pct_text,
            labels=LABELS,
            title=f"{metric_label} par catégorie",
            **series(),
        )
        fig.update_layout(height=max(height, 22 * len(order) * max(1, len(run_ids) // 2)))
        fig.update_xaxes(tickformat=".0%")
        fig.update_layout(legend_title_text="", margin={"t": 60})
        st.plotly_chart(fig, width="stretch")

    elif view == "Écart à la moyenne du run":
        run_choice = st.selectbox("Run", runs["run"], key="gap_run")
        one = cat[cat["run"] == run_choice].sort_values("gap_vs_run")
        one = one.assign(
            sens=one["gap_vs_run"].map(lambda g: "au-dessus" if g >= 0 else "en dessous")
        )
        fig = px.bar(
            one,
            x="gap_vs_run",
            y="category",
            orientation="h",
            color="sens",
            color_discrete_map={"au-dessus": "#2ca02c", "en dessous": "#d62728"},
            text_auto="+.1%" if show_values else False,
            hover_data={"accuracy": ":.1%", "nb_questions": True},
            labels={**LABELS, "gap_vs_run": "Écart à la précision globale du run"},
            title=f"Points forts et points faibles · {run_choice}",
        )
        fig.update_layout(height=max(height, 26 * len(one)), legend_title_text="")
        fig.update_xaxes(tickformat="+.0%")
        st.plotly_chart(fig, width="stretch")

    elif view == "Treemap":
        run_choice = st.selectbox("Run", runs["run"], key="tree_run")
        one = cat[cat["run"] == run_choice]
        fig = px.treemap(
            one,
            path=[px.Constant("Toutes"), "category_group", "category"],
            values="nb_questions",
            color=metric,
            color_continuous_scale=scale,
            range_color=[0, 1] if is_pct else None,
            hover_data={metric: ":.1%"},
            title=f"Taille = nombre de questions, couleur = {metric_label.lower()} · {run_choice}",
        )
        fig.update_traces(texttemplate="%{label}<br>%{color:.0%}")
        fig.update_layout(height=max(height, 600))
        st.plotly_chart(fig, width="stretch")

    else:
        st.dataframe(
            cat[
                [
                    "run", "category_group", "category", "nb_questions",
                    "share_of_run", "accuracy", "gain_over_random", "gap_vs_run",
                ]
            ].sort_values(["run", "accuracy"], ascending=[True, False]),
            column_config={
                "run": "Run",
                "category_group": "Groupe",
                "category": "Catégorie",
                "nb_questions": "Questions",
                "share_of_run": st.column_config.NumberColumn("Part du dataset", format="percent"),
                "accuracy": st.column_config.ProgressColumn(
                    "Précision", format="percent", min_value=0, max_value=1
                ),
                "gain_over_random": st.column_config.NumberColumn("Gain sur le hasard", format="percent"),
                "gap_vs_run": st.column_config.NumberColumn("Écart au run", format="percent"),
            },
            width="stretch",
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# 4. Difficulté × type (mart_accuracy_by_difficulty)
# ---------------------------------------------------------------------------
with tabs[3]:
    view = st.radio(
        "Vue",
        ["Barres (QCM / vrai-faux)", "Courbes", "Carte de chaleur", "Tableau"],
        horizontal=True,
        key="view_diff",
    )
    type_order = [TYPE_LABELS[t] for t in TYPE_LABELS if t in types]
    diff_order = [DIFFICULTY_LABELS[d] for d in DIFFICULTY_LABELS if d in difficulties]
    orders = {"type_label": type_order, "difficulty_label": diff_order}

    if diff.empty:
        st.info("Aucune donnée pour ces filtres.")

    elif view == "Barres (QCM / vrai-faux)":
        fig = px.bar(
            diff,
            x="difficulty_label",
            y=metric,
            barmode="group",
            facet_col="type_label",
            category_orders=orders,
            text_auto=pct_text,
            labels=LABELS,
            title=f"{metric_label} par difficulté",
            **series(),
        )
        # Ligne pointillée = score du hasard (seulement pour la précision)
        if is_pct:
            for i, label in enumerate(type_order, start=1):
                level = 0.25 if label == TYPE_LABELS["multiple"] else 0.5
                fig.add_hline(
                    y=level, line_dash="dash", col=i,
                    annotation_text=f"hasard ({level:.0%})",
                )
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        st.plotly_chart(finish(fig), width="stretch")
        st.caption(
            "QCM et vrai/faux sont séparés : le hasard donne 25 % sur un QCM mais 50 % "
            "sur un vrai/faux, les précisions brutes ne sont donc pas comparables."
        )

    elif view == "Courbes":
        fig = px.line(
            diff,
            x="difficulty_label",
            y=metric,
            color="run",
            line_dash="type_label",
            markers=True,
            category_orders=orders,
            labels=LABELS,
            title=f"{metric_label} quand la difficulté augmente",
        )
        fig.update_traces(marker_size=10)
        st.plotly_chart(finish(fig), width="stretch")
        st.caption("Trait plein / pointillé = type de question.")

    elif view == "Carte de chaleur":
        heat = diff.pivot_table(
            index="run", columns=["type_label", "difficulty_label"], values=metric
        )
        heat = heat[[c for t in type_order for d in diff_order if (c := (t, d)) in heat]]
        heat.columns = [f"{t} · {d}" for t, d in heat.columns]
        fig = px.imshow(
            heat,
            text_auto=".0%" if show_values else False,
            color_continuous_scale=scale,
            zmin=0 if is_pct else None,
            zmax=1 if is_pct else None,
            aspect="auto",
            labels={"x": "", "y": "Run", "color": metric_label},
            title=f"{metric_label} par run, type et difficulté",
        )
        fig.update_coloraxes(colorbar_tickformat=".0%")
        fig.update_layout(height=height)
        st.plotly_chart(fig, width="stretch")

    else:
        st.dataframe(
            diff[
                [
                    "run", "type_label", "difficulty_label", "nb_questions",
                    "accuracy", "random_baseline", "gain_over_random", "nb_unparsable",
                ]
            ],
            column_config={
                "run": "Run",
                "type_label": "Type",
                "difficulty_label": "Difficulté",
                "nb_questions": "Questions",
                "accuracy": st.column_config.ProgressColumn(
                    "Précision", format="percent", min_value=0, max_value=1
                ),
                "random_baseline": st.column_config.NumberColumn("Hasard", format="percent"),
                "gain_over_random": st.column_config.NumberColumn("Gain sur le hasard", format="percent"),
                "nb_unparsable": "Hors format",
            },
            width="stretch",
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# 5. Temps de réponse (int_answers + mart_leaderboard)
# ---------------------------------------------------------------------------
with tabs[4]:
    view = st.radio(
        "Vue",
        ["Boîtes à moustaches", "Histogramme", "Moyenne / médiane / p95", "Bonnes vs mauvaises"],
        horizontal=True,
        key="view_time",
    )
    # Au-delà du 99e centile : quelques valeurs extrêmes écrasent les graphiques
    cap = ans["response_time"].quantile(0.99) if not ans.empty else 0
    trimmed = ans[ans["response_time"] <= cap]

    if ans.empty:
        st.info("Aucune réponse pour ces filtres.")

    elif view == "Boîtes à moustaches":
        fig = px.box(
            trimmed,
            x="run",
            y="response_time",
            points=False,
            labels=LABELS,
            title="Distribution des temps de réponse",
            color=color_col,
            color_discrete_map=color_map,
        )
        st.plotly_chart(finish(fig, percent=False), width="stretch")
        st.caption(f"Valeurs au-delà du 99e centile ({cap:.2f} s) masquées.")

    elif view == "Histogramme":
        fig = px.histogram(
            trimmed,
            x="response_time",
            color="run",
            nbins=80,
            barmode="overlay",
            opacity=0.6,
            labels=LABELS,
            title="Répartition des temps de réponse",
        )
        fig.update_yaxes(title="Nombre de réponses")
        st.plotly_chart(finish(fig, percent=False), width="stretch")
        st.caption(f"Valeurs au-delà du 99e centile ({cap:.2f} s) masquées.")

    elif view == "Moyenne / médiane / p95":
        summary = runs.melt(
            id_vars="run",
            value_vars=["avg_response_time", "median_response_time", "p95_response_time"],
            var_name="mesure",
            value_name="secondes",
        )
        summary["mesure"] = summary["mesure"].map(
            {
                "avg_response_time": "moyenne",
                "median_response_time": "médiane",
                "p95_response_time": "95e centile",
            }
        )
        fig = px.bar(
            summary,
            x="run",
            y="secondes",
            color="mesure",
            barmode="group",
            text_auto=".2f" if show_values else False,
            labels={**LABELS, "secondes": "Temps (s)"},
            title="Temps de réponse par run (dataset complet)",
        )
        st.plotly_chart(finish(fig, percent=False), width="stretch")

    else:
        outcome = ans.groupby(["run", "ai_correct"])["response_time"].median().reset_index()
        outcome["résultat"] = outcome["ai_correct"].map(
            {True: "bonne réponse", False: "mauvaise réponse"}
        )
        fig = px.bar(
            outcome,
            x="run",
            y="response_time",
            color="résultat",
            barmode="group",
            color_discrete_map={"bonne réponse": "#2ca02c", "mauvaise réponse": "#d62728"},
            text_auto=".2f" if show_values else False,
            labels={**LABELS, "response_time": "Temps médian (s)"},
            title="Le modèle met-il plus de temps quand il se trompe ?",
        )
        st.plotly_chart(finish(fig, percent=False), width="stretch")

# ---------------------------------------------------------------------------
# 6. Biais de lettre (mart_letter_bias)
# ---------------------------------------------------------------------------
with tabs[5]:
    view = st.radio(
        "Vue",
        ["Attendu vs répondu", "Rappel vs fiabilité", "Sur-réponse (tous les runs)", "Tableau"],
        horizontal=True,
        key="view_bias",
    )
    if view in ("Attendu vs répondu", "Rappel vs fiabilité"):
        run_choice = st.selectbox("Run", runs["run"], key="bias_run")
        one = bias[bias["run"] == run_choice].sort_values("letter")

    if view == "Attendu vs répondu":
        bars = one.melt(
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
            text_auto=True if show_values else False,
            labels={**LABELS, "nombre": "Nombre de réponses"},
            title=f"Combien de fois chaque lettre ? · {run_choice}",
        )
        st.plotly_chart(finish(fig, percent=False), width="stretch")

    elif view == "Rappel vs fiabilité":
        rates = one.melt(
            id_vars="letter",
            value_vars=["recall", "precision"],
            var_name="mesure",
            value_name="taux",
        )
        rates["mesure"] = rates["mesure"].map({"recall": "rappel", "precision": "fiabilité"})
        fig = px.bar(
            rates,
            x="letter",
            y="taux",
            color="mesure",
            barmode="group",
            text_auto=pct_text,
            labels={**LABELS, "taux": "Taux"},
            title=f"Rappel et fiabilité par lettre · {run_choice}",
        )
        st.plotly_chart(finish(fig), width="stretch")
        st.caption(
            "Rappel = part des questions dont la bonne réponse est cette lettre que le "
            "modèle a trouvées. Fiabilité = quand le modèle répond cette lettre, part "
            "des fois où il a raison. Lettre sur-répondue = bon rappel, fiabilité faible."
        )

    elif view == "Sur-réponse (tous les runs)":
        heat = bias.pivot_table(index="run", columns="letter", values="over_answered")
        fig = px.imshow(
            heat,
            text_auto=True if show_values else False,
            color_continuous_scale="RdBu_r",
            color_continuous_midpoint=0,
            aspect="auto",
            labels={"x": "Lettre", "y": "Run", "color": "Écart"},
            title="Réponses données − réponses attendues (rouge = lettre trop choisie)",
        )
        fig.update_layout(height=height)
        st.plotly_chart(fig, width="stretch")

    else:
        st.dataframe(
            bias[
                ["run", "letter", "nb_expected", "nb_answered", "over_answered", "recall", "precision"]
            ].sort_values(["run", "letter"]),
            column_config={
                "run": "Run",
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

# ---------------------------------------------------------------------------
# 7. Explorateur des réponses (int_answers)
# ---------------------------------------------------------------------------
with tabs[6]:
    c1, c2, c3 = st.columns([2, 2, 3])
    run_choice = c1.selectbox("Run", runs["run"], key="explore_run")
    kind = c2.radio(
        "Réponses",
        ["Mauvaises", "Hors format", "Bonnes", "Toutes"],
        horizontal=True,
    )
    search = c3.text_input("Rechercher dans les questions", placeholder="ex. Mario, Paris…")

    rows = ans[ans["run"] == run_choice]
    if kind == "Mauvaises":
        rows = rows[~rows["ai_correct"]]
    elif kind == "Hors format":
        rows = rows[rows["is_unparsable"]]
    elif kind == "Bonnes":
        rows = rows[rows["ai_correct"]]
    if search:
        rows = rows[rows["question"].str.contains(search, case=False, regex=False)]

    st.write(f"**{len(rows):,}** réponses".replace(",", " "))
    columns = [
        "category", "difficulty_label", "type_label", "question", "correct_answer",
        "expected_letter", "ai_answer", "raw_output", "choices_order", "response_time",
    ]
    st.dataframe(
        rows[columns],
        column_config={
            "category": "Catégorie",
            "difficulty_label": "Difficulté",
            "type_label": "Type",
            "question": st.column_config.TextColumn("Question", width="large"),
            "correct_answer": "Bonne réponse",
            "expected_letter": "Lettre attendue",
            "ai_answer": "Lettre du modèle",
            "raw_output": "Sortie brute",
            "choices_order": "Ordre des choix",
            "response_time": st.column_config.NumberColumn("Temps (s)", format="%.2f"),
        },
        width="stretch",
        hide_index=True,
    )
    st.download_button(
        "⬇️ Télécharger ces réponses (CSV)",
        rows[columns].to_csv(index=False).encode("utf-8"),
        file_name="reponses_filtrees.csv",
        mime="text/csv",
    )

# ---------------------------------------------------------------------------
# 8. Données des marts : chaque table gold, filtrée, téléchargeable
# ---------------------------------------------------------------------------
with tabs[7]:
    marts = {
        "mart_leaderboard : classement des runs": runs,
        "mart_accuracy_by_category : précision par catégorie": cat,
        "mart_accuracy_by_difficulty : précision par difficulté × type": diff,
        "mart_letter_bias : biais de lettre": bias,
    }
    choice = st.selectbox("Mart", list(marts))
    table = marts[choice].drop(
        columns=["model_name", "run", "difficulty_label", "type_label"], errors="ignore"
    )
    st.caption(
        f"{len(table)} lignes · table `marts.{choice.split(' ')[0]}` de "
        "gold/warehouse.duckdb, construite et testée par dbt, filtrée selon la barre latérale."
    )
    st.dataframe(table, width="stretch", hide_index=True)
    st.download_button(
        "⬇️ Télécharger (CSV)",
        table.to_csv(index=False).encode("utf-8"),
        file_name=f"{choice.split(' ')[0]}.csv",
        mime="text/csv",
    )
