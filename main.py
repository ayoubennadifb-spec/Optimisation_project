"""main.py — orchestrateur du Simulateur Capacité–Commande (Maghreb Steel).

Pipeline complet, conforme à la question E12 :
    1. lecture des données (``Donnees_MaghrebSteel.xlsx``) ;
    2. construction du programme linéaire (PuLP) ;
    3. résolution (CBC) ;
    4. validation a posteriori INDÉPENDANTE du solveur (E15) ;
    5. export des résultats lisibles (Excel / CSV / JSON + figures) dans ``outputs/``.

Exemples d'utilisation
----------------------
    python main.py                 # baseline : résout, valide, exporte, affiche le récap
    python main.py --sensibilite   # + scénarios E20/E21/E22, coût règle galva, B9
    python main.py --campagnes     # variante MILP (bonus B4) + stats Branch&Bound (B5)
    python main.py --sans-galva    # désactive la règle galva HDG (analyse "sans règle")
    python main.py --objectif nb_commandes   # maximise le NOMBRE de commandes (MILP)
    python main.py --sans-stock-pk # matière = dispo HRC seule (sans le stock PK qualifié)
    python main.py --no-export     # n'écrit pas dans outputs/
"""
from __future__ import annotations

import argparse
import json
import sys

# Sortie console robuste (Windows : évite les plantages cp1252 sur caractères Unicode).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src import config, reporting
from src.data_loader import load_data
from src.model import build_model
from src.solve import solve_model
from src.validation import validate


def _print_section(titre: str) -> None:
    print("\n" + "=" * 78 + f"\n{titre}\n" + "=" * 78)


def run_baseline(opt: config.ModelOptions, export: bool = True) -> None:
    """Exécute le pipeline complet pour un jeu d'options donné."""
    _print_section("1-3. LECTURE → CONSTRUCTION → RÉSOLUTION")
    data = load_data()
    print(f"Commandes lues : {len(data.orders)}  "
          f"(périmètre modélisé : {len(data.orders_in_scope())} ; Quarto exclu)")
    bm = build_model(data, opt=opt)
    print(f"Variables x[i,r,t] : {len(bm.x)}   |   MILP : {bm.is_mip}")
    sol = solve_model(bm)

    _print_section("E13. SOLUTION OPTIMALE — INDICATEURS")
    print(json.dumps(reporting.kpis(sol), ensure_ascii=False, indent=2))

    _print_section("E14. PLAN DE MARCHE — tonnage fini par famille × semaine")
    print(reporting.plan_de_marche_famille(sol).to_string())

    _print_section("E16. TAUX D'UTILISATION DES LIGNES (%)")
    print(reporting.utilisation_table(sol).to_string())
    goulots = reporting.goulots(sol)
    print("\nGoulots (≥99 %) :",
          ", ".join(f"{L} S{t} ({u:.0f}%)" for L, t, u in goulots) or "aucun")

    if not sol.is_mip:
        _print_section("E18. SHADOW PRICES DES RESSOURCES SATURÉES")
        print(reporting.shadow_ressources(sol).to_string(index=False))

        _print_section("B1. PRIX PLANCHER D'ACCEPTATION (exemples, semaine 1)")
        for fam, gr, ep, lg in [("HDG", "DX51", 0.3, 1250), ("HDG", "DC01", 1.0, 1250),
                                ("PPGI", "DX51", 0.4, 1250), ("CRC", "S320", 0.4, 1100),
                                ("HRC DEC", "DC01", 3.0, 1320)]:
            r = reporting.prix_plancher(sol, fam, gr, ep, lg, 1)
            if r:
                print(f"  {fam:8s} {gr:5s} {ep:>4}mm  plancher={r['prix_plancher']:>8.0f} MAD/T "
                      f"(revient {r['cout_revient']:.0f} + opportunité {r['cout_opportunite']:.0f}) "
                      f"via {r['meilleure_route']}")

    _print_section("E19. MARGE PAR FAMILLE")
    print(reporting.marge_par_famille(sol).to_string(index=False))
    plus, moins = reporting.commandes_extremes(sol)
    print(f"\nB7 — commande la PLUS rentable honorée  : {plus}")
    print(f"B7 — commande la MOINS rentable honorée : {moins}")

    _print_section("E17. COMMANDES REFUSÉES + CONTRAINTE BLOQUANTE")
    refus = reporting.commandes_refusees(sol)
    print(refus.to_string(index=False) if not refus.empty else "Aucune (tout honoré).")

    _print_section("E15. VALIDATION INDÉPENDANTE DU SOLVEUR")
    rapport = validate(data, sol.x, opt)
    print(rapport)
    if not rapport.all_ok:
        print("\n!!! ATTENTION : la solution VIOLE au moins une contrainte recalculée.")

    if export and not sol.is_mip:
        reporting.export_all(sol)
        _print_section("EXPORT")
        print(f"Résultats exportés dans : {config.OUTPUT_DIR}")


def run_sensibilite() -> None:
    from src import sensitivity
    _print_section("ANALYSE DE SENSIBILITÉ (E20/E21/E22 + coût galva + B9)")
    sensitivity.run_all(verbose=True)


def run_campagnes_bb() -> None:
    from src import extensions
    _print_section("B4/B5 — VARIANTE CAMPAGNES (MILP) + STATS BRANCH-AND-BOUND")
    extensions.print_bb_report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulateur Capacité–Commande Maghreb Steel")
    parser.add_argument("--sensibilite", action="store_true",
                        help="lance aussi les scénarios de sensibilité")
    parser.add_argument("--campagnes", action="store_true",
                        help="variante MILP avec campagnes (B4) + stats B&B (B5)")
    parser.add_argument("--sans-galva", dest="sans_galva", action="store_true",
                        help="désactive la règle galva HDG (analyse sans règle)")
    parser.add_argument("--objectif", choices=["marge", "nb_commandes"], default="marge",
                        help="objectif : marge (O1, défaut) ou nb de commandes honorées (MILP)")
    parser.add_argument("--sans-stock-pk", dest="sans_stock_pk", action="store_true",
                        help="n'inclut pas le stock PK qualifié dans la matière disponible")
    parser.add_argument("--no-export", dest="export", action="store_false",
                        help="n'exporte pas les fichiers de résultats")
    args = parser.parse_args(argv)

    opt = config.ModelOptions(
        galva_rule_hdg=not args.sans_galva,
        campagnes=args.campagnes,
        objectif=args.objectif,
        inclure_stock_pk=not args.sans_stock_pk,
    )
    run_baseline(opt, export=args.export)
    if args.sensibilite:
        run_sensibilite()
    if args.campagnes:
        run_campagnes_bb()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
