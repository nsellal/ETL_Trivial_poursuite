"""Dashboard Streamlit du benchmark Trivial Poursuite.

Lit la base DuckDB construite par dbt (gold/warehouse.duckdb) en lecture seule :
- les 4 marts (gold) pour les graphiques : aucun calcul métier n'est refait ici ;
- intermediate.int_answers pour le détail ligne à ligne
  (distribution des temps de réponse et explorateur des erreurs).

Barre latérale : filtres (runs, difficulté, type, catégories) et paramètres
d'affichage (indicateur, couleur des séries, valeurs, hauteur).
Charte graphique fixe (palette, dégradé, thème) : voir aussi .streamlit/config.toml.
Chaque onglet suit la même structure : une question métier, une phrase
« À retenir » calculée sur les données, un graphique principal, puis les vues
secondaires dans un bloc repliable « Aller plus loin ».
"""
from datetime import datetime

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

from config import WAREHOUSE_PATH

st.set_page_config(
    page_title="Benchmark Trivial Poursuite", page_icon=":material/analytics:", layout="wide"
)

# ---------------------------------------------------------------------------
# Constantes d'affichage
# ---------------------------------------------------------------------------
# Charte graphique fixe : mêmes couleurs partout, comme un rapport client.
# Palette sobre (une couleur par série), de la plus foncée à la plus claire.
PALETTE = [
    "#1F3A5F",  # bleu marine (couleur principale)
    "#2E86AB",  # bleu
    "#3BA99C",  # sarcelle
    "#8C9BAB",  # gris bleuté
    "#C9A227",  # ocre (accent)
    "#6C4F8C",  # prune
    "#5B6770",  # ardoise
]
# Échelle divergente des cartes de chaleur et du treemap :
# rouge = sous la moyenne, gris = dans la moyenne, marine = au-dessus.
# Lisible par tous : on voit d'un coup d'œil les points faibles et les points forts.
DIVERGING = [[0, "#B2182B"], [0.5, "#F0EFEC"], [1, "#1F3A5F"]]
# Même échelle inversée : quand une valeur haute est MAUVAISE (lettre trop choisie)
DIVERGING_REVERSED = [[0, "#1F3A5F"], [0.5, "#F0EFEC"], [1, "#B2182B"]]
# Thème Plotly clair, cohérent avec le thème Streamlit (.streamlit/config.toml)
TEMPLATE = "plotly_white"
FONT = "Inter, Helvetica, Arial, sans-serif"
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
st.sidebar.title("Paramètres")

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
show_values = st.sidebar.toggle("Afficher les valeurs", value=True)
height = st.sidebar.slider("Hauteur des graphiques", 300, 900, 450, step=50)

# Charte appliquée à tous les graphiques Plotly
px.defaults.template = TEMPLATE
px.defaults.color_discrete_sequence = PALETTE

# Couleur fixe par série, calculée sur TOUS les runs : une couleur ne change
# pas quand on filtre, et reste la même d'un onglet à l'autre
color_values = sorted(leaderboard[color_col].unique())
color_map = {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(color_values)}

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
    fig.update_layout(
        height=height,
        legend_title_text="",
        margin={"t": 60},
        font={"family": FONT, "color": "#1F2937"},
    )
    if percent:
        fig.update_yaxes(tickformat=".0%")
    return fig


def heatmap(table, title, x_label, y_label):
    """Carte de chaleur centrée sur la moyenne des cases affichées.

    Les précisions vont de ~40 % à ~97 % : une échelle 0-100 % les rendrait toutes
    de la même couleur. On centre donc sur la moyenne (gris) et on étale
    symétriquement jusqu'à l'écart le plus grand : rouge = sous la moyenne,
    marine = au-dessus. Le texte de chaque case s'adapte au fond (noir ou blanc).
    """
    mean = table.stack().mean()
    spread = (table - mean).abs().max().max()
    fig = px.imshow(
        table,
        text_auto=".0%" if show_values else False,
        color_continuous_scale=DIVERGING,
        zmin=mean - spread,
        zmax=mean + spread,
        aspect="auto",
        labels={"x": x_label, "y": y_label, "color": metric_label},
        title=title,
    )
    # Fin liseré blanc entre les cases : chaque case se lit séparément
    fig.update_traces(xgap=2, ygap=2)
    # Libellés des colonnes en haut : lisibles sans descendre en bas du graphique
    fig.update_xaxes(side="top", title=None)
    fig.update_yaxes(title=None)
    fig.update_coloraxes(colorbar_tickformat=".0%", colorbar_title_text="")
    fig.update_layout(font={"family": FONT, "color": "#1F2937"}, margin={"t": 110})
    return fig, mean


