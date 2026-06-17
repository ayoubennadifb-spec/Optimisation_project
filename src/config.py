"""Constantes structurelles, routes métallurgiques et options du modèle.

Tout ce qui est *structurel* (lignes, grades, familles, routes, tranches
d'épaisseur) ou *choix de modélisation* (toggles) vit ici. Les valeurs
*numériques métier* (prix, cadences, rendements, arrêts...) sont lues depuis
le fichier Excel par ``data_loader`` — on ne duplique pas les données.

Références source of truth :
    - Note de cadrage §2.2 (schéma de flux) et §4.4 (spécialisation laminoirs)
    - Onglets README / Cadences du fichier Donnees_MaghrebSteel.xlsx
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Chemins
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "Donnees_MaghrebSteel.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
FIGURE_DIR = OUTPUT_DIR / "figures"

# --------------------------------------------------------------------------
# Ensembles structurels (Note de cadrage §2.2, README de l'Excel)
# --------------------------------------------------------------------------
WEEKS = [1, 2, 3, 4]                       # horizon : 4 semaines
JOURS_PAR_SEMAINE = 7                      # usine 7j/7 (§4.5) — capacité = 7 - arrêts

LINES = ["PK", "CRMA", "CRMB", "BAF", "SKP", "LGA", "LGB"]
GRADES = ["DC01", "DD13", "DX51", "DX52", "S320"]

# Familles traitées par le modèle (Quarto exclu : filière hors laminage à froid).
FAMILIES = ["CRC", "HDG", "PPGI", "BACR", "HRC DEC"]
FAMILIES_EXCLUES = ["Quarto"]

# Tranches d'épaisseur des coûts variables (onglet Couts_Variables).
# Convention : bornes inférieures incluses -> ex. 0.3 mm appartient à "0.3-0.4".
BRACKET_LABELS = ["<0.3", "0.3-0.4", "0.4-0.5", "0.5-0.7", "0.7-1.0", "1.0-1.5", ">1.5"]
BRACKET_BOUNDS = [0.3, 0.4, 0.5, 0.7, 1.0, 1.5]


def bracket_index(ep: float) -> int:
    """Indice 0..6 de la tranche d'épaisseur d'une épaisseur ``ep`` (mm).

    Bornes inférieures incluses : 0.3 -> tranche 1 ("0.3-0.4"), 0.2 -> 0 ("<0.3").
    """
    i = 0
    for b in BRACKET_BOUNDS:
        if ep >= b:
            i += 1
        else:
            break
    return i


# --------------------------------------------------------------------------
# Routes métallurgiques admissibles par famille (Note de cadrage §2.2 / §4.4)
#   Chaque route = suite ORDONNÉE de process, de l'amont (après HRC) vers l'aval.
#   Règles intégrées :
#     - CRC : chemin unique via CRMB -> BAF -> SKP (CRMA interdit).
#     - HDG : CRMA ou CRMB en amont, LGA ou LGB en aval (4 routes).
#     - PPGI : LGA uniquement (LGB n'accepte pas le PPGI).
#     - BACR : voie A (CRMB -> BAF -> LGB) OU voie B directe (CRMA/CRMB -> LGA/LGB).
#     - HRC DEC : décapage seul (PK).
#     - SKP et BAF ne reçoivent que du CRMB.
# --------------------------------------------------------------------------
ROUTES: dict[str, dict[str, list[str]]] = {
    "CRC": {
        "CRC_std": ["PK", "CRMB", "BAF", "SKP"],
    },
    "HDG": {
        "CRMA-LGA": ["PK", "CRMA", "LGA"],
        "CRMA-LGB": ["PK", "CRMA", "LGB"],
        "CRMB-LGA": ["PK", "CRMB", "LGA"],
        "CRMB-LGB": ["PK", "CRMB", "LGB"],
    },
    "PPGI": {
        "CRMA-LGA": ["PK", "CRMA", "LGA"],
        "CRMB-LGA": ["PK", "CRMB", "LGA"],
    },
    "BACR": {
        "voieA_CRMB-BAF-LGB": ["PK", "CRMB", "BAF", "LGB"],
        "voieB_CRMA-LGA": ["PK", "CRMA", "LGA"],
        "voieB_CRMA-LGB": ["PK", "CRMA", "LGB"],
        "voieB_CRMB-LGA": ["PK", "CRMB", "LGA"],
        "voieB_CRMB-LGB": ["PK", "CRMB", "LGB"],
    },
    "HRC DEC": {
        "DEC": ["PK"],
    },
}


def cout_key(process: str, famille: str) -> str:
    """Clé de l'onglet Couts_Variables pour un (process, famille).

    Le coût de galvanisation dépend de la famille : LGA-HDG, LGA-PPGI (peinture
    incluse), LGA-BACR, LGB-HDG, LGB-BACR. Les autres process ont une clé = leur nom.
    """
    if process == "LGA":
        return {"HDG": "LGA-HDG", "PPGI": "LGA-PPGI", "BACR": "LGA-BACR"}[famille]
    if process == "LGB":
        return {"HDG": "LGB-HDG", "BACR": "LGB-BACR"}[famille]
    return process


# --------------------------------------------------------------------------
# Options de modélisation (toggles). Modifiables par scénario / par l'app.
# --------------------------------------------------------------------------
@dataclass
class ModelOptions:
    """Paramètres de configuration du modèle (activables/désactivables).

    Attributs
    ---------
    galva_rule_hdg : bool
        Active la règle qualité HDG : ep <= seuil -> LGA obligatoire,
        ep > seuil -> LGB obligatoire. ATTENTION : cette règle N'EST PAS dans
        les documents source (la note §2.2/§4.4 dit que le choix LGA/LGB du HDG
        "doit être optimisé et n'est pas figé"). On la garde par choix explicite,
        on la documente comme hypothèse et on en chiffre le coût (avec/sans).
    galva_seuil_mm : float
        Seuil d'épaisseur de la règle galva HDG (mm).
    stocks_pf : bool
        Modélise les bilans de stock de produits finis (question E8) avec les
        bornes min/max de l'onglet Stocks_Initiaux.
    enforce_stock_min : bool
        Impose le stock de sécurité minimum (sinon seul le plafond max s'applique).
    cout_stockage : bool
        Ajoute les coûts de stockage à l'objectif (bonus B3).
    retards_autorises : bool
        Autorise la livraison après l'échéance avec pénalité (bonus B2).
    campagnes : bool
        Active les variables binaires de campagne + tonnage minimum (bonus B4, MILP).
        Clarification prof (Assia) : une campagne = une même FAMILLE sur une ligne
        pendant une semaine ; le changement de campagne est déclenché par un
        changement de FAMILLE seulement (pas grade/épaisseur/largeur).
    q_min_campagne : float
        Tonnage minimum d'une campagne ouverte (bonus B4), en tonnes.
    inclure_stock_pk : bool
        Compte le stock initial PK (bobines décapées, par grade) comme matière
        QUALIFIÉE disponible, en plus de la dispo HRC sur l'horizon. Clarification
        prof (Assia) : « le stock PK doit être vu comme un stock de semi-produit
        qualifié, avec une gestion par grade » ; la cohérence matière est assurée
        au niveau de l'entrée PK. Converti en équivalent HRC via le rendement PK.
    stock_pk_net_securite : bool
        Si vrai, seule la part au-dessus du stock de sécurité (initial − min) est
        utilisable (on conserve le min de sécurité PK) ; sinon tout l'initial.
    objectif : str
        Fonction objectif. ``"marge"`` (énoncé O1) : maximiser la marge sur coût
        variable. ``"nb_commandes"`` (clarification déléguée/prof) : maximiser le
        NOMBRE de commandes honorées intégralement (accept/reject binaire, MILP),
        la marge servant de fin départage (terme epsilon, n'altère jamais le
        nombre). Dans ce mode les commandes sont accept/reject (pas de partiel).
    """

    galva_rule_hdg: bool = True
    galva_seuil_mm: float = 0.6
    stocks_pf: bool = True
    enforce_stock_min: bool = True
    cout_stockage: bool = False
    retards_autorises: bool = False
    campagnes: bool = False
    q_min_campagne: float = 100.0
    # Baseline du projet : on inclut le stock PK qualifié par grade (clarification
    # prof), net du stock de sécurité. Mettre inclure_stock_pk=False pour comparer
    # au modèle « dispo HRC seule ».
    inclure_stock_pk: bool = True
    stock_pk_net_securite: bool = True
    objectif: str = "marge"
    poids_commande: float = 1.0e7


# Configuration de référence (le "baseline" des résultats du rapport).
BASELINE = ModelOptions()
