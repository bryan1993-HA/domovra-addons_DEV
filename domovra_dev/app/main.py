# domovra/app/main.py
# ============================================================
# Domovra — Point d'entrée FastAPI
# - Boot de l'app (logging, templates, static)
# - Montage des routers (pages + API + debug)
# - Hooks de lifecycle (startup)
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import DB_PATH, get_retention_thresholds
from services.events import _ensure_events_table
from utils.assets import ensure_hashed_asset
from utils.jinja import build_jinja_env

# Routers "pages"
from routes.home import router as home_router
from routes.products import router as products_router
from routes.locations import router as locations_router
from routes.lots import router as lots_router
from routes.achats import router as achats_router
from routes.journal import router as journal_router
from routes.support import router as support_router
from routes.settings import router as settings_router
# Routers techniques
from routes.api import router as api_router
from routes.debug import router as debug_router
from routes.admin_db import router as admin_db_router
from routes.shopping import router as shopping_router
from routes.ha import router as ha_router
from routes.export_import import router as export_import_router
from routes.print_route import router as print_router


# DB (uniquement ce dont on a besoin ici)
from db import init_db


# ============================================================
# Logging
# ============================================================

def setup_logging() -> None:
    """Console + fichier /data/domovra.log (rotation)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(name)s %(levelname)s: %(message)s")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    try:
        os.makedirs("/data", exist_ok=True)
        fh = RotatingFileHandler(
            "/data/domovra.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:  # pragma: no cover
        logging.getLogger("domovra").warning("Impossible d'ouvrir /data/domovra.log: %s", e)


setup_logging()
logger = logging.getLogger("domovra")


# ============================================================
# App & Templates
# ============================================================

app = FastAPI()
templates = build_jinja_env()

templates.globals.setdefault("ASSET_CSS_PATH", "static/css/domovra.css")
app.state.templates = templates


# ============================================================
# Middleware CSRF
# ============================================================

@app.middleware("http")
async def csrf_check(request: Request, call_next):
    """
    Protection CSRF basée sur la vérification du header Origin.

    Logique :
    - Les méthodes sûres (GET, HEAD, OPTIONS) passent sans vérification.
    - Via HA Ingress (header X-Ingress-Path présent) : HA gère l'auth,
      aucun risque CSRF → on laisse passer.
    - Accès direct port 8098 : si le header Origin est présent et ne
      correspond pas au Host, la requête cross-site est rejetée (403).
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return await call_next(request)

    if request.headers.get("x-ingress-path"):
        return await call_next(request)

    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        origin_host = urlparse(origin).netloc
        if origin_host != host:
            logger.warning(
                "CSRF rejeté : origin=%s host=%s path=%s",
                origin, host, request.url.path,
            )
            return PlainTextResponse("Forbidden: CSRF check failed", status_code=403)

    return await call_next(request)


# ============================================================
# Fichiers statiques + CSS versionné
# ============================================================

HERE = os.path.dirname(__file__)
STATIC_DIR = os.path.join(HERE, "static")
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

try:
    css_rel = ensure_hashed_asset("static/css/domovra.css")
    if not (isinstance(css_rel, str) and css_rel.startswith("static/")):
        css_rel = "static/css/domovra.css"
    templates.globals["ASSET_CSS_PATH"] = css_rel
    logger.info("ASSET_CSS_PATH = %s", css_rel)
except Exception as e:  # pragma: no cover
    logger.exception("Failed to compute ASSET_CSS_PATH: %s", e)

try:
    def _ls(p: str):
        try:
            return sorted(os.listdir(p))
        except Exception:
            return "N/A"

    logger.info("Static mounted at %s", STATIC_DIR)
    logger.info("Check %s exists=%s items=%s", STATIC_DIR, os.path.isdir(STATIC_DIR), _ls(STATIC_DIR))
    css_dir = os.path.join(STATIC_DIR, "css")
    css_file = os.path.join(css_dir, "domovra.css")
    logger.info(
        "Check %s exists=%s items=%s", css_dir, os.path.isdir(css_dir), _ls(css_dir)
    )
    logger.info(
        "CSS file %s exists=%s size=%s",
        css_file,
        os.path.isfile(css_file),
        os.path.getsize(css_file) if os.path.isfile(css_file) else "N/A",
    )
except Exception as e:  # pragma: no cover
    logger.exception("Static check failed: %s", e)


# ============================================================
# Montage des routers
# ============================================================

# Pages
app.include_router(home_router)
app.include_router(products_router)
app.include_router(locations_router)
app.include_router(lots_router)
app.include_router(achats_router)
app.include_router(journal_router)
app.include_router(support_router)
app.include_router(settings_router)
app.include_router(shopping_router)

# Technique / API
app.include_router(api_router)
app.include_router(debug_router)
app.include_router(admin_db_router)
app.include_router(ha_router)
app.include_router(export_import_router)
app.include_router(print_router)


# ============================================================
# Lifecycle
# ============================================================

async def _ha_push_loop() -> None:
    """Push initial au démarrage, puis toutes les 5 minutes en boucle."""
    from services.ha_entities import refresh_and_push
    try:
        refresh_and_push()
    except Exception as e:
        logger.warning("HA push initial error: %s", e)
    while True:
        await asyncio.sleep(300)
        try:
            refresh_and_push()
        except Exception as e:
            logger.warning("HA push loop error: %s", e)


@app.on_event("startup")
async def _startup() -> None:
    logger.info("Domovra starting. DB_PATH=%s", DB_PATH)

    warn, crit = get_retention_thresholds()
    logger.info("Retention thresholds: WARNING_DAYS=%s  CRITICAL_DAYS=%s", warn, crit)

    init_db()
    _ensure_events_table()

    try:
        from settings_store import load_settings
        current = load_settings()
        app.state.settings = current
        logger.info("Settings au démarrage: %s", current)
    except Exception as e:  # pragma: no cover
        logger.exception("Erreur lecture settings au démarrage: %s", e)

    asyncio.create_task(_ha_push_loop())