def heatmap_legend(mean):
    """Phrase de lecture affichée au-dessus de chaque carte de chaleur."""
    st.caption(
        f"Lecture : **rouge** = sous la moyenne ({mean:.0%}), **gris** = dans la "
        "moyenne, **bleu marine** = au-dessus. Plus la couleur est foncée, plus "
        "l'écart est grand."
    )


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
    st.warning("Aucune donnée ne correspond aux filtres : élargissez la sélection dans le panneau de gauche.")
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
st.title("Benchmark de LLM locaux sur Open Trivia Database")
# Date de dernière mise à jour = dernière écriture de la base par dbt
updated_at = datetime.fromtimestamp(WAREHOUSE_PATH.stat().st_mtime)
st.caption(
    f"Périmètre : {answers['question'].nunique():,} questions de culture générale, "
    "modèles locaux via LM Studio · Source : Open Trivia Database · "
    "Chaîne : bronze (CSV) → silver (Parquet) → dbt + DuckDB (staging → "
    f"intermediate → marts) · Mise à jour : {updated_at:%d/%m/%Y %H:%M}"
)
st.divider()

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
st.caption(f"Meilleur run : **{best['run']}** · Plus rapide : **{fastest['run']}**")

tabs = st.tabs(
    [
        "Classement",
        "Prompts",
        "Catégories",
        "Difficulté",
        "Temps de réponse",
        "Biais de lettre",
        "Explorateur",
        "Données",
    ]
)


# ---------------------------------------------------------------------------
# Structure commune à chaque onglet :
#   1. une question métier en titre
#   2. une phrase « À retenir » calculée sur les données filtrées
#   3. un seul graphique principal
#   4. les vues secondaires et tableaux dans un bloc repliable « Aller plus loin »
# ---------------------------------------------------------------------------
def section(question, takeaway):
    """Titre de l'onglet (question métier) et conclusion en une phrase."""
    st.subheader(question)
    st.markdown(f"**À retenir :** {takeaway}")


def more():
    """Bloc repliable des vues secondaires (fermé par défaut)."""
    return st.expander("Aller plus loin : autres vues et tableaux")


def weighted(df, value="accuracy"):
    """Moyenne pondérée par le nombre de questions (évite qu'une petite case pèse autant qu'une grande)."""
    return (df[value] * df["nb_questions"]).sum() / df["nb_questions"].sum()


def pts(x):
    """Écart exprimé en points de pourcentage, ex. 0.123 -> '+12,3 pts'."""
    return f"{x * 100:+.1f} pts"


def num(n):
    """Entier avec espace des milliers, ex. 1763 -> '1 763'."""
    return f"{int(n):,}".replace(",", " ")


# Couleurs fixes des deux prompts : gris = consigne minimale, marine = consigne stricte
PROMPT_COLORS = {"v1_simple": "#8C9BAB", "v2_strict": "#1F3A5F"}
# Bonne / mauvaise réponse : mêmes couleurs que les cartes de chaleur
OUTCOME_COLORS = {"bonne réponse": "#1F3A5F", "mauvaise réponse": "#B2182B"}

