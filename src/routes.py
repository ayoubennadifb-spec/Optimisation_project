"""Pré-calcul des coefficients économiques et techniques par (commande, route).

Pour 1 tonne de produit FINI fabriquée via une route donnée, on calcule une
fois pour toutes :

    - le facteur amont ``a_p`` à chaque process p = tonnage entrant en p / tonne
      finie = 1 / (produit des rendements de p jusqu'à la fin de la route) ;
    - le facteur HRC ``h`` = tonnage de HRC consommé / tonne finie ;
    - la marge unitaire ``m`` (MAD/T finie) = recette − HRC − transformation
      + valorisation des sous-produits − extras (zinc) ;
    - la charge ``charge_ℓ`` (jours-machine / tonne finie) sur chaque ligne ℓ.

Ces coefficients transforment le problème en un PL linéaire simple où la seule
variable est le tonnage fini ``x[i, route, t]`` (voir ``model.py``).

Tout repose sur la conservation de la matière : tonnage amont = tonnage aval /
rendement (onglet Rendements). C'est la base des contraintes E8/E9.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .data_loader import Data, Order


@dataclass
class RouteCoeff:
    """Coefficients pré-calculés pour une commande sur une route admissible."""

    i: int                       # indice de la commande
    order: Order
    route_name: str
    procs: list[str]             # process de la route (amont -> aval)
    marge_u: float               # marge unitaire (MAD / tonne finie)
    hrc_factor: float            # tonnes de HRC / tonne finie
    charge: dict[str, float]     # ligne -> jours-machine / tonne finie
    input_factor: dict[str, float]   # process -> tonnes entrantes / tonne finie
    # décomposition (pour transparence / tests / rapport)
    recette: float
    cout_hrc: float
    cout_transfo: float
    valorisation: float
    extra_zinc: float

    @property
    def famille(self) -> str:
        return self.order.famille

    @property
    def grade(self) -> str:
        return self.order.grade


def _route_admissible(order: Order, procs: list[str], opt: config.ModelOptions) -> bool:
    """Filtre des routes selon la règle qualité galva HDG (si activée).

    Règle (hypothèse hors documents source, conservée et togglable) :
        HDG, ep <= seuil -> LGA obligatoire ; ep > seuil -> LGB obligatoire.
    """
    if opt.galva_rule_hdg and order.famille == "HDG":
        dernier = procs[-1]
        if order.ep <= opt.galva_seuil_mm and dernier != "LGA":
            return False
        if order.ep > opt.galva_seuil_mm and dernier != "LGB":
            return False
    return True


def _cumul_rendements(procs: list[str], rend: dict) -> tuple[list[float], dict[str, float]]:
    """Rendements cumulés aval R_{r,j} et facteurs amont a_p = 1/R_{r,j}."""
    n = len(procs)
    cumul = [1.0] * n
    acc = 1.0
    for j in range(n - 1, -1, -1):           # de l'aval vers l'amont
        acc *= rend[procs[j]]["r"]
        cumul[j] = acc                       # rendement de procs[j] jusqu'à la fin
    input_factor = {procs[j]: 1.0 / cumul[j] for j in range(n)}
    return cumul, input_factor


def build_route_coeffs(data: Data,
                       opt: config.ModelOptions | None = None
                       ) -> dict[tuple[int, str], RouteCoeff]:
    """Construit le dictionnaire {(i, route_name): RouteCoeff} du périmètre."""
    opt = opt or config.BASELINE
    coeffs: dict[tuple[int, str], RouteCoeff] = {}

    for i, o in data.orders_in_scope():
        bk = config.bracket_index(o.ep)
        for rname, procs in config.ROUTES[o.famille].items():
            if not _route_admissible(o, procs, opt):
                continue

            cumul, a = _cumul_rendements(procs, data.rend)
            hrc_factor = 1.0 / cumul[0]      # = a[procs[0]]

            # coût de transformation (somme sur les process, ramené à la tonne finie)
            cout_transfo = sum(
                data.cout[config.cout_key(p, o.famille)][bk] * a[p] for p in procs
            )
            # valorisation des sous-produits (chutes, déclassé, non-conforme)
            valorisation = sum(
                a[p] * (
                    data.rend[p]["chute"] * data.prix_chute
                    + data.rend[p]["decl"] * data.coef_decl * o.prix
                    + data.rend[p]["nc"] * data.coef_nc * o.prix
                ) for p in procs
            )
            cout_hrc = hrc_factor * data.prix_hrc[(o.grade, o.larg)]

            # extras : zinc pour galvanisation (HDG et PPGI). La peinture du PPGI
            # est déjà incluse dans le coût LGA-PPGI -> pas de double comptage.
            extra_zinc = 0.0
            if o.famille == "HDG":
                extra_zinc = data.cons_zinc_hdg * data.prix_zinc
            elif o.famille == "PPGI":
                extra_zinc = data.cons_zinc_ppgi * data.prix_zinc

            marge_u = o.prix - cout_hrc - cout_transfo + valorisation - extra_zinc

            # charge machine : jours par tonne finie sur chaque ligne de la route
            charge = {p: a[p] / data.cadence[(p, o.famille)] for p in procs}

            coeffs[(i, rname)] = RouteCoeff(
                i=i, order=o, route_name=rname, procs=list(procs),
                marge_u=marge_u, hrc_factor=hrc_factor, charge=charge,
                input_factor=a, recette=o.prix, cout_hrc=cout_hrc,
                cout_transfo=cout_transfo, valorisation=valorisation,
                extra_zinc=extra_zinc,
            )
    return coeffs


def materiau_disponible(data: Data, opt: config.ModelOptions | None = None
                        ) -> dict[str, float]:
    """Matière disponible par grade (en équivalent HRC, tonnes), contrainte E9.

    Base : disponibilité HRC sur l'horizon (onglet Prix_HRC). Si
    ``opt.inclure_stock_pk`` (clarification prof : le stock PK est un semi-produit
    QUALIFIÉ géré par grade), on ajoute le stock initial PK décapé du grade,
    converti en équivalent HRC via le rendement PK. ``stock_pk_net_securite``
    réserve le stock de sécurité minimum.
    """
    opt = opt or config.BASELINE
    dispo = dict(data.dispo_hrc)
    if opt.inclure_stock_pk:
        rho_pk = data.rend["PK"]["r"]
        for g in config.GRADES:
            sk = data.stock_pk.get(g)
            if sk is None:
                continue
            util = sk.initial - sk.mini if opt.stock_pk_net_securite else sk.initial
            dispo[g] = dispo[g] + max(util, 0.0) / rho_pk
    return dispo


def best_route_per_order(coeffs: dict[tuple[int, str], RouteCoeff]
                         ) -> dict[int, RouteCoeff]:
    """Pour chaque commande, la route de meilleure marge unitaire (utile B7)."""
    best: dict[int, RouteCoeff] = {}
    for (i, _), c in coeffs.items():
        if i not in best or c.marge_u > best[i].marge_u:
            best[i] = c
    return best


if __name__ == "__main__":
    from .data_loader import load_data
    d = load_data()
    c = build_route_coeffs(d)
    print(f"Couples (commande, route) générés : {len(c)}")
    # vérification des facteurs HRC sur deux exemples typiques
    for (i, rn), rc in list(c.items())[:1]:
        pass
    # h_r d'une CRC et d'une HDG-LGA
    for key, rc in c.items():
        if rc.famille == "CRC":
            print(f"CRC  {rc.route_name}: h_r={rc.hrc_factor:.4f}  marge_u={rc.marge_u:.0f}")
            break
    for key, rc in c.items():
        if rc.famille == "HDG" and rc.route_name == "CRMB-LGA":
            print(f"HDG  {rc.route_name}: h_r={rc.hrc_factor:.4f}  marge_u={rc.marge_u:.0f}")
            break
