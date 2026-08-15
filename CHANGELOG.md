# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.
Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/).

---

## [1.4.64-dev.16] - 2026-08-15

### Added
- **#10** : Plusieurs EAN par produit — nouvelle table `product_barcodes(id, product_id, barcode, label)` avec contrainte UNIQUE sur le code-barres
- **#10** : Migration automatique au démarrage : les `products.barcode` existants sont copiés dans `product_barcodes`
- **#10** : API REST — 3 nouveaux endpoints : `GET /api/product/{id}/barcodes`, `POST /api/product/{id}/barcodes`, `DELETE /api/product/barcodes/{bc_id}`
- **#10** : UI — section EAN dans la modale "Modifier la fiche produit" : affichage des codes enregistrés avec suppression individuelle, champ d'ajout avec libellé optionnel
- **#10** : `GET /api/product/by_barcode` cherche désormais dans `product_barcodes` (tous les EAN) puis fallback sur `products.barcode`
- **#10** : Chaque achat avec EAN enregistre automatiquement le code dans `product_barcodes` (`INSERT OR IGNORE`)

### Changed
- `delete_product()` supprime aussi les lignes liées dans `product_barcodes` (cascade)
- `add_product()` enregistre le barcode initial dans `product_barcodes` en plus de `products.barcode`

---

## [1.4.64-dev.15] - 2026-08-15

### Added
- **#11** : API REST HA complète — 5 endpoints utilisables depuis HA sans composant custom : `GET /api/stock/products`, `GET /api/stock/low`, `POST /api/stock/consume-product` (FIFO), `POST /api/stock/consume-lot`, `POST /api/stock/add-lot`
- **#11** : Chaque endpoint documenté avec exemples `rest_command`, `sensor: platform: rest` et automatisations HA directement dans le code source
- **#9** : Tableau historique des prix par magasin dans la modale fiche produit (client-side, trié du plus récent au plus ancien)
- **#8** : Checkbox liste de courses — ouvre le panel d'achat semi-auto au lieu de cocher directement ; confirmation via le formulaire d'achat

### Fixed
- **#11** : `POST /api/stock/consume-lot` refactorisé — l'ancienne implémentation requêtait une table inexistante (`lots` au lieu de `stock_lots`) et n'utilisait pas `db.consume_lot()`

---

## [1.4.64-dev.14] - 2026-08-15

### Added
- **#23** : Fichiers de traductions `translations/en.json` et `translations/fr.json` pour les options de configuration HA
- **#13** : Page Stocks — vue groupée par produit (toggle Regrouper / Vue détaillée, état persisté en `localStorage`, compatible avec tous les filtres existants)

### Changed
- **#24** : `config.json` — options `retention_days_warning` et `retention_days_critical` exposées dans l'UI add-on HA (valeurs par défaut : 30 et 14 jours)
- **#24** : `config.py` — lit `/data/options.json` (Supervisor HA) en priorité pour les seuils DLC ; fallback sur les réglages in-app puis les variables d'environnement
- **#18** : `CHANGELOG.md` reconstruit depuis l'historique git (couvre toutes les versions depuis v1.4.2)

---

## [1.4.64-dev.13] - 2026-08-15

### Fixed
- **#7** : `build.json` — images Alpine 3.20 pinées pour les 3 archs (était `:latest`)
- **#25** : `Dockerfile` — dépendances pip avec bornes de version (`fastapi>=0.110.0,<1.0.0`, `uvicorn[standard]>=0.27.0,<1.0.0`, `jinja2>=3.1.0,<4.0.0`, `python-multipart>=0.0.9,<1.0.0`)
- **#16** : `add_lot_purchase()` supprimée de `db.py` (code mort — importée mais jamais appelée)
- **#17** : `consume_lot()` et `update_lot()` — erreurs silencieuses remplacées par des logs `warning` / `error` explicites
- **#27** : SQLite WAL mode activé au démarrage (`PRAGMA journal_mode=WAL`), timeout connexion 10 s, `busy_timeout` 10 000 ms

---

## [1.4.64-dev.12] - 2026-08-15

### Fixed
- **#5** : Panel Avancé — 7 clés de settings absentes de `DEFAULTS` dans `settings_store.py` (purgées à chaque chargement) → toutes les options du panel Avancé sont maintenant persistées correctement
- **#6** : Double push HA au démarrage — unification en une seule boucle asyncio (push immédiat au démarrage + périodique toutes les 5 min, sans thread daemon)
- **#14** : Scanner OFF — le flag `nameTouched` n'était pas réinitialisé au reset du formulaire → le nom du produit est désormais pré-rempli correctement depuis l'API Open Food Facts
- **#22** : Race condition SQLite sur la fusion de lots — transaction `BEGIN IMMEDIATE` atomique dans `_add_or_merge_lot()` (remplace l'ancienne séquence 3 connexions)
- **#26** : Erreurs push HA silencieuses — logs `warning` / `error` explicites dans `ha_entities.py` (URLError → warning, Exception → error)

