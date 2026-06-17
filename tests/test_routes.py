"""Tests des coefficients de route : tranches d'épaisseur, rendements, marge."""
import math

from src import config
from src.routes import build_route_coeffs


def test_bracket_index():
    assert config.bracket_index(0.2) == 0       # <0.3
    assert config.bracket_index(0.3) == 1       # 0.3-0.4 (borne inf incluse)
    assert config.bracket_index(0.4) == 2
    assert config.bracket_index(0.69) == 3      # 0.5-0.7
    assert config.bracket_index(0.7) == 4
    assert config.bracket_index(1.0) == 5
    assert config.bracket_index(2.5) == 6       # >1.5


def test_facteur_hrc_crc(data):
    """h_r de la CRC = 1 / (rendement cumulé PK·CRMB·BAF·SKP)."""
    coeffs = build_route_coeffs(data, config.BASELINE)
    r = data.rend
    attendu = 1.0 / (r["PK"]["r"] * r["CRMB"]["r"] * r["BAF"]["r"] * r["SKP"]["r"])
    crc = next(c for (i, rn), c in coeffs.items() if c.famille == "CRC")
    assert math.isclose(crc.hrc_factor, attendu, rel_tol=1e-9)


def test_facteur_hrc_hdg_lga(data):
    coeffs = build_route_coeffs(data, config.BASELINE)
    r = data.rend
    attendu = 1.0 / (r["PK"]["r"] * r["CRMB"]["r"] * r["LGA"]["r"])
    hdg = next(c for (i, rn), c in coeffs.items()
               if c.famille == "HDG" and rn == "CRMB-LGA")
    assert math.isclose(hdg.hrc_factor, attendu, rel_tol=1e-9)


def test_decomposition_marge(data):
    """marge_u == recette − HRC − transfo + valorisation − zinc (cohérence E6)."""
    coeffs = build_route_coeffs(data, config.BASELINE)
    for c in coeffs.values():
        recompose = (c.recette - c.cout_hrc - c.cout_transfo
                     + c.valorisation - c.extra_zinc)
        assert math.isclose(c.marge_u, recompose, rel_tol=0, abs_tol=1e-6)


def test_zinc_seulement_galva(data):
    coeffs = build_route_coeffs(data, config.BASELINE)
    for c in coeffs.values():
        if c.famille in ("HDG", "PPGI"):
            assert c.extra_zinc > 0
        else:
            assert c.extra_zinc == 0.0


def test_regle_galva_filtre_routes(data):
    """Avec la règle, un HDG fin (ep<=0.6) n'a que des routes finissant en LGA."""
    coeffs = build_route_coeffs(data, config.ModelOptions(galva_rule_hdg=True))
    for (i, rn), c in coeffs.items():
        if c.famille == "HDG" and c.order.ep <= 0.6:
            assert c.procs[-1] == "LGA"
        if c.famille == "HDG" and c.order.ep > 0.6:
            assert c.procs[-1] == "LGB"
    # Sans la règle, les deux destinations coexistent pour le HDG.
    coeffs2 = build_route_coeffs(data, config.ModelOptions(galva_rule_hdg=False))
    dest = {c.procs[-1] for c in coeffs2.values() if c.famille == "HDG"}
    assert dest == {"LGA", "LGB"}
