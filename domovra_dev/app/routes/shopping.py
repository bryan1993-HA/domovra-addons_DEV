# app/routes/shopping.py
import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse

from utils.http import ingress_base, render as render_with_env
from services.events import log_event  # journal Domovra

router = APIRouter(tags=["Shopping"])

DB_PATH = os.environ.get("DB_PATH", "/data/domovra.sqlite3")


# ---------- Helpers DB ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shopping_lists(
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          emoji TEXT NULL,
          color TEXT NULL,
          created_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shopping_items(
          id INTEGER PRIMARY KEY,
          list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
          product_id INTEGER NOT NULL REFERENCES products(id),
          qty REAL DEFAULT 1,
          unit TEXT NULL,
          note TEXT NULL,
          is_checked INTEGER DEFAULT 0,
          purchased_at TEXT NULL,
          store TEXT NULL,
          shelf_unit_price REAL NULL,
          ticket_unit_price REAL NULL,
          price_delta REAL NULL,
          position INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_list ON shopping_items(list_id, position);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_checked ON shopping_items(list_id, is_checked);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_product ON shopping_items(product_id);"
    )
    conn.commit()
    conn.close()


init_db()


# ---------- Queries ----------
def ensure_default_list(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM shopping_lists ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    if row:
        return row["id"]
    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO shopping_lists(name, emoji, color, created_at) VALUES(?,?,?,?)",
        ("Courses", "🛒", None, now),
    )
    conn.commit()
    return cur.lastrowid


def fetch_lists_with_counts(conn) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT L.id, L.name, L.emoji, L.color,
               SUM(CASE WHEN I.is_checked=0 THEN 1 ELSE 0 END) AS to_buy,
               COUNT(I.id) AS total
        FROM shopping_lists L
        LEFT JOIN shopping_items I ON I.list_id = L.id
        GROUP BY L.id
        ORDER BY L.id ASC;
        """
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_products(conn, q: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if q:
        qlike = f"%{q.strip()}%"
        cur.execute(
            """
            SELECT id, name, brand, unit, barcode
            FROM products
            WHERE name LIKE ? OR brand LIKE ? OR barcode LIKE ?
            ORDER BY name ASC
            LIMIT ?;
            """,
            (qlike, qlike, qlike, limit),
        )
    else:
        cur.execute(
            """
            SELECT id, name, brand, unit, barcode
            FROM products
            ORDER BY name ASC
            LIMIT ?;
            """,
            (limit,),
        )
    return [dict(r) for r in cur.fetchall()]


def fetch_items(conn, list_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    where_status = ""
    params: List[Any] = [list_id]
    if status == "todo":
        where_status = "AND I.is_checked=0"
    elif status == "done":
        where_status = "AND I.is_checked=1"
    cur.execute(
        f"""
        SELECT I.*, P.name AS product_name, P.brand AS product_brand, P.unit AS product_unit
        FROM shopping_items I
        JOIN products P ON P.id = I.product_id
        WHERE I.list_id = ?
        {where_status}
        ORDER BY I.is_checked ASC, I.position ASC, I.id ASC;
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def next_position(conn, list_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(position), 0) AS maxpos FROM shopping_items WHERE list_id=?;", (list_id,))
    row = cur.fetchone()
    return (row["maxpos"] or 0) + 1


def product_unit(conn, product_id: int) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT unit FROM products WHERE id=?;", (product_id,))
    row = cur.fetchone()
    return row["unit"] if row else None


# ---------- Routes ----------
@router.get("/shopping", response_class=HTMLResponse)
def page_shopping(
    request: Request,
    list_id: Optional[int] = Query(None, alias="list"),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),  # recherche simple client-side; q non utilisée server-side en V1
):
    conn = get_db()
    try:
        default_list_id = ensure_default_list(conn)
        if not list_id:
            list_id = default_list_id

        lists = fetch_lists_with_counts(conn)
        items = fetch_items(conn, list_id, status=status)
        # Prépare datalist produits (jusqu'à 200)
        products = fetch_products(conn, q=None, limit=200)

        return render_with_env(
            request,
            "shopping.html",
            {
                "BASE": ingress_base(request),
                "ACTIVE_LIST_ID": list_id,
                "LISTS": lists,
                "ITEMS": items,
                "PRODUCTS": products,
                "STATUS": status or "all",
            },
        )
    finally:
        conn.close()