---

## [1.4.64-dev.11] - 2026-08-15

### Security
- **#21** : Import CSV — taille limitée à 1 MB (rejet HTTP 413 si dépassée)
- **#4** : Routes `/admin` et `/debug` protégées par vérification du token Ingress HA ; port 8098 supprimé de `config.json` (accès exclusivement via Ingress)

---

## [1.4.64-dev.10] - 2026-08-15

### Security
- **#19** : Protection CSRF sur toutes les routes POST (middleware token double-submit cookie)
- **#20** : Barcode validé dans `/api/off` — regex alphanumérique, max 48 caractères

---

## [1.4.64-dev.9] - 2026-08-15

### Security
- **#2** : XSS corrigé — `AC_LOCATIONS_JSON | safe` dans `base.html` remplacé par sérialisation JSON sécurisée côté serveur
- **#3** : XSS corrigé — `price_history_json | safe` dans `products.html` remplacé par filtre sécurisé

---

## [1.4.64-dev.8] - 2026-08-14

### Chore
- Ajout des templates d'issues GitHub (bug, amélioration, fonctionnalité, UI)

---

## [1.4.64-dev.7] - 2026-08-08

### Fixed
- Panneau Acheter (liste de courses) : espacement flex corrigé entre les sections
- Formulaire d'achat : `display: grid` déplacé sur le bon élément DOM (était sur le wrapper)

---

## [1.4.64-dev.6] - 2026-08-07

### Fixed
- Espacement du panneau Acheter dans la liste de courses

---

## [1.4.64-dev.5] - 2026-08-07

### Added
- Autocomplete universel stylé — remplace tous les `<datalist>` natifs (produits, marques, magasins, emplacements)

---

## [1.4.64-dev.4] - 2026-08-07

### Changed
- Liste de courses : refonte complète du layout → cartes par catégorie avec items cochables (remplace la liste plate)

---

## [1.4.64-dev.3] - 2026-08-07

### Fixed
- Datalist enseignes françaises déplacé dans le bon bloc Jinja2 (`content`) — était hors du bloc et ignoré au rendu

---

## [1.4.64-dev.2] - 2026-08-07

### Added
- Datalist enseignes françaises sur les champs Magasin dans la liste de courses

---

## [1.4.64-dev.1] - 2026-08-07

### Added
- F18 : Depuis la liste de courses → ajout direct au stock (semi-automatique, avec correspondance produit existant)
- Multi-magasin dans la liste de courses (association item ↔ magasin préféré)
- Gestion des produits depuis la liste de courses (édition / suppression)

---

## [1.4.63] - 2026-08-07

### Added
- F9 : 5 capteurs Home Assistant exposés via l'API Supervisor (stock bas, DLC urgents, DLC bientôt, total lots, total produits)
- F7 : Export / Import CSV pour produits et lots (avec validation et rapport d'erreurs)

### Changed
- F10 : Lots sans DLC supportés (champ `best_before` optionnel)
- F6 : Auto-remplissage de la DLC depuis la fiche produit lors de l'achat
- F19 : Ordre d'affichage de la page d'accueil revu (alertes en tête)
- B4 : Bouton « Masquer stock à 0 » sur la page Stocks

### Fixed
- SyntaxError lié aux guillemets bouclés (U+201C / U+2019) dans `ha.py` — réécriture complète du fichier avec `Write`

---

## [1.4.59] - 2026-08-07

### Changed
- Refonte UI complète : sidebar avec icônes SVG, navigation améliorée, chip variants CSS, cards redesign
- Ajustements post-refonte : sidebar active state, contraste cards, chips, gap

---

## [1.4.57] - 2026-08-07

### Fixed
- Faux router HA, retour de `delete_lot`, tables SQL manquantes, `run.sh` version, `log_event`
- Corrections UI : CSS toasts, prix/pièce, filtre date, boutons shopping

---

## [1.4.3 → 1.4.56] - 2025-09-05 → 2025-09-16

> Développement initial intensif — commits non détaillés.

### Added (période)
- Module **Shopping** : liste de courses avec items cochables par catégorie
- Intégration base de données SQLite initiale
- Routes CRUD : produits, lots, achats, catégories, emplacements
- Layout sidebar + navigation principale

---

## [1.4.2] - 2025-09-04

### Added
- Page `_about.html` regroupant informations système et liens :
  - Add-on (nom, version, canal, slug)
  - Liens : Projet GitHub, Documentation, Issues, Changelog
  - Description de la version en cours
  - Système : Python, FastAPI, Jinja2, SQLite
  - Données : base SQLite, paramètres, journal (avec tailles affichées)
  - Comportement & UI : thème, sidebar, durée toasts, seuils DLC

### Changed
- `settings.html` : menu repensé (plus esthétique et responsive) + onglet **À propos**
- `settings.py` : fonctions pour récupérer et afficher les informations système
- `run.sh` : lecture des informations système (About)
- `Dockerfile` : copie de `config.json` dans l'image Docker
