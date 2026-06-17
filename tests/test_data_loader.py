"""Tests de la lecture des données (Donnees_MaghrebSteel.xlsx)."""
from src import config


def test_nombre_de_commandes(data):
    # Le carnet contient 66 commandes (énoncé §3 / onglet Commandes).
    assert len(data.orders) == 66


def test_perimetre_exclut_quarto(data):
    familles = {o.famille for _i, o in data.orders_in_scope()}
    assert "Quarto" not in familles
    # Une commande Quarto existe bien dans le carnet brut mais est hors périmètre.
    assert any(o.famille == "Quarto" for o in data.orders)


def test_dispo_hrc_valeurs_source(data):
    # Valeurs de l'onglet Prix_HRC (source of truth).
    attendu = {"DC01": 6750, "DD13": 3750, "DX51": 3200, "DX52": 1500, "S320": 800}
    for g, v in attendu.items():
        assert data.dispo_hrc[g] == v


def test_parametres_metier(data):
    assert data.prix_chute == 1800.0
    assert data.coef_decl == 0.5
    assert data.coef_nc == 0.2
    assert data.prix_zinc == 18000.0
    assert data.cons_zinc_hdg == 0.025
    assert data.penalite_retard["Haute"] == 500.0
    assert data.cout_stock_pf == 40.0


def test_cadences_presentes(data):
    # Cadences clés (onglet Cadences).
    assert data.cadence[("LGA", "PPGI")] == 300
    assert data.cadence[("LGA", "HDG")] == 250
    assert data.cadence[("LGB", "HDG")] == 455
    assert data.cadence[("CRMB", "CRC")] == 789
    assert data.cadence[("PK", "CRC")] == 900


def test_stocks_pf_bornes(data):
    for fam in config.FAMILIES:
        sk = data.stock_pf[fam]
        assert sk.mini <= sk.initial <= sk.maxi