# ---------------------------------------------------------------------------
# 1. Classement (mart_leaderboard)
# ---------------------------------------------------------------------------
with tabs[0]:
    ranked = runs.sort_values("accuracy", ascending=False)
    worst = ranked.iloc[-1]
    takeaway = (
        f"**{best['run']}** arrive en tête avec **{best['accuracy']:.1%}** de bonnes "
        f"réponses, soit {pts(best['gain_over_random'])} au-dessus du hasard."
    )
    if len(ranked) > 1:
        takeaway += (
            f" Le dernier, {worst['run']}, est à {worst['accuracy']:.1%} "
            f"({pts(worst['accuracy'] - best['accuracy'])})."
        )
    section("Quel modèle répond le mieux ?", takeaway)

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
            x=best["random_baseline"], line_dash="dash", line_color="#5B6770",
            annotation_text="hasard",
        )
    finish(fig, percent=False).update_xaxes(tickformat=".0%")
    st.plotly_chart(fig, width="stretch")

    with more():
        fig = px.scatter(
            runs,
            x="median_response_time",
            y=metric,
            color=color_col,
            color_discrete_map=color_map,
            symbol="prompt_id",
            hover_name="run",
            text="run" if show_values else None,
            labels=LABELS,
            title=f"{metric_label} contre temps médian de réponse",
        )
        fig.update_traces(textposition="top center", marker_size=14)
        st.plotly_chart(finish(fig), width="stretch")
        st.caption(
            "En haut à gauche = précis et rapide. Les temps dépendent de la machine : "
            "comparables seulement entre runs faits sur la même machine."
        )
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
    # Modèles testés avec les deux prompts : seuls ceux-là permettent une comparaison
    by_prompt = runs.pivot_table(
        index="model_name", columns="prompt_id", values=["accuracy", "format_rate"]
    )
    both = {("accuracy", "v1_simple"), ("accuracy", "v2_strict")} <= set(by_prompt.columns)
    if both:
        by_prompt = by_prompt.dropna()
        gain_acc = by_prompt[("accuracy", "v2_strict")] - by_prompt[("accuracy", "v1_simple")]
        gain_fmt = (
            by_prompt[("format_rate", "v2_strict")] - by_prompt[("format_rate", "v1_simple")]
        )
    if both and not by_prompt.empty:
        takeaway = (
            f"la consigne stricte (v2) fait gagner en moyenne **{pts(gain_acc.mean())}** "
            f"de précision, surtout parce que les réponses respectent mieux le format "
            f"({pts(gain_fmt.mean())} de réponses en une seule lettre)."
        )
    else:
        takeaway = "sélectionnez au moins un modèle testé avec les deux prompts pour les comparer."
    section("La façon de poser la question change-t-elle le résultat ?", takeaway)

    fig = px.bar(
        runs.sort_values(["model_name", "prompt_id"]),
        x="model_name",
        y=metric,
        color="prompt_id",
        color_discrete_map=PROMPT_COLORS,
        barmode="group",
        text_auto=pct_text,
        labels=LABELS,
        title=f"{metric_label} par modèle : consigne minimale (v1) contre stricte (v2)",
    )
    st.plotly_chart(finish(fig), width="stretch")
    st.caption(
        "Une réponse hors format (une phrase au lieu d'une lettre) est comptée fausse : "
        "une consigne floue fait perdre des points même quand le modèle « sait » la réponse."
    )

    with more():
        fig = px.bar(
            runs.sort_values(["model_name", "prompt_id"]),
            x="model_name",
            y="format_rate",
            color="prompt_id",
            color_discrete_map=PROMPT_COLORS,
            barmode="group",
            text_auto=pct_text,
            labels={**LABELS, "format_rate": "Format respecté"},
            title="Part des réponses au bon format (une seule lettre)",
        )
        st.plotly_chart(finish(fig), width="stretch")
        if both and not by_prompt.empty:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Modèle": by_prompt.index,
                        "Précision v1": by_prompt[("accuracy", "v1_simple")].values,
                        "Précision v2": by_prompt[("accuracy", "v2_strict")].values,
                        "Gain précision": gain_acc.values,
                        "Gain format": gain_fmt.values,
                    }
                ),
                column_config={
                    c: st.column_config.NumberColumn(c, format="percent")
                    for c in ["Précision v1", "Précision v2", "Gain précision", "Gain format"]
                },
                width="stretch",
                hide_index=True,
            )

