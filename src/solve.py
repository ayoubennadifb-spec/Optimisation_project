"""Résolution CBC et extraction d'une solution exploitable.

On résout le :class:`BuiltModel` avec CBC puis on en extrait une structure
:class:`Solution` contenant le tonnage produit, le servi par commande, les
stocks, l'utilisation des lignes, les valeurs duales (shadow prices) et les
coûts réduits — tout ce dont ont besoin ``reporting`` et ``validation``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from . import config
from .model import BuiltModel, build_model
from .data_loader import Data


@dataclass
class Solution:
    """Résultat résolu et extrait d'un modèle."""

    status: str
    margin: float                                  # marge sur coût variable RÉELLE (Σ marge_u·x)
    objective_value: float                         # valeur de l'objectif résolu (= marge, ou nb commandes)
    x: dict[tuple[int, str, int], float]          # production (valeurs > 0 seulement)
    served: dict[int, float]                       # tonnage servi par commande
    inv: dict[tuple[str, int], float]              # stock PF
    utilisation: dict[tuple[str, int], tuple[float, float]]   # (jours utilisés, dispo)
    shadow: dict[str, tuple[float, float]]         # contrainte -> (pi, slack)
    reduced_cost: dict[tuple[int, str, int], float]
    bm: BuiltModel
    is_mip: bool

    # ---- indicateurs agrégés ----
    @property
    def data(self) -> Data:
        return self.bm.data

    def total_demande_scope(self) -> float:
        return sum(o.ton for _i, o in self.data.orders_in_scope())

    def total_livre(self) -> float:
        return sum(self.served.values())

    def taux_service(self) -> float:
        d = self.total_demande_scope()
        return 100.0 * self.total_livre() / d if d else 0.0


def solve_model(bm: BuiltModel, msg: bool = False,
                time_limit: int | None = None) -> Solution:
    """Résout ``bm`` avec CBC et renvoie la :class:`Solution` extraite."""
    solver = pulp.PULP_CBC_CMD(msg=1 if msg else 0, timeLimit=time_limit)
    bm.prob.solve(solver)
    status = pulp.LpStatus[bm.prob.status]
    objective_value = pulp.value(bm.prob.objective) or 0.0

    # production
    xval = {k: (v.value() or 0.0) for k, v in bm.x.items()}
    xval = {k: val for k, val in xval.items() if val > 1e-9}

    # marge sur coût variable RÉELLE (recalculée depuis x : valable quel que soit
    # l'objectif — en mode "nb_commandes" l'objectif n'est PAS la marge).
    margin = sum(bm.coeffs[(i, rn)].marge_u * val for (i, rn, t), val in xval.items())

    # servi par commande
    served = {i: 0.0 for i, _o in bm.data.orders_in_scope()}
    for (i, _rn, _t), val in xval.items():
        served[i] += val

    # stocks
    invval = {k: (v.value() or 0.0) for k, v in bm.inv.items()}

    # utilisation des lignes (jours utilisés / disponibles)
    utilisation: dict[tuple[str, int], tuple[float, float]] = {}
    for L in config.LINES:
        for t in config.WEEKS:
            used = sum(bm.coeffs[(i, rn)].charge.get(L, 0.0) * val
                       for (i, rn, tt), val in xval.items() if tt == t)
            avail = config.JOURS_PAR_SEMAINE - bm.data.arret.get((L, t), 0.0)
            utilisation[(L, t)] = (used, avail)

    # valeurs duales et coûts réduits (LP uniquement — vides si MILP)
    shadow: dict[str, tuple[float, float]] = {}
    reduced: dict[tuple[int, str, int], float] = {}
    if not bm.is_mip and status == "Optimal":
        for name, c in bm.prob.constraints.items():
            pi = c.pi
            slack = c.slack
            if pi is not None:
                shadow[name] = (float(pi), float(slack) if slack is not None else 0.0)
        for k, v in bm.x.items():
            dj = getattr(v, "dj", None)
            if dj is not None:
                reduced[k] = float(dj)

    return Solution(status=status, margin=margin, objective_value=objective_value,
                    x=xval, served=served,
                    inv=invval, utilisation=utilisation, shadow=shadow,
                    reduced_cost=reduced, bm=bm, is_mip=bm.is_mip)


def run(data: Data, opt: config.ModelOptions | None = None,
        msg: bool = False) -> Solution:
    """Construit + résout en une fois (raccourci)."""
    bm = build_model(data, opt=opt)
    return solve_model(bm, msg=msg)


if __name__ == "__main__":
    from .data_loader import load_data
    d = load_data()
    sol = run(d)
    print(f"Statut         : {sol.status}")
    print(f"Marge totale   : {sol.margin:,.0f} MAD")
    print(f"Tonnage livré  : {sol.total_livre():,.0f} T / {sol.total_demande_scope():,.0f} T")
    print(f"Taux de service: {sol.taux_service():.1f} %")
    sp = sorted(((n, pi, sl) for n, (pi, sl) in sol.shadow.items() if abs(pi) > 1e-6),
                key=lambda r: -abs(r[1]))
    print("Top shadow prices :")
    for n, pi, sl in sp[:8]:
        print(f"   {n:16s} pi={pi:12.1f}  slack={sl:8.3f}")
