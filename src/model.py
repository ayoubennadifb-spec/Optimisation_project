"""Construction du programme linéaire PuLP (questions E5–E10, + extensions B2/B3/B4).

Variable de décision :
    x[i, r, t] >= 0 : tonnage de produit FINI de la commande i fabriqué via la
    route r en semaine t (t <= échéance, ou toutes semaines si retards autorisés).

Objectif : maximiser la marge sur coût variable (E6).

Contraintes :
    (1) Demande         : Σ_{r,t} x[i,r,t] <= D_i                         (E8)
    (2) Capacité ligne  : Σ charge_ℓ · x <= 7 − arrêt(ℓ,t)   ∀ ℓ, t       (E7)
    (3) Disponibilité HRC : Σ h_r · x <= H_g                ∀ g           (E9)
    (4) Bilan stock PF  : I[f,t] = I[f,t-1] + prod − livraisons, bornes min/max (E8)

Extensions activables (voir ModelOptions) :
    - B2 : livraisons en retard pénalisées (objectif).
    - B3 : coûts de stockage des produits finis (objectif).
    - B4 : campagnes (binaires z + tonnage minimum) -> le modèle devient un MILP.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from . import config
from .data_loader import Data
from .routes import RouteCoeff, build_route_coeffs, materiau_disponible


@dataclass
class BuiltModel:
    """Modèle PuLP construit + références aux variables et métadonnées."""

    prob: pulp.LpProblem
    x: dict[tuple[int, str, int], pulp.LpVariable]
    inv: dict[tuple[str, int], pulp.LpVariable]          # stock PF (si activé)
    z: dict[tuple[str, str, int], pulp.LpVariable]       # binaires campagne (si activé)
    accept: dict[int, pulp.LpVariable]                   # binaires accept/reject (mode nb_commandes)
    coeffs: dict[tuple[int, str], RouteCoeff]
    data: Data
    opt: config.ModelOptions
    served: dict[int, pulp.LpAffineExpression]           # tonnage total servi / commande
    is_mip: bool


def build_model(data: Data,
                coeffs: dict[tuple[int, str], RouteCoeff] | None = None,
                opt: config.ModelOptions | None = None) -> BuiltModel:
    """Construit et renvoie le :class:`BuiltModel` (non encore résolu)."""
    opt = opt or config.BASELINE
    coeffs = coeffs if coeffs is not None else build_route_coeffs(data, opt)
    prob = pulp.LpProblem("MaghrebSteel_CapaciteCommande", pulp.LpMaximize)

    # semaines de production autorisées pour une commande
    def weeks_for(due: int) -> list[int]:
        if opt.retards_autorises:
            return list(config.WEEKS)
        return [t for t in config.WEEKS if t <= due]

    # ----- Variables x[i, r, t] -----
    x: dict[tuple[int, str, int], pulp.LpVariable] = {}
    for (i, rname), rc in coeffs.items():
        for t in weeks_for(rc.order.sem):
            x[(i, rname, t)] = pulp.LpVariable(f"x_{i}_{rname}_{t}", lowBound=0)

    # tonnage total servi par commande (expression réutilisée)
    served: dict[int, pulp.LpAffineExpression] = {}
    for i, _o in data.orders_in_scope():
        terms = [v for (ii, rn, t), v in x.items() if ii == i]
        served[i] = pulp.lpSum(terms) if terms else pulp.lpSum([])

    # binaires accept/reject (mode "nb_commandes") : accept[i] = 1 si la commande
    # est honorée intégralement (clarification déléguée/prof : maximiser le NOMBRE
    # de commandes). Sinon, mode marge classique (acceptation partielle autorisée).
    mode_nb = (opt.objectif == "nb_commandes")
    accept: dict[int, pulp.LpVariable] = {}
    if mode_nb:
        for i, _o in data.orders_in_scope():
            if any(ii == i for (ii, _, _) in x):
                accept[i] = pulp.LpVariable(f"accept_{i}", cat="Binary")

    # ----- Expression de marge (sert d'objectif ou de départage) -----
    objectif = pulp.lpSum(coeffs[(i, rn)].marge_u * v for (i, rn, t), v in x.items())

    # B2 : pénalité de retard (proportionnelle au retard et à la priorité)
    if opt.retards_autorises:
        pen = data.penalite_retard
        objectif -= pulp.lpSum(
            pen.get(coeffs[(i, rn)].order.prio, 0.0)
            * max(0, t - coeffs[(i, rn)].order.sem) * v
            for (i, rn, t), v in x.items()
        )

    # ----- Stock produits finis (E8) + B3 -----
    inv: dict[tuple[str, int], pulp.LpVariable] = {}
    if opt.stocks_pf:
        for fam in config.FAMILIES:
            sk = data.stock_pf[fam]
            low = sk.mini if opt.enforce_stock_min else 0.0
            for t in config.WEEKS:
                inv[(fam, t)] = pulp.LpVariable(
                    f"inv_{fam.replace(' ', '')}_{t}", lowBound=low, upBound=sk.maxi)
        # bilan de stock : I[t] = I[t-1] + production(t) − livraisons(t)
        for fam in config.FAMILIES:
            sk = data.stock_pf[fam]
            idx_fam = [i for i, o in data.orders_in_scope() if o.famille == fam]
            for t in config.WEEKS:
                prod_t = pulp.lpSum(v for (i, rn, tt), v in x.items()
                                    if tt == t and data.orders[i].famille == fam)
                # livraisons de la famille en semaine t = commandes échéant en t
                livr_t = pulp.lpSum(served[i] for i in idx_fam
                                    if data.orders[i].sem == t)
                prev = data.stock_pf[fam].initial if t == 1 else inv[(fam, t - 1)]
                prob += (inv[(fam, t)] == prev + prod_t - livr_t,
                         f"stock_{fam.replace(' ', '')}_{t}")
        if opt.cout_stockage:
            objectif -= pulp.lpSum(data.cout_stock_pf * inv[(fam, t)]
                                   for fam in config.FAMILIES for t in config.WEEKS)

    if mode_nb:
        # Maximiser le NOMBRE de commandes honorées ; la marge (échelle epsilon)
        # ne sert que de fin départage et ne peut jamais changer le nombre, car
        # eps * marge_totale_possible < 1.
        eps = 1.0e-9
        prob += pulp.lpSum(accept.values()) + eps * objectif, "nb_commandes"
    else:
        prob += objectif, "marge_totale"

    # ----- (1) Demande -----
    #   mode marge      : servi <= demandé (acceptation partielle possible)
    #   mode nb_commandes : servi == demandé * accept (accept/reject intégral)
    for i, o in data.orders_in_scope():
        if (i in served) and any(ii == i for (ii, _, _) in x):
            if mode_nb:
                prob += (served[i] == o.ton * accept[i], f"demande_{i}")
            else:
                prob += (served[i] <= o.ton, f"demande_{i}")

    # ----- (2) Capacité ligne × semaine (en jours) -----
    for L in config.LINES:
        for t in config.WEEKS:
            terms = [coeffs[(i, rn)].charge[L] * v
                     for (i, rn, tt), v in x.items()
                     if tt == t and L in coeffs[(i, rn)].charge]
            if terms:
                dispo_jours = config.JOURS_PAR_SEMAINE - data.arret.get((L, t), 0.0)
                prob += (pulp.lpSum(terms) <= dispo_jours, f"cap_{L}_{t}")

    # ----- (3) Disponibilité matière par grade (HRC + éventuel stock PK qualifié) -----
    dispo_mat = materiau_disponible(data, opt)
    for g in config.GRADES:
        terms = [coeffs[(i, rn)].hrc_factor * v
                 for (i, rn, t), v in x.items() if coeffs[(i, rn)].grade == g]
        if terms:
            prob += (pulp.lpSum(terms) <= dispo_mat[g], f"hrc_{g}")

    # ----- B4 : campagnes (binaires) -> MILP -----
    z: dict[tuple[str, str, int], pulp.LpVariable] = {}
    is_mip = mode_nb            # le mode nb_commandes est déjà un MILP (binaires accept)
    if opt.campagnes:
        is_mip = True
        # grosse borne : tonnage max plausible d'une (ligne, famille, semaine)
        BIG_M = sum(o.ton for o in data.orders) + 1.0
        # une "campagne" = (ligne galva/finition, famille, semaine).
        # On l'applique aux lignes finales caractéristiques de chaque famille.
        ligne_famille = {"CRC": "SKP", "HDG": None, "PPGI": "LGA",
                         "BACR": None, "HRC DEC": "PK"}
        for fam, Lf in ligne_famille.items():
            if Lf is None:
                continue
            for t in config.WEEKS:
                z[(Lf, fam, t)] = pulp.LpVariable(
                    f"z_{Lf}_{fam}_{t}", cat="Binary")
                prod_ft = pulp.lpSum(
                    v for (i, rn, tt), v in x.items()
                    if tt == t and data.orders[i].famille == fam)
                # pas de production sans campagne ouverte ; et si ouverte, >= Q_min
                prob += (prod_ft <= BIG_M * z[(Lf, fam, t)],
                         f"camp_max_{Lf}_{fam}_{t}")
                prob += (prod_ft >= opt.q_min_campagne * z[(Lf, fam, t)],
                         f"camp_min_{Lf}_{fam}_{t}")

    return BuiltModel(prob=prob, x=x, inv=inv, z=z, accept=accept, coeffs=coeffs,
                      data=data, opt=opt, served=served, is_mip=is_mip)
