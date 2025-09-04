# app/routes/shopping.py
import os
import sqlite3
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse

from utils.http import ingress_base, render as render_with_env
from services.events import log_event  # journal Domovra

router = APIRouter(tags=["Shopping"])

DB_PATH = os.environ.get("DB_PATH", "/data/domovra.sqlite3")


# --- Compat renderer (2 ou 3 arguments) ---
def safe_render(request: Request, template_name: str, context: dict):
    """
    Rend la template quelle que soit la signature de render_with_env :
    - (request, template, context)
    - (request, template, data=context)
    - (request, template)  -> push le contexte dans request.state
    """
    try:
        return render_with_env(request, template_name, context)
    except TypeError:
        try:
            return render_with_env(request, template_name, data=context)  # type: ignore
        except TypeError:
            for k, v in context.items():
                setattr(request.state, k, v)
            return render_with_env(request, template_name)  # type: ignore


# ---------- Helpers DB ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    cols = {r[1] for r in cur.fetchall()}  # r[1] = name
    return column in cols

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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_list ON shopping_items(list_id, position);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_checked ON shopping_items(list_id, is_checked);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_product ON shopping_items(product_id);")
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
    has_barcode = table_has_column(conn, "products", "barcode")
    has_unit = table_has_column(conn, "products", "unit")

    select_cols = ["id", "name"]
    if has_unit: select_cols.append("unit")
    if has_barcode: select_cols.append("barcode")

    where_parts = ["name LIKE ?"]
    params: List[Any] = []
    if q:
        qlike = f"%{q.strip()}%"
        params.append(qlike)
        if has_barcode:
            where_parts.append("barcode LIKE ?")
            params.append(qlike)
        where_sql = " WHERE " + " OR ".join(where_parts)
    else:
        where_sql = ""

    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM products
        {where_sql}
        ORDER BY name ASC
        LIMIT ?;
    """
    params.append(limit)

    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]

    # Normalise pour template
    for r in rows:
        r.setdefault("unit", None)
        r.setdefault("barcode", None)
    return rows

def fetch_items(conn, list_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    has_unit = table_has_column(conn, "products", "unit")
    unit_sel = ", P.unit AS product_unit" if has_unit else ""

    where_status = ""
    params: List[Any] = [list_id]
    if status == "todo":
        where_status = "AND I.is_checked=0"
    elif status == "done":
        where_status = "AND I.is_checked=1"

    sql = f"""
        SELECT I.*,
               P.name AS product_name
               {unit_sel}
        FROM shopping_items I
        JOIN products P ON P.id = I.product_id
        WHERE I.list_id = ?
        {where_status}
        ORDER BY I.is_checked ASC, I.position ASC, I.id ASC;
    """
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r.setdefault("product_unit", None)
    return rows

def fetch_purchased_today(conn, list_id: int) -> List[Dict[str, Any]]:
    """Items cochés aujourd'hui (pour le contrôle ticket)."""
    today = date.today().isoformat()
    has_unit = table_has_column(conn, "products", "unit")
    unit_sel = ", P.unit AS product_unit" if has_unit else ""

    sql = f"""
        SELECT I.*,
               P.name AS product_name
               {unit_sel}
        FROM shopping_items I
        JOIN products P ON P.id = I.product_id
        WHERE I.list_id = ?
          AND I.is_checked = 1
          AND I.purchased_at IS NOT NULL
          AND substr(I.purchased_at, 1, 10) = ?
        ORDER BY I.position ASC, I.id ASC;
    """
    cur = conn.cursor()
    cur.execute(sql, (list_id, today))
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r.setdefault("product_unit", None)
        s = r.get("shelf_unit_price")
        t = r.get("ticket_unit_price")
        if s is not None and t is not None:
            r["computed_delta"] = float(t) - float(s)
        else:
            r["computed_delta"] = None
    return rows

def next_position(conn, list_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(position), 0) AS maxpos FROM shopping_items WHERE list_id=?;", (list_id,))
    row = cur.fetchone()
    return (row["maxpos"] or 0) + 1