# ---------------------------------------------------------------------------
# 3. Catégories (mart_accuracy_by_category)
# ---------------------------------------------------------------------------
with tabs[2]:
    if cat.empty:
        st.info("Aucune catégorie pour ces filtres.")
    else:
        heat = cat.pivot_table(index="category", columns="run", values=metric)
        # Catégories triées de la mieux réussie à la moins bien réussie
        heat = heat.loc[heat.mean(axis=1).sort_values(ascending=False).index]
        section(
            "Sur quels sujets les modèles sont-ils forts ou faibles ?",
            f"tous runs confondus, les modèles sont les plus forts en "
            f"**{heat.index[0]}** ({heat.iloc[0].mean():.0%}) et les plus faibles en "
            f"**{heat.index[-1]}** ({heat.iloc[-1].mean():.0%}).",
        )
        fig, mean = heatmap(
            heat, f"{metric_label} par catégorie et par run", "Run", "Catégorie"
        )
        fig.update_layout(height=max(height, 32 * len(heat) + 140))
        heatmap_legend(mean)
        st.plotly_chart(fig, width="stretch")

        with more():
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
                color_discrete_map={"au-dessus": "#1F3A5F", "en dessous": "#B2182B"},
                text_auto="+.1%" if show_values else False,
                hover_data={"accuracy": ":.1%", "nb_questions": True},
                labels={**LABELS, "gap_vs_run": "Écart à la précision globale du run"},
                title=f"Points forts et points faibles par rapport à sa moyenne · {run_choice}",
            )
            fig.update_layout(height=max(height, 26 * len(one)), legend_title_text="")
            fig.update_xaxes(tickformat="+.0%")
            st.plotly_chart(fig, width="stretch")

            fig = px.treemap(
                one,
                path=[px.Constant("Toutes"), "category_group", "category"],
                values="nb_questions",
                color=metric,
                color_continuous_scale=DIVERGING,
                color_continuous_midpoint=one[metric].mean(),
                hover_data={metric: ":.1%"},
                title=f"Poids de chaque catégorie dans le dataset (taille) et {metric_label.lower()} (couleur) · {run_choice}",
            )
            fig.update_traces(texttemplate="%{label}<br>%{color:.0%}")
            fig.update_layout(height=max(height, 600))
            st.plotly_chart(fig, width="stretch")

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
    type_order = [TYPE_LABELS[t] for t in TYPE_LABELS if t in types]
    diff_order = [DIFFICULTY_LABELS[d] for d in DIFFICULTY_LABELS if d in difficulties]
    orders = {"type_label": type_order, "difficulty_label": diff_order}

    if diff.empty:
        st.info("Aucune donnée pour ces filtres.")
    else:
        # Conclusion : écart entre le niveau le plus facile et le plus difficile affichés
        first, last = diff_order[0], diff_order[-1]
        acc_first = weighted(diff[diff["difficulty_label"] == first])
        acc_last = weighted(diff[diff["difficulty_label"] == last])
        takeaway = (
            f"la précision passe de **{acc_first:.0%}** ({first}) à **{acc_last:.0%}** "
            f"({last}) : la difficulté annoncée par OpenTDB se retrouve chez l'IA."
        )
        if len(type_order) == 2:
            gain_mcq = weighted(diff[diff["type"] == "multiple"], "gain_over_random")
            gain_tf = weighted(diff[diff["type"] == "boolean"], "gain_over_random")
            takeaway += (
                f" Par rapport au hasard, les QCM rapportent {pts(gain_mcq)} contre "
                f"seulement {pts(gain_tf)} pour les vrai/faux."
            )
        section("Les questions difficiles le sont-elles aussi pour l'IA ?", takeaway)

        fig = px.bar(
            diff,
            x="difficulty_label",
            y=metric,
            barmode="group",
            facet_col="type_label",
            category_orders=orders,
            text_auto=pct_text,
            labels=LABELS,
            title=f"{metric_label} par difficulté, QCM et vrai/faux séparés",
            **series(),
        )
        # Ligne pointillée = score du hasard (seulement pour la précision)
        if is_pct:
            for i, label in enumerate(type_order, start=1):
                level = 0.25 if label == TYPE_LABELS["multiple"] else 0.5
                fig.add_hline(
                    y=level, line_dash="dash", line_color="#5B6770", col=i,
                    annotation_text=f"hasard ({level:.0%})",
                )
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        st.plotly_chart(finish(fig), width="stretch")
        st.caption(
            "QCM et vrai/faux sont séparés : le hasard donne 25 % sur un QCM mais 50 % "
            "sur un vrai/faux, les précisions brutes ne sont donc pas comparables."
        )

        with more():
            heat = diff.pivot_table(
                index="run", columns=["type_label", "difficulty_label"], values=metric
            )
            heat = heat[[c for t in type_order for d in diff_order if (c := (t, d)) in heat]]
            heat.columns = [f"{t} · {d}" for t, d in heat.columns]
            fig, mean = heatmap(
                heat, f"{metric_label} par run, type et difficulté", "", "Run"
            )
            fig.update_layout(height=height)
            heatmap_legend(mean)
            st.plotly_chart(fig, width="stretch")

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
# 5. Temps de réponse (mart_leaderboard + int_answers)
# ---------------------------------------------------------------------------
with tabs[4]:
    slowest = runs.sort_values("median_response_time").iloc[-1]
    takeaway = (
        f"**{fastest['run']}** est le plus rapide avec **{fastest['median_response_time']:.2f} s** "
        "par question (médiane)."
    )
    if slowest["run"] != fastest["run"]:
        ratio = slowest["median_response_time"] / fastest["median_response_time"]
        takeaway += (
            f" Le plus lent, {slowest['run']}, met {slowest['median_response_time']:.2f} s, "
            f"soit {ratio:.1f}× plus."
        )
    section("Quel modèle répond le plus vite ?", takeaway)

    fig = px.bar(
        runs.sort_values("median_response_time", ascending=False),
        x="median_response_time",
        y="run",
        orientation="h",
        text_auto=".2f" if show_values else False,
        labels=LABELS,
        title="Temps médian de réponse par run (en secondes, plus court = mieux)",
        **series(),
    )
    st.plotly_chart(finish(fig, percent=False), width="stretch")
    st.caption(
        "Médiane = la moitié des questions ont été traitées plus vite que cette valeur. "
        "Les temps dépendent de la machine (MacBook Air, LM Studio)."
    )

    with more():
        if ans.empty:
            st.info("Aucune réponse pour ces filtres.")
        else:
            # Au-delà du 99e centile : quelques valeurs extrêmes écrasent le graphique
            cap = ans["response_time"].quantile(0.99)
            fig = px.box(
                ans[ans["response_time"] <= cap],
                x="run",
                y="response_time",
                points=False,
                labels=LABELS,
                title="Dispersion des temps de réponse",
                color=color_col,
                color_discrete_map=color_map,
            )
            st.plotly_chart(finish(fig, percent=False), width="stretch")
            st.caption(f"Valeurs au-delà du 99e centile ({cap:.2f} s) masquées.")

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
                color_discrete_map=OUTCOME_COLORS,
                text_auto=".2f" if show_values else False,
                labels={**LABELS, "response_time": "Temps médian (s)"},
                title="Le modèle met-il plus de temps quand il se trompe ?",
            )
            st.plotly_chart(finish(fig, percent=False), width="stretch")

