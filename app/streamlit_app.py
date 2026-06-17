"""Application interactive de planification — bonus B6.

Interface "user-friendly" permettant à un planificateur Maghreb Steel (non
technique) de :
    - modifier le carnet de commandes (éditeur de tableau) ;
    - régler les options du modèle (règle galva, stocks, retards, campagnes,
      prix du HRC) ;
    - relancer l'optimisation et lire immédiatement le plan de marche, les
      goulots, les commandes refusées et leur contrainte bloquante.

Lancement :  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import copy
import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Rendre le package src importable quel que soit le dossier de lancement.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, reporting
from src.data_loader import Data, Order, load_data
from src.solve import run
from src.validation import validate


st.set_page_config(page_title="Simulateur Capacité–Commande — Maghreb Steel",
                   page_icon="🏭", layout="wide")


# --------------------------------------------------------------------------
# Données : chargées une fois et mises en cache
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _base_orders_df() -> pd.DataFrame:
    d = load_data()
    return pd.DataFrame([{
        "id": o.id, "client": o.client, "famille": o.famille, "grade": o.grade,
        "ep": o.ep, "larg": o.larg, "ton": o.ton, "prix": o.prix,
        "sem": o.sem, "prio": o.prio,
    } for o in d.orders])


@st.cache_resource(show_spinner=False)
def _base_data() -> Data:
    return load_data()


def _data_from_df(df: pd.DataFrame) -> Data:
    """Reconstruit un objet Data à partir du carnet édité (sans re-sanity)."""
    d = copy.deepcopy(_base_data())
    orders = []
    for _, r in df.iterrows():
        if not str(r["id"]).strip():
            continue
        orders.append(Order(
            id=str(r["id"]), client=str(r["client"]), famille=str(r["famille"]),
            grade=str(r["grade"]), ep=float(r["ep"]), larg=int(r["larg"]),
            ton=float(r["ton"]), prix=float(r["prix"]), sem=int(r["sem"]),
            prio=str(r["prio"]),
        ))
    d.orders = orders
    return d


# --------------------------------------------------------------------------
# En-tête
# --------------------------------------------------------------------------
st.title("🏭 Simulateur Capacité–Commande — Maghreb Steel")
st.caption("Site de Tit Mellil · horizon 4 semaines · maximisation de la marge "
           "sur coût variable · EMINES – UM6P")

# --------------------------------------------------------------------------
# Barre latérale : options du modèle
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Options du modèle")
    objectif = st.radio("🎯 Objectif", ["marge", "nb_commandes"],
                        format_func=lambda o: "Maximiser la marge (O1)" if o == "marge"
                        else "Maximiser le nb de commandes",
                        help="O1 de l'énoncé (marge) ou maximisation du "
                             "nombre de commandes honorées (MILP accept/reject).")
    galva = st.checkbox("Règle galva HDG (ep≤seuil→LGA)", value=True,
                        help="Hypothèse hors note de cadrage : fige LGA/LGB du HDG selon l'épaisseur.")
    seuil = st.slider("Seuil galva (mm)", 0.3, 1.0, 0.6, 0.05, disabled=not galva)
    stocks = st.checkbox("Stocks produits finis (E8)", value=True)
    enforce_min = st.checkbox("Imposer le stock de sécurité min", value=True,
                              disabled=not stocks)
    inclure_pk = st.checkbox("Stock PK qualifié par grade", value=True,
                             help="Ajoute le stock PK décapé (par grade, net du min sécurité) "
                                  "à la matière disponible, en plus de la dispo HRC.")
    retards = st.checkbox("Autoriser les retards (B2)", value=False)
    stockage = st.checkbox("Coûts de stockage (B3)", value=False)
    campagnes = st.checkbox("Campagnes / MILP (B4)", value=False)
    qmin = st.slider("Tonnage min de campagne (T)", 0, 1500, 100, 50,
                     disabled=not campagnes)
    hrc_mult = st.slider("Variation prix HRC (%)", -20, 30, 0, 5,
                         help="Scénario type E20 (HRC plus cher / moins cher).")
    st.divider()
    lancer = st.button("▶️ Lancer l'optimisation", type="primary", width="stretch")


# --------------------------------------------------------------------------
# Carnet de commandes éditable
# --------------------------------------------------------------------------
st.subheader("📋 Carnet de commandes (éditable)")
st.caption("Modifiez les tonnages, prix, semaines… ajoutez ou supprimez des lignes, "
           "puis cliquez sur « Lancer l'optimisation ».")
if "orders_df" not in st.session_state:
    st.session_state.orders_df = _base_orders_df()

col_a, col_b = st.columns([1, 1])
with col_a:
    if st.button("↺ Réinitialiser le carnet d'origine"):
        st.session_state.orders_df = _base_orders_df()
with col_b:
    st.metric("Lignes dans le carnet", len(st.session_state.orders_df))

edited = st.data_editor(st.session_state.orders_df, num_rows="dynamic",
                        width="stretch", height=280, key="editor")


# --------------------------------------------------------------------------
# Résolution + affichage
# --------------------------------------------------------------------------
def _options() -> config.ModelOptions:
    return config.ModelOptions(
        objectif=objectif,
        galva_rule_hdg=galva, galva_seuil_mm=seuil,
        stocks_pf=stocks, enforce_stock_min=enforce_min,
        inclure_stock_pk=inclure_pk,
        retards_autorises=retards, cout_stockage=stockage,
        campagnes=campagnes, q_min_campagne=float(qmin),
    )


def _plan_excel_bytes(sol) -> bytes:
    """Sérialise le plan de marche complet en classeur Excel (téléchargeable)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        reporting.plan_de_marche_famille(sol).to_excel(xl, sheet_name="Plan_famille")
        reporting.plan_de_marche_ligne(sol).to_excel(xl, sheet_name="Charge_ligne")
        reporting.utilisation_table(sol).to_excel(xl, sheet_name="Utilisation")
        reporting.marge_par_famille(sol).to_excel(xl, sheet_name="Marge_famille", index=False)
        reporting.commandes_refusees(sol).to_excel(xl, sheet_name="Refus", index=False)
        if not sol.is_mip:
            reporting.shadow_ressources(sol).to_excel(xl, sheet_name="ShadowPrices", index=False)
    return buf.getvalue()


