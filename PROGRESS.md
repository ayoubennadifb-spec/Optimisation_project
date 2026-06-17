# PROGRESS — Simulateur Capacité–Commande Maghreb Steel

> Fichier de reprise (résumabilité). Mis à jour au fil de l'eau. Reflète l'état réel du dépôt.

## Objectif
Refaire le projet RO **rigoureusement et honnêtement**, fidèle aux *source of truth*
(`Note_Projet_Maghreb_Steel_1.docx`, `Donnees_MaghrebSteel.xlsx`, `Présentation…pptx`
dans le dossier `…(1)\`). Cible : **20/20**. Périmètre : **E1–E24 + B1–B10 + app Streamlit**.

## Décisions cadrées avec l'utilisateur
- **Périmètre maximal** : toutes les essentielles + tous les bonus + application.
- **Règle galva HDG** (ep≤0,6→LGA / >0,6→LGB) : **conservée** mais implémentée comme
  contrainte **paramétrable** (`ModelOptions.galva_rule_hdg`), documentée comme hypothèse
  hors note de cadrage, avec analyse « coût de la règle » (avec/sans).

## Résultats de référence — BASELINE ACTUELLE (validée indépendamment, tol 1e-4 T)
> Baseline = marge (O1) + règle galva + stocks PF (E8) + **stock PK qualifié par grade, net du min
> sécurité** (clarification prof). L'ancienne valeur 33,0 MMAD / 75,9 % était SANS le stock PK.
- Marge **34 190 405 MAD (34,19 MMAD)** · service **79,8 %** · **50/65 commandes pleines** · 11 refus + 4 partielles.
- Goulot dominant **LGA S1** (shadow **588 355 MAD/jour**) ; **CRMB S2**, **BAF S1&S3** saturées ; HRC **S320** épuisé (1 908), DX51 (1 629), DX52 (1 454).
- E20 HRC+10 % → 25,37 MMAD (−25,8 %) · E21 LGB +2j S2 → **impact nul** (report) ·
  E22 urgente 300T HDG → servie 300/300 (+291 545 MAD) ·
  coût règle galva = **−3,19 MMAD (−8,5 %)** (sans règle : 37,38 MMAD / 86,9 %) ·
  B9 cadences ±5 % → 34,01 ↔ 34,37 MMAD · B8 inflexion DC01 ≈ 5 400 T.
- **Variante objectif `nb_commandes`** (prof) : MILP accept/reject → **55/65 commandes** (vs 50) pour
  32,70 MMAD (−1,49). Marge gardée comme objectif principal (O1) ; nb commandes en option.

## État du dépôt
**FAIT & VÉRIFIÉ** (src/, construit lors d'une session précédente, audité ce jour) :
- `src/config.py` — sets, routes (§2.2/§4.4), tranches ép., toggles (ModelOptions). OK.
- `src/data_loader.py` — lecture des 9 onglets + sanity checks. OK.
- `src/routes.py` — coefficients par (commande, route) : rendements, h_r, marge_u, charge. OK.
- `src/model.py` — PL PuLP : demande, capacité, HRC, **stock PF (E8)** + toggles B2/B3/B4. OK.
- `src/solve.py` — résolution CBC + extraction (duales, coûts réduits, utilisation). OK.
- `src/validation.py` — revérification indépendante du solveur (E15). **TOUT OK**.
- `src/reporting.py` — KPIs, plan de marche, utilisation, shadow prices, refus+bloquante, B7, figures. OK.
- `src/sensitivity.py` — E20/E21/E22 + B8 (enveloppe) + B9 (robustesse) + coût galva. OK.
- `outputs/` — kpis.json, plan_de_marche.xlsx, shadow_prices.csv, commandes_refusees.csv, figures.
- **Bug corrigé** : caractère `Δ` (cp1252) dans `sensitivity.__main__`.

**TERMINÉ (suite)** :
- [x] `main.py` (racine) — orchestrateur unique load→build→solve→validate→export (+ options CLI). Testé.
- [x] `src/extensions.py` — **B5** : stats Branch-and-Bound + balayage Q_min (transition LP→MILP).
      Docstring de `src/__init__.py` corrigé.
- [x] `tests/` — **29 tests pytest, tous verts** (rendements, marge, capacité, HRC, stock, galva, sensibilité, B5).
- [x] `README.md` — installation, lancement, structure, correspondance questions↔code.
- [x] `app/streamlit_app.py` — **B6** : testé en headless (Streamlit AppTest) → résout 33,0 MMAD sans exception.
- [x] `rapport/rapport.pdf` — rapport complet **E1–E24 + B1–B10** + annexes + déclaration IA,
      compilé (**11 pages**, pdflatex/MiKTeX, 0 warning, références résolues).
      **Figures intégrées** : flux métallurgique (TikZ), carte de chaleur utilisation (E16),
      marge par famille (E19), **courbe d'enveloppe DC01 (B8)**. Corrections typo `\fg` (espaces).
- [x] `requirements.txt` — `pulp` figé `<4` (API stable).
- [x] `.gitignore` — caches Python, artefacts LaTeX (on versionne `.tex` + `.pdf`).

## Vérifié en direct (dernière session)
- `python -m pytest -q` → **29 passed** (warnings = dépréciation PuLP 4.0, inoffensifs).
- `python main.py` → solve + validation **TOUT OK** + export `outputs/`. Marge 33,0 MMAD reproduite.

## Ajouts session 16/06
- [x] `slides/soutenance.tex` + `soutenance.pdf` — **support de soutenance Beamer** (15 slides,
      thème Madrid/msblue, figures réutilisées : flux TikZ, heatmap utilisation, marge, enveloppe).
      Compilé 0 erreur. Couvre E1→E24 + bonus, pour la défense 10 min (§5.3).
- [x] Rapport E24 enrichi : **analyse honnête des stocks interprocess** — tampons en amont des goulots
      réels (LGA + HRC) ⇒ ne déplaceraient pas l'optimum ; donnée non ventilée par grade. Niveau 3 de
      complétude, pas un levier. (Pas de modèle « cosmétique » : choix de rigueur/honnêteté.)

## Noms du groupe (intégrés 17/06)
Ayoub EN-NADIF · Ennaji Soufiane · Tarik Elbaraka · Hakim Abdelhakim · Amine Oubella —
insérés dans `rapport/rapport.tex`, `slides/soutenance.tex`, `README.md`.

## Justification du choix « stock PK » (E9, défendable)
(i) stock PK = HRC déjà décapé, qualifié (grade hérité), utilisable en aval, **distinct** de la dispo HRC
→ pas de double comptage ; (ii) on ne compte que (init − min sécurité) → on ne vide pas le tampon ;
(iii) /ρ_PK = conversion en équivalent HRC (contrainte écrite côté HRC). Activable (`inclure_stock_pk`)
pour le « avec/sans ». Ne change pas les conclusions structurelles.

## Application Streamlit (B6) — vérifiée 17/06
`app/streamlit_app.py` : carnet éditable + options (objectif marge/nb_commandes, règle galva, stocks PF,
**stock PK**, retards, stockage, campagnes, variation prix HRC) → relance + plan de marche, utilisation,
shadow prices, refus. **Testée headless (Streamlit AppTest)** : 0 exception ; mode marge → 34,19 MMAD,
mode nb_commandes → 32,70 MMAD. Lancement : `streamlit run app/streamlit_app.py`.

## Round mise en forme + visuels + B1 (17/06)
- **Couverture pro** (cadre 0,5 cm, logos EMINES/Maghreb Steel détourés, marine+doré) ; résumé et TOC
  chacun sur une page dédiée ; Promotion **2028**.
- **Mise en forme** : titres de section stylés (titlesec, marine+filet doré), en-têtes/pieds courants
  (fancyhdr), légendes harmonisées (caption).
- **Symboles** : E5 = 3 tableaux (ensembles / attributs commande / paramètres) → **chaque variable des
  équations est définie** ($D_i, w_i, f_i, \pi_i, M$…).
- **Nouveaux visuels** (générés par `reporting.py`) : E3 demande vs HRC, E14 plan empilé, **E17 frontière
  marge / coût d'opportunité** (pourquoi une commande est refusée).
- **B1 RÉEL** : `reporting.prix_plancher(...)` calcule le prix plancher d'acceptation (= revient + coût
  d'opportunité). Ex. HDG fin LGA S1 → 12 747 (explique le refus de CMD-001) ; exposé dans `main.py`,
  l'app (onglet « Prix plancher ») et le rapport B1. Test ajouté → **31 tests verts**.
- **Texte** : E20 reformulé en argument dual/enveloppe ; E2 cite les simplifications §4.4 (DULL, split SKP) ;
  E18 note sur les shadow prices de demande.
- **CLI** : `main.py --objectif nb_commandes` et `--sans-stock-pk`. Rapport = **16 pages**.

## Reste (côté étudiant)
- Appropriation formulation/analyse pour la soutenance (test individuel — politique IA).

## STATUT : PROJET COMPLET ✅
Toutes les essentielles (E1–E24) et tous les bonus (B1–B10) traités ; application livrée ; rapport PDF
compilé ; validation indépendante OK ; 29 tests verts. Prêt pour relecture/appropriation et soutenance.

## Comment lancer
```bash
cd simulateur_maghreb_steel
pip install -r requirements.txt           # pulp, openpyxl, pandas, numpy, matplotlib, streamlit, pytest
python -m src.solve            # résout + KPIs + top shadow prices
python -m src.validation       # revérification indépendante (E15)
python -m src.reporting        # toutes les tables + exports outputs/
python -m src.sensitivity      # E20/E21/E22 + galva + B9
# (à venir) python main.py     # pipeline complet ; streamlit run app/streamlit_app.py
```
> Sous Windows, exporter `PYTHONIOENCODING=utf-8` si la console plante sur un caractère non cp1252.

## Garde-fous (politique IA du projet)
Formulation/rapport/analyse doivent être **compris et défendables** par l'étudiant (testé en
soutenance individuelle). Le code et les analyses sont construits pour être appropriables :
commentaires, docstrings, et un rapport pédagogique. Déclaration d'usage d'IA incluse en annexe.