# ----- Listes -----
@router.post("/shopping/list/create")
def create_list(request: Request, name: str = Form(...), emoji: str = Form(""), color: str = Form("")):
    conn = get_db()
    try:
        now = datetime.utcnow().isoformat()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO shopping_lists(name, emoji, color, created_at) VALUES(?,?,?,?)",
            (name.strip() or "Liste", emoji.strip() or None, color.strip() or None, now),
        )
        conn.commit()
        list_id = cur.lastrowid
        log_event("shopping", f"Création liste '{name}' (id={list_id})")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=added_list"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()


@router.post("/shopping/list/rename")
def rename_list(request: Request, list_id: int = Form(...), name: str = Form(...)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE shopping_lists SET name=? WHERE id=?", (name.strip(), list_id))
        conn.commit()
        log_event("shopping", f"Renommage liste id={list_id} → '{name}'")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=renamed_list"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()


@router.post("/shopping/list/delete")
def delete_list(request: Request, list_id: int = Form(...)):
    conn = get_db()
    try:
        # Trouver une autre liste pour rediriger après suppression
        cur = conn.cursor()
        cur.execute("SELECT id FROM shopping_lists WHERE id<>? ORDER BY id LIMIT 1;", (list_id,))
        other = cur.fetchone()
        target = other["id"] if other else None

        cur.execute("DELETE FROM shopping_lists WHERE id=?", (list_id,))
        conn.commit()
        log_event("shopping", f"Suppression liste id={list_id}")
        if not target:
            # recrée une liste par défaut si c'était la dernière
            target = ensure_default_list(conn)
        url = f"{ingress_base(request)}/shopping?list={target}&toast=deleted_list"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()


# ----- Items -----
@router.post("/shopping/item/add")
def add_item(
    request: Request,
    list_id: int = Form(...),
    product_id: int = Form(...),
    qty: float = Form(1),
    unit: Optional[str] = Form(None),
    note: str = Form(""),
):
    conn = get_db()
    try:
        # Défaut unité = unité du produit si non fournie
        if not unit:
            unit = product_unit(conn, product_id)

        pos = next_position(conn, list_id)
        now = datetime.utcnow().isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO shopping_items(list_id, product_id, qty, unit, note, is_checked, position, created_at)
            VALUES(?,?,?,?,?,0,?,?);
            """,
            (list_id, product_id, qty, unit, (note or "").strip() or None, pos, now),
        )
        conn.commit()
        log_event("shopping", f"Ajout item produit_id={product_id} liste_id={list_id} qty={qty} {unit or ''}")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=added_item"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()


@router.post("/shopping/item/toggle")
def toggle_item(request: Request, item_id: int = Form(...), list_id: int = Form(...)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_checked FROM shopping_items WHERE id=?", (item_id,))
        row = cur.fetchone()
        if not row:
            url = f"{ingress_base(request)}/shopping?list={list_id}&toast=error"
            return RedirectResponse(url, status_code=303)
        new_val = 0 if int(row["is_checked"] or 0) == 1 else 1
        cur.execute("UPDATE shopping_items SET is_checked=? WHERE id=?", (new_val, item_id))
        conn.commit()
        log_event("shopping", f"Toggle item id={item_id} → {new_val}")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=toggled"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()


@router.post("/shopping/item/delete")
def delete_item(request: Request, item_id: int = Form(...), list_id: int = Form(...)):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM shopping_items WHERE id=?", (item_id,))
        conn.commit()
        log_event("shopping", f"Suppression item id={item_id}")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=deleted_item"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()