if lancer:
    st.session_state.orders_df = edited
    data = _data_from_df(edited)
    if hrc_mult != 0:
        data.prix_hrc = {k: v * (1 + hrc_mult / 100.0) for k, v in data.prix_hrc.items()}
    opt = _options()
    with st.spinner("Résolution du programme linéaire (CBC)…"):
        sol = run(data, opt)
        rep = validate(data, sol.x, opt)
    st.session_state["sol"] = sol      # on persiste pour que l'UI survive aux reruns
    st.session_state["rep"] = rep

sol = st.session_state.get("sol")
rep = st.session_state.get("rep")

if sol is None:
    st.info("Réglez les options puis cliquez sur **« Lancer l'optimisation »** "
            "dans la barre latérale.")
elif sol.status != "Optimal":
    st.error(f"Statut du solveur : {sol.status} — le problème n'a pas d'optimum.")
else:
    k = reporting.kpis(sol)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Marge totale", f"{k['marge_totale_MAD']/1e6:.2f} MMAD")
    c2.metric("Taux de service", f"{k['taux_service_pct']:.1f} %")
    c3.metric("Marge moyenne", f"{k['marge_moyenne_MAD_par_T']:.0f} MAD/T")
    c4.metric("Refus (total/partiel)",
              f"{k['commandes_refus_total']} / {k['commandes_partielles']}")

    ok = "✅ Toutes les contraintes vérifiées (validation indépendante)" if rep.all_ok \
        else "❌ Violation détectée par la validation indépendante !"
    (st.success if rep.all_ok else st.error)(ok)

    st.download_button("⬇️ Télécharger le plan de marche (Excel)",
                       data=_plan_excel_bytes(sol), file_name="plan_de_marche.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    tabs = st.tabs(["📅 Plan de marche", "📊 Utilisation lignes",
                    "💰 Marge par famille", "💎 Shadow prices",
                    "🚫 Commandes refusées", "🧮 Prix plancher (B1)"])
    with tabs[0]:
        st.dataframe(reporting.plan_de_marche_famille(sol), width="stretch")
        st.caption("Tonnage fini produit par famille × semaine.")
    with tabs[1]:
        util = reporting.utilisation_table(sol)
        st.dataframe(util.style.background_gradient(cmap="YlOrRd", vmin=0, vmax=100)
                     .format("{:.0f}"), width="stretch")
        g = reporting.goulots(sol)
        st.caption("Goulots (≥99 %) : " +
                   (", ".join(f"{L} S{t}" for L, t, _ in g) or "aucun"))
    with tabs[2]:
        st.dataframe(reporting.marge_par_famille(sol), width="stretch",
                     hide_index=True)
        plus, moins = reporting.commandes_extremes(sol)
        if plus:
            st.write(f"**Plus rentable honorée** : {plus['ID']} "
                     f"({plus['Famille']} {plus['Grade']}, {plus['MargeU']:.0f} MAD/T)")
            st.write(f"**Moins rentable honorée** : {moins['ID']} "
                     f"({moins['Famille']} {moins['Grade']}, {moins['MargeU']:.0f} MAD/T)")
    with tabs[3]:
        if sol.is_mip:
            st.info("Shadow prices indisponibles en variante MILP (campagnes).")
        else:
            st.dataframe(reporting.shadow_ressources(sol), width="stretch",
                         hide_index=True)
            st.caption("Valeur marginale d'une unité de ressource saturée "
                       "(MAD/jour pour la capacité, MAD/T pour le HRC).")
    with tabs[4]:
        refus = reporting.commandes_refusees(sol)
        if refus.empty:
            st.success("Toutes les commandes sont honorées intégralement.")
        else:
            st.dataframe(refus, width="stretch", hide_index=True)
            st.caption("Contrainte bloquante tracée depuis les valeurs duales "
                       "(coût d'opportunité des ressources saturées).")
    with tabs[5]:
        st.caption("**Prix de vente minimal** (MAD/T) pour qu'une nouvelle commande soit "
                   "rentable, compte tenu des ressources saturées. Au-dessus de ce prix : "
                   "à accepter ; en dessous : à refuser ou renégocier.")
        if sol.is_mip:
            st.info("Les prix planchers nécessitent les shadow prices : utilisez "
                    "l'objectif « marge » (sans campagnes).")
        else:
            cps = st.number_input("Semaine de la consultation", 1, 4, 1)
            exemples = [("HDG", "DX51", 0.3, 1250), ("HDG", "DC01", 1.0, 1250),
                        ("PPGI", "DX51", 0.4, 1250), ("CRC", "S320", 0.4, 1100),
                        ("BACR", "DC01", 0.5, 1280), ("HRC DEC", "DC01", 3.0, 1320)]
            rows = []
            for fam, gr, ep, lg in exemples:
                r = reporting.prix_plancher(sol, fam, gr, ep, lg, int(cps))
                if r:
                    rows.append({"Famille": fam, "Grade": gr, "Ép.": ep, "Larg.": lg,
                                 "Prix plancher": r["prix_plancher"],
                                 "Coût de revient": r["cout_revient"],
                                 "Coût d'opportunité": r["cout_opportunite"],
                                 "Route": r["meilleure_route"]})
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            st.caption("Prix plancher = coût de revient + coût d'opportunité des ressources "
                       "rares (= 0 si aucune ressource saturée n'est consommée).")
