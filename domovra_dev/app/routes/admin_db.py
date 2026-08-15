# ===============================================
# Admin DB — version simple (list, view, export)
# ===============================================
from __future__ import annotations

import csv
import io
import re
import sqlite3
from typing import Any, List

from fastapi import APIRouter, Depends, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from config import DB_PATH
from utils.http import ingress_base, render as render_with_env

router = APIRouter()

# Regex : noms de table/colonne SQLite valides (lettres, chiffres, _)
_SAFE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# Préfixes dangereux pour l'injection de formules CSV
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _validate_ident(name: str, label: str = "identifiant") -> str:
    """Lève une HTTPException 400 si le nom n'est pas un identifiant SQLite valide."""
    if not name or not _SAFE_IDENT.match(name):
        raise HTTPException(status_code=400, detail=f"{label} invalide : '{name}'")
    return name


def _sanitize_csv_cell(value: Any) -> str:
    """Préfixe les valeurs dangereuses (formules CSV) avec une apostrophe."""
    s = "" if value is None else str(value)
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s
    return s


def _require_ingress(request: Request) -> None:
    """Bloque l'accès si la requête ne passe pas par HA Ingress."""
    if not request.headers.get("x-ingress-path"):
        raise HTTPException(
            status_code=403,
            detail="Accès refusé : ces routes sont réservées à l'accès via HA Ingress.",
        )


@router.get("/admin/db", response_class=HTMLResponse, dependencies=[Depends(_require_ingress)])
async def admin_db_home(request: Request):
    with _conn() as c:
        rows = c.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()
        tables = [r["name"] for r in rows]

    return render_with_env(
        request.app.state.templates,
        "admin/db_list.html",
        request=request,
        BASE=ingress_base(request),
        tables=tables,
        db_path=DB_PATH,
        title="Admin · Base de données",
    )

@router.get("/admin/db/table/{table}", response_class=HTMLResponse, dependencies=[Depends(_require_ingress)])
async def admin_db_table(
    request: Request,
    table: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    order_by: str | None = Query(None),
    desc: bool = Query(True),
):
    _validate_ident(table, "Nom de table")

    with _conn() as c:
        # Vérifie l'existence dans sqlite_master (paramétrisé)
        exists = c.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()["n"]
        if not exists:
            raise HTTPException(status_code=404, detail=f"Table '{table}' introuvable")

        # Colonnes — table déjà validée par _validate_ident
        cols_rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        columns = [r["name"] for r in cols_rows]

        # Tri (fallback sur rowid) — valide que order_by est une colonne réelle
        order = order_by if (order_by and order_by in columns) else None
        if order:
            _validate_ident(order, "Colonne de tri")
        order_sql = f" ORDER BY {order} {'DESC' if desc else 'ASC'}" if order else " ORDER BY rowid DESC"

        # Pagination
        total = c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        offset = (page - 1) * page_size

        rows = c.execute(
            f"SELECT * FROM {table}{order_sql} LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
        data: List[dict[str, Any]] = [dict(r) for r in rows]

    return render_with_env(
        request.app.state.templates,
        "admin/db_table.html",
        request=request,
        BASE=ingress_base(request),
        table=table,
        columns=columns,
        rows=data,
        page=page,
        page_size=page_size,
        total=total,
        order_by=order,
        desc=desc,
        title=f"Admin · {table}",
    )

@router.get("/admin/db/table/{table}/export.csv", dependencies=[Depends(_require_ingress)])
async def admin_db_export_csv(
    request: Request,
    table: str,
    order_by: str | None = Query(None),
    desc: bool = Query(True),
):
    _validate_ident(table, "Nom de table")

    with _conn() as c:
        exists = c.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()["n"]
        if not exists:
            raise HTTPException(status_code=404, detail=f"Table '{table}' introuvable")

        cols_rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        columns = [r["name"] for r in cols_rows]

        order = order_by if (order_by and order_by in columns) else None
        if order:
            _validate_ident(order, "Colonne de tri")
        order_sql = f" ORDER BY {order} {'DESC' if desc else 'ASC'}" if order else " ORDER BY rowid DESC"

        rows = c.execute(f"SELECT * FROM {table}{order_sql}").fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow([_sanitize_csv_cell(r[col]) for col in columns])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={table}.csv"},
    )
