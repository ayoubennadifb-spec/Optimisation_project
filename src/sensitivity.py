"""Analyse de sensibilité : scénarios E20, E21, E22 + bonus B8, B9 + coût galva.

Chaque scénario part d'une copie profonde des données, applique une
modification, re-résout, et compare à la baseline. On renvoie des structures
simples (dicts / DataFrames) réutilisées par le rapport et l'application.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import config
from .data_loader import Data, Order, load_data
from .solve import Solution, run


# --------------------------------------------------------------------------
# Baseline réutilisable
# --------------------------------------------------------------------------
def baseline(data: Data | None = None,
             opt: config.ModelOptions | None = None) -> tuple[Data, Solution]:
    data = data or load_data()
    return data, run(data, opt or config.BASELINE)


# --------------------------------------------------------------------------
# E20 — HRC +10 %
# --------------------------------------------------------------------------
def scenario_hrc(data: Data, mult: float = 1.10,
                 opt: config.ModelOptions | None = None) -> Solution:
    d2 = copy.deepcopy(data)
    d2.prix_hrc = {k: v * mult for k, v in d2.prix_hrc.items()}
    return run(d2, opt or config.BASELINE)


# --------------------------------------------------------------------------
# E21 — panne LGB : +2 jours d'arrêt en semaine 2
# --------------------------------------------------------------------------
def scenario_panne(data: Data, ligne: str = "LGB", semaine: int = 2, jours: float = 2.0,
                   opt: config.ModelOptions | None = None) -> Solution:
    d2 = copy.deepcopy(data)
    d2.arret[(ligne, semaine)] = d2.arret.get((ligne, semaine), 0.0) + jours
    return run(d2, opt or config.BASELINE)


def commandes_qui_basculent(sol0: Solution, sol1: Solution, seuil: float = 1.0):
    rows = []
    for i, o in sol0.data.orders_in_scope():
        a, b = sol0.served.get(i, 0.0), sol1.served.get(i, 0.0)
        if abs(b - a) > seuil:
            rows.append({"ID": o.id, "Famille": o.famille,
                         "Livre_avant": round(a, 0), "Livre_apres": round(b, 0),
                         "Delta": round(b - a, 0)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# E22 — commande urgente entrante
# --------------------------------------------------------------------------
def scenario_commande_urgente(data: Data, order: Order | None = None,
                              opt: config.ModelOptions | None = None
                              ) -> tuple[Solution, int]:
    """Ajoute une commande et re-résout. Renvoie (solution, indice de la commande)."""
    order = order or Order(id="CMD-URG", client="Client_URG", famille="HDG",
                           grade="DC01", ep=0.5, larg=1140, ton=300.0,
                           prix=11500.0, sem=1, prio="Haute")
    d2 = copy.deepcopy(data)
    d2.orders.append(order)
    sol = run(d2, opt or config.BASELINE)
    return sol, len(d2.orders) - 1


# --------------------------------------------------------------------------
# B8 — courbe d'enveloppe : marge vs disponibilité HRC d'un grade
# --------------------------------------------------------------------------
def courbe_enveloppe(data: Data, grade: str = "DC01",
                     deltas=np.linspace(-0.5, 0.5, 21),
                     opt: config.ModelOptions | None = None) -> pd.DataFrame:
    base = data.dispo_hrc[grade]
    rows = []
    for dl in deltas:
        d2 = copy.deepcopy(data)
        d2.dispo_hrc[grade] = base * (1 + dl)
        sol = run(d2, opt or config.BASELINE)
        sp = sol.shadow.get(f"hrc_{grade}", (0.0, 0.0))[0]
        rows.append({"delta_pct": round(100 * dl, 1),
                     "dispo_T": round(d2.dispo_hrc[grade], 0),
                     "marge_MAD": round(sol.margin, 0),
                     "shadow_price": round(sp, 1)})
    return pd.DataFrame(rows)


def figure_enveloppe(df: pd.DataFrame, grade: str = "DC01", path=None):
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(df["dispo_T"], df["marge_MAD"] / 1e6, "o-", color="#00508c")
    ax.set_xlabel(f"Disponibilité HRC {grade} (T)")
    ax.set_ylabel("Marge optimale (MMAD)")
    ax.set_title(f"Courbe d'enveloppe — disponibilité HRC {grade}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = path or (config.FIGURE_DIR / f"enveloppe_{grade}.png")
    config.FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130); plt.close(fig)
    return path


# --------------------------------------------------------------------------
# B9 — robustesse aux cadences (± x %)
# --------------------------------------------------------------------------
def robustesse_cadences(data: Data, facteurs=(0.95, 1.0, 1.05),
                        opt: config.ModelOptions | None = None) -> pd.DataFrame:
    rows = []
    for f in facteurs:
        d2 = copy.deepcopy(data)
        d2.cadence = {k: v * f for k, v in d2.cadence.items()}
        sol = run(d2, opt or config.BASELINE)
        rows.append({"facteur_cadence": f, "statut": sol.status,
                     "marge_MAD": round(sol.margin, 0),
                     "taux_service_pct": round(sol.taux_service(), 1)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Coût de la règle de routage galva HDG (avec / sans)
# --------------------------------------------------------------------------
def cout_regle_galva(data: Data) -> dict:
    opt_avec = config.ModelOptions(galva_rule_hdg=True)
    opt_sans = config.ModelOptions(galva_rule_hdg=False)
    s_avec = run(data, opt_avec)
    s_sans = run(data, opt_sans)
    return {
        "marge_avec_regle": round(s_avec.margin, 0),
        "service_avec_regle": round(s_avec.taux_service(), 1),
        "marge_sans_regle": round(s_sans.margin, 0),
        "service_sans_regle": round(s_sans.taux_service(), 1),
        "cout_regle_MAD": round(s_sans.margin - s_avec.margin, 0),
        "cout_regle_pct": round(100 * (s_sans.margin - s_avec.margin) / s_sans.margin, 2),
    }


# --------------------------------------------------------------------------
# Exécution complète + résumé
# --------------------------------------------------------------------------
def run_all(verbose: bool = True) -> dict:
    data, sol0 = baseline()
    m0 = sol0.margin
    res = {"baseline_marge": m0, "baseline_service": sol0.taux_service()}

    s_hrc = scenario_hrc(data, 1.10)
    res["E20"] = {"marge": s_hrc.margin, "delta": s_hrc.margin - m0,
                  "delta_pct": 100 * (s_hrc.margin - m0) / m0}

    s_panne = scenario_panne(data)
    res["E21"] = {"marge": s_panne.margin, "delta": s_panne.margin - m0,
                  "bascule": commandes_qui_basculent(sol0, s_panne)}

    s_urg, idx = scenario_commande_urgente(data)
    res["E22"] = {"marge": s_urg.margin, "delta": s_urg.margin - m0,
                  "servie": s_urg.served.get(idx, 0.0)}

    res["galva"] = cout_regle_galva(data)
    res["B9"] = robustesse_cadences(data)

    if verbose:
        print(f"BASELINE  marge = {m0:,.0f} MAD  | service {sol0.taux_service():.1f}%")
        print(f"E20 HRC+10%   : {s_hrc.margin:,.0f}  (delta {res['E20']['delta']:,.0f} ; "
              f"{res['E20']['delta_pct']:.1f}%)")
        print(f"E21 LGB+2j S2 : {s_panne.margin:,.0f}  (delta {res['E21']['delta']:,.0f})")
        print("   commandes qui basculent :")
        print(res["E21"]["bascule"].to_string(index=False) if not res["E21"]["bascule"].empty
              else "   (aucune)")
        print(f"E22 urgente   : servie {res['E22']['servie']:.0f}/300 T ; "
              f"marge {s_urg.margin:,.0f} (delta {res['E22']['delta']:,.0f})")
        print(f"Coût règle galva : {res['galva']}")
        print("B9 robustesse cadences :")
        print(res["B9"].to_string(index=False))
    return res


if __name__ == "__main__":
    run_all(verbose=True)
