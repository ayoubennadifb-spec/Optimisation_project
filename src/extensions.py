"""Bonus B4/B5 — campagnes (MILP) et statistiques de Branch-and-Bound.

Les *campagnes* (B4) sont déjà modélisées dans ``model.py`` via les variables
binaires ``z[ligne, famille, semaine]`` et un tonnage minimum de campagne
(option ``ModelOptions.campagnes``). Ce module ajoute la **lecture de
l'arbre de Branch-and-Bound** demandée en B5 :

    - on résout le MILP avec CBC en capturant son journal (log) ;
    - on en extrait le nombre de nœuds explorés et le temps de résolution ;
    - on le compare à la **relaxation linéaire pure** (binaires relâchés dans
      [0,1]) : borne supérieure, temps, et écart d'intégralité (gap).

Tout est volontairement défensif : si CBC ne reporte pas le nombre de nœuds
(MILP trivial résolu à la racine), on le signale proprement.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass

import pulp

from . import config
from .data_loader import Data, load_data
from .model import build_model


@dataclass
class BBStats:
    milp_obj: float
    lp_relax_obj: float
    baseline_obj: float          # LP sans campagnes (référence)
    gap_abs: float               # borne LP − solution MILP (>= 0 en max)
    gap_pct: float
    milp_time_s: float
    lp_time_s: float
    nodes: int | None
    status: str
    n_binaires: int


def _solve_capture(prob: pulp.LpProblem, time_limit: int | None = None
                   ) -> tuple[float, int | None, str]:
    """Résout ``prob`` avec CBC en capturant le log ; renvoie (temps, nœuds, log)."""
    tmp = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False, encoding="utf-8")
    tmp.close()
    solver = pulp.PULP_CBC_CMD(msg=0, logPath=tmp.name, timeLimit=time_limit)
    t0 = time.perf_counter()
    prob.solve(solver)
    dt = time.perf_counter() - t0
    try:
        log = open(tmp.name, encoding="utf-8", errors="ignore").read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    # CBC écrit p.ex. "... took N iterations and M nodes" ou "Enumerated nodes: M".
    nodes = None
    matches = re.findall(r"(\d+)\s+nodes", log)
    if matches:
        nodes = int(matches[-1])
    else:
        m = re.search(r"Enumerated nodes:\s*(\d+)", log)
        if m:
            nodes = int(m.group(1))
    return dt, nodes, log


def bb_stats(data: Data | None = None,
             opt: config.ModelOptions | None = None) -> BBStats:
    """Calcule les statistiques B&B du MILP campagnes vs sa relaxation LP."""
    data = data or load_data()
    opt = opt or config.ModelOptions(campagnes=True)
    if not opt.campagnes:
        opt = config.ModelOptions(campagnes=True)

    # --- MILP (binaires entières) ---
    bm = build_model(data, opt=opt)
    assert bm.is_mip and bm.z, "Le modèle n'est pas un MILP (campagnes désactivées ?)"
    milp_time, nodes, _ = _solve_capture(bm.prob)
    milp_obj = float(pulp.value(bm.prob.objective) or 0.0)
    status = pulp.LpStatus[bm.prob.status]
    n_bin = len(bm.z)

    # --- Relaxation linéaire pure (binaires relâchés dans [0,1]) ---
    bm_lp = build_model(data, opt=opt)
    for v in bm_lp.z.values():
        v.cat = pulp.LpContinuous
        v.lowBound, v.upBound = 0.0, 1.0
    lp_time, _, _ = _solve_capture(bm_lp.prob)
    lp_obj = float(pulp.value(bm_lp.prob.objective) or 0.0)

    # --- Référence : LP sans campagnes ---
    bm_base = build_model(data, opt=config.ModelOptions(campagnes=False,
                                                        galva_rule_hdg=opt.galva_rule_hdg))
    bm_base.prob.solve(pulp.PULP_CBC_CMD(msg=0))
    base_obj = float(pulp.value(bm_base.prob.objective) or 0.0)

    gap_abs = lp_obj - milp_obj
    gap_pct = 100.0 * gap_abs / lp_obj if lp_obj else 0.0
    return BBStats(milp_obj=milp_obj, lp_relax_obj=lp_obj, baseline_obj=base_obj,
                   gap_abs=gap_abs, gap_pct=gap_pct, milp_time_s=milp_time,
                   lp_time_s=lp_time, nodes=nodes, status=status, n_binaires=n_bin)


def bb_sweep(q_values=(100.0, 400.0, 800.0, 1200.0),
             data: Data | None = None) -> list[dict]:
    """Balaye le tonnage minimum de campagne pour illustrer la transition LP→MILP.

    En dessous d'un seuil, les campagnes ne contraignent pas (relaxation LP déjà
    entière, 0 nœud) ; au-delà, elles deviennent actives (gap > 0, branchement).
    """
    data = data or load_data()
    rows = []
    for q in q_values:
        s = bb_stats(data, config.ModelOptions(campagnes=True, q_min_campagne=q))
        rows.append({"Q_min_T": q, "MILP_MAD": round(s.milp_obj, 0),
                     "LP_relax_MAD": round(s.lp_relax_obj, 0),
                     "gap_pct": round(s.gap_pct, 3), "noeuds": s.nodes,
                     "temps_ms": round(s.milp_time_s * 1000, 0)})
    return rows


def print_bb_report(stats: BBStats | None = None) -> BBStats:
    """Affiche un récapitulatif lisible des statistiques B&B (B5)."""
    s = stats or bb_stats()
    print(f"Statut MILP                         : {s.status}")
    print(f"Variables binaires (campagnes)      : {s.n_binaires}")
    print(f"Objectif MILP (avec campagnes)      : {s.milp_obj:,.0f} MAD")
    print(f"Relaxation LP (borne supérieure)    : {s.lp_relax_obj:,.0f} MAD")
    print(f"Écart d'intégralité (gap)           : {s.gap_abs:,.0f} MAD "
          f"({s.gap_pct:.3f} %)")
    print(f"LP de référence (sans campagnes)    : {s.baseline_obj:,.0f} MAD")
    print(f"Coût des campagnes (vs LP libre)    : {s.baseline_obj - s.milp_obj:,.0f} MAD")
    noeuds = s.nodes if s.nodes is not None else "non reporté (résolu à la racine)"
    print(f"Nœuds de Branch-and-Bound explorés  : {noeuds}")
    print(f"Temps de résolution MILP            : {s.milp_time_s*1000:.0f} ms")
    print(f"Temps de résolution relaxation LP   : {s.lp_time_s*1000:.0f} ms")

    print("\nBalayage du tonnage minimum de campagne (transition LP → MILP) :")
    print(f"  {'Q_min(T)':>9} {'MILP(MAD)':>14} {'LP relax(MAD)':>14} "
          f"{'gap%':>7} {'nœuds':>6} {'t(ms)':>7}")
    for r in bb_sweep():
        print(f"  {r['Q_min_T']:>9.0f} {r['MILP_MAD']:>14,.0f} {r['LP_relax_MAD']:>14,.0f} "
              f"{r['gap_pct']:>7.3f} {str(r['noeuds']):>6} {r['temps_ms']:>7.0f}")
    return s


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print_bb_report()
