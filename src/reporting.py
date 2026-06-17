"""Analyse et restitution des résultats (questions E13–E19, B7).

Produit les indicateurs (marge, taux de service), le plan de marche, le taux
d'utilisation des lignes, les shadow prices interprétés, la liste des commandes
refusées AVEC leur contrainte bloquante (tracée depuis les valeurs duales,
pas "à la main"), les marges par famille, et exporte le tout (Excel/CSV/JSON
+ figures).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from . import config
from .solve import Solution
from .routes import best_route_per_order


# --------------------------------------------------------------------------
# Indicateurs globaux (E13)
# --------------------------------------------------------------------------
def kpis(sol: Solution) -> dict:
    d = sol.data
    livre = sol.total_livre()
    demande = sol.total_demande_scope()
    refus_total = refus_partiel = 0
    for i, o in d.orders_in_scope():
        s = sol.served.get(i, 0.0)
        if s < 1e-3:
            refus_total += 1
        elif s < o.ton - 1e-3:
            refus_partiel += 1
    return {
        "statut": sol.status,
        "marge_totale_MAD": round(sol.margin, 2),
        "tonnage_livre_T": round(livre, 1),
        "tonnage_demande_perimetre_T": round(demande, 1),
        "tonnage_demande_carnet_T": round(sum(o.ton for o in d.orders), 1),
        "taux_service_pct": round(sol.taux_service(), 2),
        "marge_moyenne_MAD_par_T": round(sol.margin / livre, 1) if livre else 0.0,
        "commandes_refus_total": refus_total,
        "commandes_partielles": refus_partiel,
        "is_mip": sol.is_mip,
    }


# --------------------------------------------------------------------------
# Plan de marche (E14)
# --------------------------------------------------------------------------
def plan_de_marche_famille(sol: Solution) -> pd.DataFrame:
    """Tonnage FINI produit par famille × semaine (livrable Planification)."""
    rows = {f: {t: 0.0 for t in config.WEEKS} for f in config.FAMILIES}
    for (i, _rn, t), v in sol.x.items():
        rows[sol.data.orders[i].famille][t] += v
    df = pd.DataFrame(rows).T[config.WEEKS]
    df.columns = [f"S{t}" for t in config.WEEKS]
    df["Total"] = df.sum(axis=1)
    df.loc["TOTAL"] = df.sum(axis=0)
    return df.round(0)


def plan_de_marche_ligne(sol: Solution) -> pd.DataFrame:
    """Tonnage ENTRANT (charge matière) par ligne × semaine, toutes familles."""
    rows = {L: {t: 0.0 for t in config.WEEKS} for L in config.LINES}
    for (i, rn, t), v in sol.x.items():
        rc = sol.bm.coeffs[(i, rn)]
        for L, inp in rc.input_factor.items():
            rows[L][t] += inp * v
    df = pd.DataFrame(rows).T[config.WEEKS]
    df.columns = [f"S{t}" for t in config.WEEKS]
    df["Total"] = df.sum(axis=1)
    return df.round(0)


# --------------------------------------------------------------------------
# Utilisation des lignes (E16)
# --------------------------------------------------------------------------
def utilisation_table(sol: Solution) -> pd.DataFrame:
    rows = {}
    for L in config.LINES:
        rows[L] = {}
        for t in config.WEEKS:
            used, avail = sol.utilisation[(L, t)]
            rows[L][f"S{t}"] = round(100 * used / avail, 0) if avail > 0 else float("nan")
    return pd.DataFrame(rows).T


def goulots(sol: Solution, seuil: float = 99.0) -> list[tuple[str, int, float]]:
    out = []
    for (L, t), (used, avail) in sol.utilisation.items():
        if avail > 0 and 100 * used / avail >= seuil:
            out.append((L, t, 100 * used / avail))
    return sorted(out, key=lambda r: -r[2])


# --------------------------------------------------------------------------
# Shadow prices interprétés (E18)
# --------------------------------------------------------------------------
def shadow_ressources(sol: Solution) -> pd.DataFrame:
    """Shadow prices des contraintes de RESSOURCE (capacité + HRC), triés."""
    rows = []
    for name, (pi, slack) in sol.shadow.items():
        if abs(pi) < 1e-6:
            continue
        if name.startswith("cap_"):
            _, L, t = name.split("_")
            rows.append(("Capacité", f"{L} S{t}", pi, slack, "MAD/jour"))
        elif name.startswith("hrc_"):
            g = name[4:]
            rows.append(("HRC", g, pi, slack, "MAD/T"))
    df = pd.DataFrame(rows, columns=["Type", "Ressource", "ShadowPrice", "Slack", "Unité"])
    return df.sort_values("ShadowPrice", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# Commandes refusées + contrainte bloquante tracée par les duales (E17)
# --------------------------------------------------------------------------
def _cout_opportunite(sol: Solution, i: int, rname: str, t: int) -> dict[str, float]:
    """Contribution de chaque ressource saturée au coût d'opportunité d'1 T finie."""
    rc = sol.bm.coeffs[(i, rname)]
    contrib: dict[str, float] = {}
    for L, ch in rc.charge.items():
        pi = sol.shadow.get(f"cap_{L}_{t}", (0.0, 0.0))[0]
        if pi > 1e-6:
            contrib[f"cap {L} S{t}"] = ch * pi
    pi_hrc = sol.shadow.get(f"hrc_{rc.grade}", (0.0, 0.0))[0]
    if pi_hrc > 1e-6:
        contrib[f"HRC {rc.grade}"] = rc.hrc_factor * pi_hrc
    return contrib


def commandes_refusees(sol: Solution) -> pd.DataFrame:
    """Liste les commandes non honorées intégralement + contrainte bloquante.

    Pour chaque commande, on cherche la façon la MOINS chère (route, semaine)
    de la produire et on compare sa marge unitaire au coût d'opportunité des
    ressources saturées qu'elle consommerait. Si marge < coût d'opportunité,
    la commande est (partiellement) refusée et la ressource dominante est la
    contrainte bloquante.
    """
    d = sol.data
    rows = []
    for i, o in d.orders_in_scope():
        s = sol.served.get(i, 0.0)
        if s >= o.ton - 1e-3:
            continue
        # meilleure route (marge max) et meilleure semaine (coût d'oppo. min)
        routes_i = [(rn, rc) for (ii, rn), rc in sol.bm.coeffs.items() if ii == i]
        best = max(routes_i, key=lambda kv: kv[1].marge_u)
        rname, rc = best
        best_co, best_t, best_contrib = float("inf"), o.sem, {}
        for t in config.WEEKS:
            if t > o.sem and not sol.bm.opt.retards_autorises:
                continue
            contrib = _cout_opportunite(sol, i, rname, t)
            co = sum(contrib.values())
            if co < best_co:
                best_co, best_t, best_contrib = co, t, contrib
        bloquante = max(best_contrib, key=best_contrib.get) if best_contrib else "—"
        rows.append({
            "ID": o.id, "Famille": o.famille, "Grade": o.grade, "Ep": o.ep,
            "Demande_T": o.ton, "Livre_T": round(s, 1), "Prix": o.prix,
            "Sem": o.sem, "Prio": o.prio,
            "MargeU": round(rc.marge_u, 0), "CoutOppo": round(best_co, 0),
            "Contrainte_bloquante": bloquante,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Marge par famille (E19) et commandes extrêmes (B7)
# --------------------------------------------------------------------------
def marge_par_famille(sol: Solution) -> pd.DataFrame:
    agg: dict[str, list[float]] = {f: [0.0, 0.0] for f in config.FAMILIES}
    for (i, rn, t), v in sol.x.items():
        rc = sol.bm.coeffs[(i, rn)]
        agg[rc.famille][0] += v
        agg[rc.famille][1] += rc.marge_u * v
    rows = []
    for f, (ton, mg) in agg.items():
        rows.append({"Famille": f, "Tonnage_T": round(ton, 0),
                     "Marge_MAD": round(mg, 0),
                     "Marge_par_T": round(mg / ton, 0) if ton else 0.0})
    df = pd.DataFrame(rows).sort_values("Marge_par_T", ascending=False).reset_index(drop=True)
    return df


def commandes_extremes(sol: Solution):
    """(plus rentable, moins rentable) parmi les commandes honorées (B7)."""
    best = best_route_per_order(sol.bm.coeffs)
    honorees = [(i, best[i]) for i in sol.served if sol.served[i] > 1.0]
    honorees.sort(key=lambda kv: kv[1].marge_u)
    if not honorees:
        return None, None
    moins, plus = honorees[0], honorees[-1]
    def info(kv):
        i, rc = kv
        o = sol.data.orders[i]
        return {"ID": o.id, "Famille": o.famille, "Grade": o.grade, "Ep": o.ep,
                "Prix": o.prix, "MargeU": round(rc.marge_u, 0),
                "Livre_T": round(sol.served[i], 0)}
    return info(plus), info(moins)


# --------------------------------------------------------------------------
# Demande vs ressource (E3) — analyse a priori
# --------------------------------------------------------------------------
def demande_par_famille(sol: Solution) -> pd.DataFrame:
    d = sol.data
    agg = {}
    for o in d.orders:
        agg[o.famille] = agg.get(o.famille, 0.0) + o.ton
    df = pd.DataFrame([{"Famille": f, "Demande_T": round(t, 0)} for f, t in agg.items()])
    return df.sort_values("Demande_T", ascending=False).reset_index(drop=True)


def demande_vs_hrc(sol: Solution) -> pd.DataFrame:
    d = sol.data
    dem = {g: 0.0 for g in config.GRADES}
    for i, o in d.orders_in_scope():
        dem[o.grade] = dem.get(o.grade, 0.0) + o.ton
    rows = [{"Grade": g, "Demande_finie_T": round(dem[g], 0),
             "HRC_dispo_T": d.dispo_hrc[g],
             "Tension": "TENDU" if dem[g] > d.dispo_hrc[g] else "ok"}
            for g in config.GRADES]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Prix plancher d'acceptation (B1) — répond à la question du directeur commercial
# --------------------------------------------------------------------------
def prix_plancher(sol: Solution, famille: str, grade: str, ep: float, larg: int,
                  semaine: int, route_name: str | None = None) -> dict | None:
    """B1 -- Prix de vente plancher (MAD/T) pour accepter une nouvelle consultation.

    C'est la valeur de V telle que la marge unitaire egale le cout d'opportunite
    des ressources rares consommees la semaine demandee. La marge etant affine en
    V (m = A*V + B), le prix plancher vaut (cout_opportunite - B) / A ; sans
    ressource saturee, il se reduit au cout de revient variable. On renvoie la
    meilleure route (prix plancher minimal).
    """
    import copy as _copy
    from .data_loader import Order
    from .routes import build_route_coeffs
    d = sol.data
    o = Order(id="NEW", client="?", famille=famille, grade=grade, ep=float(ep),
              larg=int(larg), ton=1.0, prix=10000.0, sem=int(semaine), prio="Normale")
    d2 = _copy.deepcopy(d)
    idx = len(d2.orders)
    d2.orders.append(o)
    coeffs = build_route_coeffs(d2, sol.bm.opt)
    par_route = {}
    for (i, rn), rc in coeffs.items():
        if i != idx or (route_name and rn != route_name):
            continue
        # marge_u(V) = A*V + B  (A, B indépendants de V)
        valV = sum(rc.input_factor[p] * (d.rend[p]["decl"] * d.coef_decl
                                         + d.rend[p]["nc"] * d.coef_nc) for p in rc.procs)
        val0 = sum(rc.input_factor[p] * d.rend[p]["chute"] * d.prix_chute for p in rc.procs)
        A = 1.0 + valV
        B = val0 - rc.cout_hrc - rc.cout_transfo - rc.extra_zinc
        opp = rc.hrc_factor * sol.shadow.get(f"hrc_{grade}", (0.0, 0.0))[0]
        for L, ch in rc.charge.items():
            opp += ch * sol.shadow.get(f"cap_{L}_{int(semaine)}", (0.0, 0.0))[0]
        par_route[rn] = {
            "prix_plancher": round((opp - B) / A, 0),
            "cout_revient": round(-B / A, 0),
            "cout_opportunite": round(opp, 0),
        }
    if not par_route:
        return None
    best = min(par_route.items(), key=lambda kv: kv[1]["prix_plancher"])
    return {"meilleure_route": best[0], **best[1], "par_route": par_route}


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------
def figure_utilisation(sol: Solution, path=None):
    df = utilisation_table(sol)
    fig, ax = plt.subplots(figsize=(6, 4))
    im = ax.imshow(df.values, cmap="YlOrRd", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(df.columns))); ax.set_xticklabels(df.columns)
    ax.set_yticks(range(len(df.index))); ax.set_yticklabels(df.index)
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            val = df.values[r, c]
            if pd.notna(val):
                ax.text(c, r, f"{val:.0f}", ha="center", va="center", fontsize=8)
    ax.set_title("Taux d'utilisation des lignes (%)")
    fig.colorbar(im, ax=ax, label="% utilisation")
    fig.tight_layout()
    path = path or (config.FIGURE_DIR / "utilisation_lignes.png")
    fig.savefig(path, dpi=130); plt.close(fig)
    return path


def figure_marge_famille(sol: Solution, path=None):
    df = marge_par_famille(sol)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(df["Famille"], df["Marge_par_T"], color="#00508c")
    ax.set_ylabel("Marge (MAD / T livrée)")
    ax.set_title("Marge unitaire par famille")
    for i, v in enumerate(df["Marge_par_T"]):
        ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    path = path or (config.FIGURE_DIR / "marge_par_famille.png")
    fig.savefig(path, dpi=130); plt.close(fig)
    return path


def figure_demande_hrc(sol: Solution, path=None):
    """E3 : demande (produit fini) vs disponibilité HRC par grade — barres groupées."""
    df = demande_vs_hrc(sol)
    grades = df["Grade"].tolist()
    dem = df["Demande_finie_T"].tolist()
    disp = df["HRC_dispo_T"].tolist()
    x = list(range(len(grades)))
    w = 0.38
    fig, ax = plt.subplots(figsize=(6, 4))
    col_dem = ["#c0392b" if d > h else "#00508c" for d, h in zip(dem, disp)]
    ax.bar([i - w / 2 for i in x], dem, w, color=col_dem)
    ax.bar([i + w / 2 for i in x], disp, w, color="#b9c0c7")
    for i, (d, h) in enumerate(zip(dem, disp)):
        if d > h:
            ax.text(i, max(d, h) + 60, "tendu", ha="center", color="#c0392b",
                    fontsize=8, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(grades)
    ax.set_ylabel("Tonnes (équivalent)")
    ax.set_title("Demande (produit fini) vs disponibilité HRC, par grade")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color="#00508c", label="Demande (fini)"),
                       Patch(color="#c0392b", label="Demande > dispo (tendu)"),
                       Patch(color="#b9c0c7", label="HRC disponible")], fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = path or (config.FIGURE_DIR / "demande_hrc.png")
    config.FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130); plt.close(fig)
    return path


def figure_plan_marche(sol: Solution, path=None):
    """E14 : plan de marche — barres empilées du tonnage fini par famille × semaine."""
    rows = {f: [0.0] * len(config.WEEKS) for f in config.FAMILIES}
    for (i, _rn, t), v in sol.x.items():
        rows[sol.data.orders[i].famille][t - 1] += v
    weeks = [f"S{t}" for t in config.WEEKS]
    colors = {"CRC": "#00508c", "HDG": "#3b7dd8", "PPGI": "#2a9d8f",
              "BACR": "#e9a13b", "HRC DEC": "#9b8579"}
    fig, ax = plt.subplots(figsize=(6, 4))
    bottom = [0.0] * len(config.WEEKS)
    for f in config.FAMILIES:
        ax.bar(weeks, rows[f], bottom=bottom, label=f, color=colors.get(f, "#888"))
        bottom = [b + v for b, v in zip(bottom, rows[f])]
    ax.set_ylabel("Tonnage fini produit (T)")
    ax.set_title("Plan de marche : tonnage fini par famille × semaine")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = path or (config.FIGURE_DIR / "plan_marche.png")
    config.FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130); plt.close(fig)
    return path


def figure_refus_frontiere(sol: Solution, path=None):
    """E17 : pourquoi une commande est refusée — marge unitaire vs coût d'opportunité.

    Chaque point est une commande : en abscisse le coût d'opportunité (somme des
    shadow prices des ressources qu'elle consommerait), en ordonnée sa marge
    unitaire. La diagonale est la frontière de décision : au-dessus la commande
    est rentable (honorée), au-dessous son coût d'opportunité dépasse sa marge
    (refusée).
    """
    d = sol.data
    best = best_route_per_order(sol.bm.coeffs)
    pts = {"hon": [[], []], "par": [[], []], "ref": [[], []]}
    for i, o in d.orders_in_scope():
        if i not in best:
            continue
        rc = best[i]
        co = float("inf")
        for t in config.WEEKS:
            if t > o.sem and not sol.bm.opt.retards_autorises:
                continue
            co = min(co, sum(_cout_opportunite(sol, i, rc.route_name, t).values()))
        if co == float("inf"):
            co = 0.0
        s = sol.served.get(i, 0.0)
        key = "hon" if s >= o.ton - 1e-3 else ("par" if s > 1e-3 else "ref")
        pts[key][0].append(co); pts[key][1].append(rc.marge_u)
    fig, ax = plt.subplots(figsize=(6, 4.2))
    allv = [v for k in pts for v in pts[k][0] + pts[k][1]]
    lim = (max(allv) if allv else 1) * 1.05
    ax.plot([0, lim], [0, lim], "--", color="gray", lw=1.1,
            label="marge = coût d'opportunité")
    ax.scatter(pts["hon"][0], pts["hon"][1], c="#2a9d4a", s=30, label="honorée",
               edgecolors="white", linewidths=0.4)
    ax.scatter(pts["par"][0], pts["par"][1], c="#e9a13b", s=34, label="partielle")
    ax.scatter(pts["ref"][0], pts["ref"][1], c="#c0392b", s=42, marker="x",
               label="refusée")
    ax.set_xlabel("Coût d'opportunité = Σ shadow prices consommés (MAD/T)")
    ax.set_ylabel("Marge unitaire (MAD/T)")
    ax.set_title("Pourquoi une commande est refusée")
    ax.legend(fontsize=8, loc="lower right"); ax.grid(alpha=0.3)
    fig.tight_layout()
    path = path or (config.FIGURE_DIR / "refus_frontiere.png")
    config.FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130); plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Export complet
# --------------------------------------------------------------------------
def export_all(sol: Solution) -> None:
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    config.FIGURE_DIR.mkdir(exist_ok=True)

    k = kpis(sol)
    (config.OUTPUT_DIR / "kpis.json").write_text(
        json.dumps(k, ensure_ascii=False, indent=2), encoding="utf-8")

    with pd.ExcelWriter(config.OUTPUT_DIR / "plan_de_marche.xlsx") as xl:
        plan_de_marche_famille(sol).to_excel(xl, sheet_name="Plan_famille")
        plan_de_marche_ligne(sol).to_excel(xl, sheet_name="Charge_ligne")
        utilisation_table(sol).to_excel(xl, sheet_name="Utilisation")
        marge_par_famille(sol).to_excel(xl, sheet_name="Marge_famille", index=False)
        commandes_refusees(sol).to_excel(xl, sheet_name="Refus", index=False)
        shadow_ressources(sol).to_excel(xl, sheet_name="ShadowPrices", index=False)

    shadow_ressources(sol).to_csv(config.OUTPUT_DIR / "shadow_prices.csv", index=False)
    commandes_refusees(sol).to_csv(config.OUTPUT_DIR / "commandes_refusees.csv", index=False)
    figure_utilisation(sol)
    figure_marge_famille(sol)
    figure_demande_hrc(sol)
    figure_plan_marche(sol)
    figure_refus_frontiere(sol)


if __name__ == "__main__":
    from .data_loader import load_data
    from .solve import run
    d = load_data()
    sol = run(d)
    print("KPIs :", json.dumps(kpis(sol), ensure_ascii=False, indent=2))
    print("\nPlan de marche (famille x semaine) :")
    print(plan_de_marche_famille(sol))
    print("\nUtilisation lignes (%) :")
    print(utilisation_table(sol))
    print("\nShadow prices ressources :")
    print(shadow_ressources(sol).to_string(index=False))
    print("\nMarge par famille :")
    print(marge_par_famille(sol).to_string(index=False))
    print("\nCommandes refusées :")
    print(commandes_refusees(sol).to_string(index=False))
    plus, moins = commandes_extremes(sol)
    print("\nB7 plus rentable :", plus, "\nB7 moins rentable :", moins)
