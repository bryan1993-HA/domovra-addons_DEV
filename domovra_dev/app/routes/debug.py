# domovra/app/routes/debug.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from config import DB_PATH

router = APIRouter()


# ========= DB helper =========
def _conn() -> sqlite3.Connection:
    """Open a SQLite connection with dict-style row access."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _require_ingress(request: Request) -> None:
    """Bloque l'accès si la requête ne passe pas par HA Ingress.

    HA Ingress injecte le header X-Ingress-Path sur chaque requête proxifiée.
    Sans ce header (accès direct port 8098), les routes debug sont refusées.
    """
    if not request.headers.get("x-ingress-path"):
        raise HTTPException(
            status_code=403,
            detail="Accès refusé : ces routes sont réservées à l'accès via HA Ingress.",
        )


# ========= Endpoints =========
@router.get("/_debug/vars", dependencies=[Depends(_require_ingress)])
def debug_vars(request: Request) -> Dict[str, Any]:
    """
    Retourne quelques infos utiles pour vérifier les assets/CSS :
    - chemin du répertoire static
    - listing des fichiers présents
    - chemin du CSS versionné injecté dans Jinja
    """
    # …/app/routes -> …/app
    app_dir = Path(__file__).resolve().parent.parent
    static_dir = app_dir / "static"

    templates = request.app.state.templates
    asset_css = templates.globals.get("ASSET_CSS_PATH")

    def _ls(p: Path) -> List[str]:
        try:
            return sorted([x.name for x in p.iterdir()])
        except Exception:
            return []

    return {
        "ASSET_CSS_PATH": asset_css,
        "STATIC_DIR": str(static_dir),
        "ls_static": _ls(static_dir),
        "ls_css": _ls(static_dir / "css"),
    }


@router.get("/debug/db", dependencies=[Depends(_require_ingress)])
def debug_db() -> JSONResponse:
    """
    Dump léger : liste les tables (hors sqlite_*) et
    jusqu'à 5 lignes par table, pour inspection rapide.
    """
    out: List[Dict[str, Any]] = []
    with _conn() as c:
        tables = [
            r["name"]
            for r in c.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for t in tables:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM {t} LIMIT 5")]
            out.append({
                "table": t,
                "columns": list(rows[0].keys()) if rows else [],
                "rows": rows,
            })
    return JSONResponse(out)