# ---------------------------------------------------------------------------
# 6. Biais de lettre (mart_letter_bias)
# ---------------------------------------------------------------------------
with tabs[5]:
    # Sur-réponse relative : +20 % = la lettre est donnée 20 % plus souvent qu'attendu
    bias = bias.assign(over_rate=bias["nb_answered"] / bias["nb_expected"] - 1)
    top = bias.sort_values("over_rate", ascending=False).iloc[0]
    section(
        "Les modèles ont-ils une lettre préférée ?",
        f"oui. Le biais le plus fort est celui de **{top['run']}**, qui répond "
        f"**« {top['letter']} »** {num(top['nb_answered'])} fois alors que c'est la bonne "
        f"réponse {num(top['nb_expected'])} fois ({top['over_rate']:+.0%}). Comme l'ordre "
        "des choix est mélangé, chaque lettre devrait sortir autant qu'attendu.",
    )

    heat = bias.pivot_table(index="run", columns="letter", values="over_rate")
    fig = px.imshow(
        heat,
        text_auto="+.0%" if show_values else False,
        color_continuous_scale=DIVERGING_REVERSED,
        color_continuous_midpoint=0,
        aspect="auto",
        labels={"x": "Lettre", "y": "Run", "color": "Écart"},
        title="Chaque lettre est-elle répondue plus ou moins souvent qu'attendu ?",
    )
    fig.update_traces(xgap=2, ygap=2)
    fig.update_xaxes(side="top", title=None)
    fig.update_yaxes(title=None)
    fig.update_coloraxes(colorbar_tickformat="+.0%", colorbar_title_text="")
    fig.update_layout(height=height, font={"family": FONT, "color": "#1F2937"}, margin={"t": 110})
    st.caption(
        "Lecture : **rouge** = lettre répondue trop souvent, **gris** = autant "
        "qu'attendu, **bleu marine** = pas assez souvent."
    )
    st.plotly_chart(fig, width="stretch")

    with more():
        run_choice = st.selectbox("Run", runs["run"], key="bias_run")
        one = bias[bias["run"] == run_choice].sort_values("letter")
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
            color_discrete_map={"rappel": "#8C9BAB", "fiabilité": "#1F3A5F"},
            barmode="group",
            text_auto=pct_text,
            labels={**LABELS, "taux": "Taux"},
            title=f"Rappel et fiabilité par lettre · {run_choice}",
        )
        st.plotly_chart(finish(fig), width="stretch")
        st.caption(
            "Rappel = part des questions dont la bonne réponse est cette lettre que le "
            "modèle a trouvées. Fiabilité = quand le modèle répond cette lettre, part "
            "des fois où il a raison. Lettre trop choisie = bon rappel, fiabilité faible."
        )
        st.dataframe(
            bias[
                ["run", "letter", "nb_expected", "nb_answered", "over_rate", "recall", "precision"]
            ].sort_values(["run", "letter"]),
            column_config={
                "run": "Run",
                "letter": "Lettre",
                "nb_expected": "Attendues",
                "nb_answered": "Répondues",
                "over_rate": st.column_config.NumberColumn("Écart", format="percent"),
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
    st.subheader("Sur quelles questions le modèle se trompe-t-il ?")
    c1, c2, c3 = st.columns([2, 2, 3])
    run_choice = c1.selectbox("Run", runs["run"], key="explore_run")
    kind = c2.radio(
        "Réponses",
        ["Mauvaises", "Hors format", "Bonnes", "Toutes"],
        horizontal=True,
    )
    search = c3.text_input("Rechercher dans les questions", placeholder="ex. Mario, Paris…")

    of_run = ans[ans["run"] == run_choice]
    errors = of_run[~of_run["ai_correct"]]
    if not errors.empty:
        top_cat = errors["category"].value_counts()
        st.markdown(
            f"**À retenir :** {num(len(errors))} erreurs sur {num(len(of_run))} réponses "
            f"({len(errors) / len(of_run):.0%}). La catégorie qui en concentre le plus "
            f"est **{top_cat.index[0]}** ({num(top_cat.iloc[0])} erreurs)."
        )

    rows = of_run
    if kind == "Mauvaises":
        rows = rows[~rows["ai_correct"]]
    elif kind == "Hors format":
        rows = rows[rows["is_unparsable"]]
    elif kind == "Bonnes":
        rows = rows[rows["ai_correct"]]
    if search:
        rows = rows[rows["question"].str.contains(search, case=False, regex=False)]

    st.caption(f"{num(len(rows))} réponses affichées")
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
        "Télécharger ces réponses (CSV)",
        rows[columns].to_csv(index=False).encode("utf-8"),
        file_name="reponses_filtrees.csv",
        mime="text/csv",
        icon=":material/download:",
    )

# ---------------------------------------------------------------------------
# 8. Données des marts : chaque table gold, filtrée, téléchargeable
# ---------------------------------------------------------------------------
with tabs[7]:
    st.subheader("D'où viennent les chiffres ?")
    st.markdown(
        "Chaque graphique lit une table **gold** de la base DuckDB, construite et "
        "testée par dbt. Elles sont consultables et téléchargeables ici, filtrées "
        "selon le panneau de gauche."
    )
    marts = {
        "mart_leaderboard : classement des runs": runs,
        "mart_accuracy_by_category : précision par catégorie": cat,
        "mart_accuracy_by_difficulty : précision par difficulté × type": diff,
        "mart_letter_bias : biais de lettre": bias.drop(columns="over_rate"),
    }
    choice = st.selectbox("Table", list(marts))
    table = marts[choice].drop(
        columns=["model_name", "run", "difficulty_label", "type_label"], errors="ignore"
    )
    st.caption(f"{len(table)} lignes · table `marts.{choice.split(' ')[0]}` de gold/warehouse.duckdb")
    st.dataframe(table, width="stretch", hide_index=True)
    st.download_button(
        "Télécharger (CSV)",
        table.to_csv(index=False).encode("utf-8"),
        file_name=f"{choice.split(' ')[0]}.csv",
        mime="text/csv",
        icon=":material/download:",
    )
