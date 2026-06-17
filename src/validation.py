"""Validation a posteriori INDÉPENDANTE du solveur (question E15).

À partir des seules valeurs de production ``x[i,r,t]`` renvoyées, on recalcule
et on revérifie *toutes* les contraintes du problème, sans interroger le
solveur ni faire confiance à son statut. Les coefficients techniques sont
recalculés à partir des données brutes (rendements, cadences) — ce script
ne dépend que de l'Excel et de la solution.

Sortie : un rapport de contrôle (liste de vérifications, violation maximale)
avec une tolérance numérique ``TOL`` cohérente avec la tolérance de faisabilité
du solveur CBC (1e-4 T, soit 0,1 kg — négligeable industriellement). Une
violation inférieure à TOL est un simple arrondi du solveur, pas une infaisabilité.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .data_loader import Data
from .routes import build_route_coeffs, materiau_disponible

# Tolérance de validation : >= tolérance de faisabilité de CBC. Les contraintes
# tout juste saturantes peuvent être dépassées de ~1e-5 T par arrondi du solveur.
TOL = 1e-4


@dataclass
class Check:
    nom: str
    ok: bool
    violation_max: float
    detail: str = ""


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def __str__(self) -> str:
        lignes = [f"=== VALIDATION INDÉPENDANTE (tol={TOL:.0e}) ==="]
        for c in self.checks:
            tag = "OK  " if c.ok else "ÉCHEC"
            lignes.append(f"[{tag}] {c.nom:42s} viol_max={c.violation_max:.2e}"
                          + (f"  {c.detail}" if c.detail else ""))
        lignes.append("RÉSULTAT GLOBAL : " + ("TOUT OK" if self.all_ok else "ÉCHECS DÉTECTÉS"))
        return "\n".join(lignes)


def validate(data: Data,
             x: dict[tuple[int, str, int], float],
             opt: config.ModelOptions | None = None) -> ValidationReport:
    """Revérifie toutes les contraintes à partir de la solution ``x``."""
    opt = opt or config.BASELINE
    coeffs = build_route_coeffs(data, opt)        # recalcul indépendant
    rep = ValidationReport()

    # 0) non-négativité
    vmax = max((-v for v in x.values() if v < 0), default=0.0)
    rep.checks.append(Check("Non-négativité des x", vmax <= TOL, vmax))

    # 0bis) production seulement sur routes admissibles + semaines autorisées
    bad_route = 0.0
    bad_week = ""
    for (i, rn, t), v in x.items():
        if (i, rn) not in coeffs:
            bad_route = max(bad_route, v)
        due = data.orders[i].sem
        if not opt.retards_autorises and t > due:
            bad_week = f"cmd {data.orders[i].id} t={t}>échéance {due}"
    rep.checks.append(Check("Routes admissibles utilisées", bad_route <= TOL, bad_route))
    rep.checks.append(Check("Production dans semaines autorisées", bad_week == "", 0.0, bad_week))

    # 1) demande : servi <= demandé
    served: dict[int, float] = {}
    for (i, rn, t), v in x.items():
        served[i] = served.get(i, 0.0) + v
    vmax = 0.0
    worst = ""
    for i, o in data.orders_in_scope():
        s = served.get(i, 0.0)
        viol = s - o.ton
        if viol > vmax:
            vmax, worst = viol, o.id
    rep.checks.append(Check("Demande (servi <= demandé)", vmax <= TOL, vmax, worst))

    # 2) capacité ligne × semaine
    vmax = 0.0
    worst = ""
    for L in config.LINES:
        for t in config.WEEKS:
            used = sum(coeffs[(i, rn)].charge.get(L, 0.0) * v
                       for (i, rn, tt), v in x.items() if tt == t)
            avail = config.JOURS_PAR_SEMAINE - data.arret.get((L, t), 0.0)
            viol = used - avail
            if viol > vmax:
                vmax, worst = viol, f"{L} S{t} ({used:.3f}>{avail:.3f} j)"
    rep.checks.append(Check("Capacité lignes (jours)", vmax <= TOL, vmax, worst))

    # 3) disponibilité matière par grade (HRC + éventuel stock PK qualifié)
    dispo_mat = materiau_disponible(data, opt)
    vmax = 0.0
    worst = ""
    for g in config.GRADES:
        conso = sum(coeffs[(i, rn)].hrc_factor * v
                    for (i, rn, t), v in x.items() if coeffs[(i, rn)].grade == g)
        viol = conso - dispo_mat[g]
        if viol > vmax:
            vmax, worst = viol, f"{g} ({conso:.1f}>{dispo_mat[g]:.0f} T)"
    rep.checks.append(Check("Disponibilité matière par grade", vmax <= TOL, vmax, worst))

    # 4) bilans de stock produits finis + bornes
    if opt.stocks_pf:
        vmax = 0.0
        worst = ""
        for fam in config.FAMILIES:
            sk = data.stock_pf[fam]
            inv_prev = sk.initial
            for t in config.WEEKS:
                prod_t = sum(v for (i, rn, tt), v in x.items()
                             if tt == t and data.orders[i].famille == fam)
                livr_t = sum(served.get(i, 0.0) for i, o in data.orders_in_scope()
                             if o.famille == fam and o.sem == t)
                inv_t = inv_prev + prod_t - livr_t
                lo = sk.mini if opt.enforce_stock_min else 0.0
                viol = max(lo - inv_t, inv_t - sk.maxi, 0.0)
                if viol > vmax:
                    vmax, worst = viol, f"{fam} S{t} inv={inv_t:.1f} [{lo:.0f},{sk.maxi:.0f}]"
                inv_prev = inv_t
        rep.checks.append(Check("Stock PF (bilan + bornes min/max)", vmax <= TOL, vmax, worst))

    # 5) règle de routage galva HDG (si active)
    if opt.galva_rule_hdg:
        bad = 0.0
        worst = ""
        for (i, rn, t), v in x.items():
            o = data.orders[i]
            if o.famille != "HDG":
                continue
            dernier = coeffs[(i, rn)].procs[-1]
            if o.ep <= opt.galva_seuil_mm and dernier != "LGA":
                bad = max(bad, v); worst = f"{o.id} ep={o.ep}->{dernier}"
            if o.ep > opt.galva_seuil_mm and dernier != "LGB":
                bad = max(bad, v); worst = f"{o.id} ep={o.ep}->{dernier}"
        rep.checks.append(Check("Règle galva HDG (LGA/LGB selon ep)", bad <= TOL, bad, worst))

    return rep


if __name__ == "__main__":
    from .data_loader import load_data
    from .solve import run
    d = load_data()
    sol = run(d)
    rep = validate(d, sol.x, sol.bm.opt)
    print(rep)
