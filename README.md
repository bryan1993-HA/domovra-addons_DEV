<div align="center">

<img src="https://raw.githubusercontent.com/bryan1993-HA/domovra-addons/main/domovra/icon.png" width="120" alt="Domovra logo">

# Domovra — Gestion de stock domestique

**Add-on Home Assistant** · Gérez votre frigo, congélateur et placards depuis votre tableau de bord HA.

[![Version](https://img.shields.io/badge/version-1.4.64--dev.14-blue?style=for-the-badge)](https://github.com/bryan1993-HA/domovra-addons_DEV/blob/main/CHANGELOG.md)
[![Stage](https://img.shields.io/badge/stage-experimental-orange?style=for-the-badge)](https://github.com/bryan1993-HA/domovra-addons_DEV)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-compatible-41BDF5?style=for-the-badge&logo=home-assistant)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)

[![Forum HACF](https://img.shields.io/badge/Forum-HACF-blue?style=flat-square)](https://forum.hacf.fr/t/domovra-gestion-de-stock-domestique-pour-home-assistant/66040)
[![Issues](https://img.shields.io/github/issues/bryan1993-HA/domovra-addons_DEV?style=flat-square)](https://github.com/bryan1993-HA/domovra-addons_DEV/issues)
[![Ko-fi](https://img.shields.io/badge/☕_Ko--fi-soutenir-FF5E5B?style=flat-square)](https://ko-fi.com/domovra)

---

> **Domovra** est un gestionnaire de stock domestique complet, intégré nativement à Home Assistant via Ingress.  
> Suivez vos dates de péremption, gérez vos listes de courses, et laissez HA vous alerter automatiquement.

</div>

---

## 📋 Table des matières

- [Aperçu](#-aperçu)
- [Fonctionnalités](#-fonctionnalités)
- [Installation](#-installation)
- [Configuration](#️-configuration)
- [Capteurs Home Assistant](#-capteurs-home-assistant)
- [Données & Persistance](#-données--persistance)
- [Architecture technique](#️-architecture-technique)
- [Changelog](#-changelog)
- [Support](#️-support)
- [Forum & Communauté](#-forum--communauté)
- [Conventions de commits](#-conventions-de-commits)

---

## 🖼️ Aperçu

<div align="center">
<img src="https://raw.githubusercontent.com/bryan1993-HA/domovra-addons/main/domovra/images/EcranPrincipal.png" alt="Écran principal Domovra" width="700">
</div>

---

## ✨ Fonctionnalités

### 📦 Gestion des stocks

| Fonctionnalité | Description |
|---|---|
| **Produits** | Catalogue complet avec catégories hiérarchiques, EAN/barcode, marque, unité, quantité minimale |
| **Lots** | Suivi individuel par emplacement avec quantité, DLC, date de congélation |
| **Emplacements** | Frigo, congélateur, placards — entièrement personnalisables |
| **Statuts DLC** | 🟢 OK · 🟡 Bientôt (seuil configurable, défaut 30 j) · 🔴 Urgent (défaut 14 j) |
| **Consommation** | Consommation partielle ou totale d'un lot, avec traçabilité |
| **Vue groupée** | Regroupement des lots par produit (total, nombre de lots, pire DLC) avec détail expansible |
| **Filtres avancés** | Recherche texte + filtres emplacement / produit / statut DLC / masquer stock à 0 |

### 🛒 Achats

| Fonctionnalité | Description |
|---|---|
| **Formulaire enrichi** | Marque, EAN, prix, magasin, conditionnement (pack × quantité), DLC, congélation |
| **Scanner code-barres** | Caméra live avec fallback manuel — recherche automatique sur **Open Food Facts** |
| **Autocomplete universel** | Suggestions intelligentes sur tous les champs (produits, marques, magasins, emplacements) |
| **Fusion automatique** | Détecte les lots existants identiques et les fusionne en une seule transaction atomique |
| **Prix / kg·L** | Calcul automatique du prix au kg ou au litre pour comparer les produits |

### 📝 Liste de courses

| Fonctionnalité | Description |
|---|---|
| **Listes par catégorie** | Items cochables organisés par catégorie, layout cartes |
| **Multi-magasin** | Associez chaque item à un magasin préféré |
| **Retour au stock** | Ajout semi-automatique au stock depuis la liste de courses avec correspondance produit |
| **Enseignes françaises** | Autocomplétion sur les grandes enseignes (Leclerc, Carrefour, Lidl…) |

### 🏠 Intégration Home Assistant

| Fonctionnalité | Description |
|---|---|
| **5 capteurs HA** | Push automatique toutes les 5 min via l'API Supervisor |
| **Alertes stock bas** | Produits en-dessous de leur quantité minimale → sensor + page accueil |
| **Alertes DLC** | Lots urgents et bientôt périmés → sensors dédiés |
| **Options via UI** | Seuils DLC configurables directement dans l'UI de gestion des add-ons |
| **Ingress natif** | Accessible depuis le panneau latéral HA, sans port exposé |

### 📤 Export / Import

- **Export CSV** : produits et lots en un clic
- **Import CSV** : import avec validation ligne par ligne et rapport d'erreurs (limite 1 MB)
- **Admin DB** : visualisation des tables SQLite + export brut (accès protégé par token Ingress)

### 📓 Journal & Traçabilité

- Événements horodatés : achats, consommations, ajouts, suppressions
- Purge configurable (rétention paramétrable)
- Consultable directement depuis l'interface

### ⚙️ Paramètres & Interface

- **Thème** : automatique (suit le système) / clair / sombre
- **Sidebar** : mode compact ou standard
- **Toasts** : durée et couleurs personnalisables
- **Panel Avancé** : scanner caméra, bloc Open Food Facts, notifications HA
- **À propos** : versions, tailles des fichiers de données, liens rapides

---

## 🧩 Installation

> ⚠️ Ce dépôt est la **version de développement (DEV)**. Pour une installation stable, utilisez le dépôt principal : [`bryan1993-HA/domovra-addons`](https://github.com/bryan1993-HA/domovra-addons).

### Version DEV (ce dépôt)

1. Dans Home Assistant : **Paramètres → Modules complémentaires → Magasin → ⋮ → Dépôts**
2. Ajoutez : `https://github.com/bryan1993-HA/domovra-addons_DEV`
3. Recherchez **Domovra (DEV)** → **Installer** → **Démarrer**
4. Ouvrez l'interface via le panneau latéral

### Version stable

1. Dans Home Assistant : **Paramètres → Modules complémentaires → Magasin → ⋮ → Dépôts**
2. Ajoutez : `https://github.com/bryan1993-HA/domovra-addons`
3. Recherchez **Domovra (Stock Manager)** → **Installer** → **Démarrer** → *Ouvrir l'interface*

---

## ⚙️ Configuration

Les options sont accessibles dans **Paramètres → Modules complémentaires → Domovra → Configuration**.

| Option | Type | Défaut | Description |
|---|---|---|---|
| `retention_days_warning` | `int` | `30` | Jours avant expiration → statut **Bientôt** 🟡 |
| `retention_days_critical` | `int` | `14` | Jours avant expiration → statut **Urgent** 🔴 |

> Ces valeurs peuvent aussi être ajustées directement depuis l'interface Domovra (Paramètres → Seuils DLC).  
> La priorité est : **options HA Supervisor** › réglages in-app › variables d'environnement › défauts.

---

## 📡 Capteurs Home Assistant

Domovra expose automatiquement **5 capteurs** via l'API Supervisor HA, mis à jour toutes les 5 minutes :

| Entité | Description | Unité |
|---|---|---|
| `sensor.domovra_low_stock` | Nombre de produits en-dessous de la quantité minimale | produits |
| `sensor.domovra_expiring_urgent` | Lots expirés ou en état Urgent 🔴 | lots |
| `sensor.domovra_expiring_soon` | Lots en état Bientôt 🟡 | lots |
| `sensor.domovra_total_lots` | Nombre total de lots en stock | lots |
| `sensor.domovra_total_products` | Nombre total de produits référencés | produits |

Ces capteurs peuvent être utilisés dans vos **automatisations**, **tableaux de bord** et **notifications HA**.

---

## 💾 Données & Persistance

Toutes les données sont stockées dans le répertoire `/data` de l'add-on (mappé sur le volume persistent HA) :

| Fichier | Contenu |
|---|---|
| `/data/domovra.sqlite3` | Base de données principale (produits, lots, achats, emplacements, journal…) |
| `/data/settings.json` | Préférences UI (thème, seuils, toasts, panel avancé…) |
| `/data/domovra.log` | Journal applicatif |
| `/data/options.json` | Options écrites par le Supervisor HA (configuration add-on) |

> **SQLite en mode WAL** : `journal_mode=WAL` activé au démarrage pour les accès concurrents. Timeout 10 s, `busy_timeout` 10 000 ms.

---

## 🛠️ Architecture technique

```
Domovra (add-on HA)
├── FastAPI          — serveur ASGI (Python 3.x)
├── Uvicorn          — serveur ASGI standard
├── Jinja2           — moteur de templates HTML
├── SQLite           — base de données embarquée (WAL)
├── Vanilla JS/CSS   — frontend sans framework
└── HA Ingress       — proxy natif HA (port 8098 interne, non exposé)
```

**Stack complète :** `Python · FastAPI · Jinja2 · SQLite · HTML/CSS/JS · Docker (Alpine 3.20)`

---

## 📝 Changelog

Le détail de toutes les versions est disponible dans [CHANGELOG.md](../CHANGELOG.md).

| Version | Date | Points clés |
|---|---|---|
| `1.4.64-dev.14` | 2026-08-15 | Options HA, traductions, CHANGELOG, vue groupée stocks |
| `1.4.64-dev.13` | 2026-08-15 | SQLite WAL, logs, dead code, versions pinées |
| `1.4.64-dev.12` | 2026-08-15 | Fix Panel Avancé, double push HA, race condition, scanner |
| `1.4.64-dev.11` | 2026-08-15 | Sécurité : CSV limit, admin guard |
| `1.4.64-dev.10` | 2026-08-15 | Sécurité : CSRF, validation barcode |
| `1.4.64-dev.9` | 2026-08-15 | Sécurité : XSS corrigé |
| `1.4.64-dev.5` | 2026-08-07 | Autocomplete universel stylé |
| `1.4.64-dev.4` | 2026-08-07 | Refonte liste de courses |
| `1.4.63` | 2026-08-07 | 5 capteurs HA, export/import CSV |

---

## ❤️ Support

Domovra est un projet personnel développé sur mon temps libre, par plaisir de coder.  
Si vous le trouvez utile, vous pouvez me soutenir avec un café ☕

<div align="center">

[![Support on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/domovra)

[ko-fi.com/domovra](https://ko-fi.com/domovra) — Les dons sont entièrement facultatifs et n'ouvrent aucune contrepartie payante.

</div>

---

## 💬 Forum & Communauté

Retours, idées, questions et suivi du projet sur le forum **HACF** (Home Assistant Communauté Francophone) :

👉 [forum.hacf.fr — Domovra](https://forum.hacf.fr/t/domovra-gestion-de-stock-domestique-pour-home-assistant/66040)

Pour signaler un bug ou proposer une fonctionnalité :  
👉 [GitHub Issues](https://github.com/bryan1993-HA/domovra-addons_DEV/issues)

---

## 📌 Conventions de commits

| Préfixe | Usage | Exemple |
|---|---|---|
| `feat:` | Nouvelle fonctionnalité | `feat: ajout du scanner code-barres` |
| `fix:` | Correction de bug | `fix: éviter le crash si la DLC est vide` |
| `security:` | Correctif de sécurité | `security: CSRF middleware sur les routes POST` |
| `docs:` | Documentation uniquement | `docs: mise à jour du README` |
| `chore:` | Maintenance / version | `chore: bump version to 1.4.64-dev.14` |
| `refactor:` | Refactoring sans impact fonctionnel | `refactor: centraliser la gestion des erreurs db` |

---

<div align="center">

Fait avec ❤️ pour la communauté Home Assistant francophone

</div>