def product_unit(conn, product_id: int) -> Optional[str]:
    if not table_has_column(conn, "products", "unit"):
        return None
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
    q: Optional[str] = Query(None),
):
    conn = get_db()
    try:
        default_list_id = ensure_default_list(conn)
        if not list_id:
            list_id = default_list_id

        lists = fetch_lists_with_counts(conn)
        items = fetch_items(conn, list_id, status=status)
        products = fetch_products(conn, q=None, limit=200)
        purchased_today = fetch_purchased_today(conn, list_id)

        # Totaux contrôle
        anomalies = 0
        total_delta = 0.0
        for r in purchased_today:
            d = r.get("price_delta")
            if d is None:
                d = r.get("computed_delta")
            if d is not None:
                total_delta += float(d)
                if abs(float(d)) > 0.009:  # > 1 centime
                    anomalies += 1

        return safe_render(
            request,
            "shopping.html",
            {
                "BASE": ingress_base(request),
                "ACTIVE_LIST_ID": list_id,
                "LISTS": lists,
                "ITEMS": items,
                "PRODUCTS": products,
                "STATUS": status or "all",
                "PURCHASED_TODAY": purchased_today,
                "ANOMALIES": anomalies,
                "TOTAL_DELTA": round(total_delta, 2),
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
        cur = conn.cursor()
        cur.execute("SELECT id FROM shopping_lists WHERE id<>? ORDER BY id LIMIT 1;", (list_id,))
        other = cur.fetchone()
        target = other["id"] if other else None

        cur.execute("DELETE FROM shopping_lists WHERE id=?", (list_id,))
        conn.commit()
        log_event("shopping", f"Suppression liste id={list_id}")
        if not target:
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
    """Cocher/décocher. Si on décoche : on nettoie les infos d'achat."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_checked FROM shopping_items WHERE id=?", (item_id,))
        row = cur.fetchone()
        if not row:
            url = f"{ingress_base(request)}/shopping?list={list_id}&toast=error"
            return RedirectResponse(url, status_code=303)
        was_checked = int(row["is_checked"] or 0) == 1
        if was_checked:
            # On repasse en "à acheter" => purge des champs achat
            cur.execute(
                """
                UPDATE shopping_items
                   SET is_checked=0,
                       purchased_at=NULL,
                       store=NULL,
                       shelf_unit_price=NULL,
                       ticket_unit_price=NULL,
                       price_delta=NULL
                 WHERE id=?;
                """,
                (item_id,),
            )
        else:
            cur.execute(
                "UPDATE shopping_items SET is_checked=1, purchased_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), item_id),
            )
        conn.commit()
        log_event("shopping", f"Toggle item id={item_id} → {0 if was_checked else 1}")
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

@router.post("/shopping/item/mark_bought")
def mark_bought(
    request: Request,
    item_id: int = Form(...),
    list_id: int = Form(...),
    store: str = Form(""),
    shelf_unit_price: Optional[float] = Form(None),
):
    """Depuis le magasin : noter prix rayon + magasin et marquer 'acheté'."""
    conn = get_db()
    try:
        now = datetime.utcnow().isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE shopping_items
               SET is_checked=1,
                   purchased_at=?,
                   store=?,
                   shelf_unit_price=?
             WHERE id=?;
            """,
            (now, (store or None), shelf_unit_price, item_id),
        )
        conn.commit()
        log_event("shopping", f"Achat item id={item_id} store='{store}' shelf={shelf_unit_price}")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=marked_bought"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()

@router.post("/shopping/item/ticket_price")
def ticket_price(
    request: Request,
    item_id: int = Form(...),
    list_id: int = Form(...),
    ticket_unit_price: float = Form(...),
):
    """Après caisse : saisir le prix ticket, calculer l'écart et garder la trace."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT shelf_unit_price FROM shopping_items WHERE id=?", (item_id,))
        row = cur.fetchone()
        shelf = float(row["shelf_unit_price"]) if row and row["shelf_unit_price"] is not None else 0.0
        delta = float(ticket_unit_price) - shelf
        cur.execute(
            """
            UPDATE shopping_items
               SET ticket_unit_price=?,
                   price_delta=?
             WHERE id=?;
            """,
            (ticket_unit_price, delta, item_id),
        )
        conn.commit()
        log_event("shopping", f"Ticket item id={item_id} ticket={ticket_unit_price} delta={delta:+.2f}")
        url = f"{ingress_base(request)}/shopping?list={list_id}&toast=ticket_ok"
        return RedirectResponse(url, status_code=303)
    finally:
        conn.close()
