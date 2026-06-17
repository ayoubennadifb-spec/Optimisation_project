"""Simulateur Capacité–Commande Maghreb Steel — package source.

Modules :
    config       : constantes, routes métallurgiques, options (toggles) du modèle.
    data_loader  : lecture rigoureuse de Donnees_MaghrebSteel.xlsx.
    routes       : pré-calcul des coefficients par (commande, route).
    model        : construction du programme linéaire PuLP.
    solve        : résolution CBC + extraction de la solution et des duales.
    validation   : revérification indépendante des contraintes (question E15).
    reporting    : KPIs, plan de marche, utilisation, shadow prices, figures.
    sensitivity  : scénarios E20/E21/E22 + bonus B8/B9 + coût de la règle galva.
    extensions   : bonus B5 (stats Branch-and-Bound du MILP campagnes). Les bonus
                   B2 (retards), B3 (stockage) et B4 (campagnes) sont des options
                   (toggles) du modèle, voir ``config.ModelOptions`` et ``model``.
"""
