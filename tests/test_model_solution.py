"""Tests de la solution optimale : optimalité, valeurs de référence, goulots."""
from src import config
from src.routes import materiau_disponible
from src.solve import run
from src.validation import validate


def test_statut_optimal(baseline):
    assert baseline.status == "Optimal"


def test_marge_de_reference(baseline):
    # Marge baseline documentée : 34 190 405 MAD (règle galva + stocks PF +
    # stock PK qualifié par grade, net du stock de sécurité — clarification prof).
    assert abs(baseline.margin - 34_190_405) < 5_000


def test_taux_de_service(baseline):
    assert 79.0 < baseline.taux_service() < 81.0


def test_lga_s1_saturee(baseline):
    used, avail = baseline.utilisation[("LGA", 1)]
    assert avail > 0
    assert used / avail > 0.999          # LGA semaine 1 = goulot saturé


def test_crma_sous_utilisee(baseline):
    # CRMA n'est pas utilisée (cas remarquable : le goulot galva est en aval, LGA).
    total_crma = sum(u for (L, t), (u, a) in baseline.utilisation.items() if L == "CRMA")
    assert total_crma < 1e-6


def test_hrc_s320_sature(baseline):
    """Le grade S320 (le plus rare) doit être entièrement consommé.

    La matière disponible inclut la dispo HRC (800 T) + le stock PK qualifié net
    du min de sécurité (clarification prof) ; on compare donc à cette dispo effective.
    """
    d = baseline.data
    dispo_eff = materiau_disponible(d, config.BASELINE)["S320"]
    conso = sum(baseline.bm.coeffs[(i, rn)].hrc_factor * v
                for (i, rn, t), v in baseline.x.items()
                if baseline.bm.coeffs[(i, rn)].grade == "S320")
    assert conso <= dispo_eff + 1e-4
    assert conso > 0.99 * dispo_eff


def test_validation_independante_ok(data, baseline):
    rep = validate(data, baseline.x, config.BASELINE)
    assert rep.all_ok, str(rep)


def test_cout_regle_galva(data):
    """Sans la règle galva la marge est strictement meilleure (≈ 37,38 MMAD)."""
    sans = run(data, config.ModelOptions(galva_rule_hdg=False))
    avec = run(data, config.ModelOptions(galva_rule_hdg=True))
    assert sans.margin > avec.margin
    assert abs(sans.margin - 37_378_441) < 50_000


def test_capacite_jamais_depassee(baseline):
    for (L, t), (used, avail) in baseline.utilisation.items():
        assert used <= avail + 1e-6, f"{L} S{t} dépasse la capacité"
