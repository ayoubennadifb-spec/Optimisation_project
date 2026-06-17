"""Tests des extensions bonus (B2/B3/B4/B5) et de l'analyse de sensibilité."""
from src import config
from src.solve import run
from src.validation import validate
from src import sensitivity, extensions


def test_stocks_pf_respecte_bornes(data, baseline):
    """Les stocks de produits finis restent dans [min, max] à chaque semaine."""
    rep = validate(data, baseline.x, config.BASELINE)
    check = next(c for c in rep.checks if c.nom.startswith("Stock PF"))
    assert check.ok


def test_retards_ne_degradent_pas(data):
    """B2 : autoriser les retards ne peut qu'améliorer (ou égaler) la marge brute."""
    base = run(data, config.ModelOptions(retards_autorises=False))
    late = run(data, config.ModelOptions(retards_autorises=True))
    assert late.margin >= base.margin - 1e-6


def test_cout_stockage_reduit_marge(data):
    """B3 : pénaliser le stockage ne peut pas augmenter la marge réelle (tol. solveur)."""
    sans = run(data, config.ModelOptions(cout_stockage=False))
    avec = run(data, config.ModelOptions(cout_stockage=True))
    assert avec.margin <= sans.margin + 1.0     # 1 MAD : robuste au bruit du solveur


def test_campagnes_milp_optimal(data):
    """B4 : la variante campagnes (MILP) se résout à l'optimum."""
    sol = run(data, config.ModelOptions(campagnes=True))
    assert sol.status == "Optimal"
    assert sol.is_mip


def test_b5_bb_stats(data):
    """B5 : Q_min faible -> campagnes non contraignantes (gap nul)."""
    s = extensions.bb_stats(data, config.ModelOptions(campagnes=True,
                                                      q_min_campagne=100.0))
    assert s.status == "Optimal"
    assert s.gap_pct <= 1e-3


def test_e20_hrc_plus_cher(data):
    base = run(data, config.BASELINE)
    s = sensitivity.scenario_hrc(data, 1.10)
    assert s.margin < base.margin           # +10% HRC dégrade la marge


def test_e21_panne_lgb_impact_nul(data):
    base = run(data, config.BASELINE)
    s = sensitivity.scenario_panne(data, "LGB", 2, 2.0)
    assert abs(s.margin - base.margin) < 1.0   # LGB a du mou en S2


def test_e22_commande_urgente_acceptee(data):
    s, idx = sensitivity.scenario_commande_urgente(data)
    assert s.served.get(idx, 0.0) > 299.0      # servie 300/300


def test_prix_plancher(data):
    """B1 : le prix plancher dépasse le coût de revient quand la commande consomme
    une ressource saturée ; il s'y réduit sinon."""
    from src import reporting
    s = run(data, config.BASELINE)
    # HDG fin -> LGA semaine 1 (saturée) : surcoût d'opportunité strictement positif
    r = reporting.prix_plancher(s, "HDG", "DX51", 0.3, 1250, 1)
    assert r is not None
    assert r["prix_plancher"] > r["cout_revient"] + 1.0
    assert r["cout_opportunite"] > 0.0
    # HRC DEC (ligne PK seule, non saturée) : plancher == coût de revient
    r2 = reporting.prix_plancher(s, "HRC DEC", "DC01", 3.0, 1320, 4)
    assert abs(r2["prix_plancher"] - r2["cout_revient"]) < 1.0


def test_objectif_nb_commandes(data):
    """Objectif 'nombre de commandes' (clarification prof) : honore au moins autant
    de commandes pleines que l'objectif marge, mais sans améliorer la marge."""
    def full_count(s):
        return sum(1 for i, o in data.orders_in_scope()
                   if s.served.get(i, 0.0) >= o.ton - 1e-3)
    marge = run(data, config.ModelOptions(objectif="marge"))
    nb = run(data, config.ModelOptions(objectif="nb_commandes"))
    assert nb.is_mip                                   # accept/reject binaire -> MILP
    assert full_count(nb) >= full_count(marge)         # sert plus (ou autant) de commandes
    assert nb.margin <= marge.margin + 1e-6            # au prix d'une marge <= optimum marge
