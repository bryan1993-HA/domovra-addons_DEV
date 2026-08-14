# domovra/app/routes/export_import.py
"""
Export CSV (produits + lots) et import CSV avec gestion des doublons.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import List, Dict, Any

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse

from db import (
    _conn,
    list_products, list_lots, list_locations,
    add_location,
)

logger = logging.getLogger("domovra.export_import")
router = APIRouter(prefix="/data", tags=["export-import"])

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 Mo


# ─── EXPORT ──────────────────────────────────────────────────────────────────

@router.get("/export/products.csv")
def export_products():
    """Télécharge tous les produits en CSV."""
    products = list_products()
    fields = ["id", "name", "unit", "barcode", "min_qty",
              "default_shelf_life_days", "default_freeze_shelf_days",
              "category", "description", "no_expiry", "no_freeze"]

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore",
                       lineterminator="\r\n")
    w.writeheader()
    for p in products:
        w.writerow({f: (p.get(f) or "") for f in fields})

    content = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=domovra_produits.csv"},
    )


@router.get("/export/lots.csv")
def export_lots():
    """Télécharge tous les lots ouverts en CSV."""
    lots = list_lots()
    fields = ["id", "product", "name", "location", "qty", "unit",
              "best_before", "frozen_on", "created_on",
              "brand", "ean", "store", "price_total"]

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore",
                       lineterminator="\r\n")
    w.writeheader()
    for lot in lots:
        w.writerow({f: (lot.get(f) or "") for f in fields})

    content = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=domovra_lots.csv"},
    )


# ─── IMPORT HELPERS ──────────────────────────────────────────────────────────

def _parse_csv(raw: bytes) -> List[Dict[str, str]]:
    """Parse CSV bytes (auto-detect BOM + delimiter)."""
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [dict(row) for row in reader]


def _str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _float_or_none(v: Any):
    s = _str(v)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int_or_none(v: Any):
    s = _str(v)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _bool_field(v: Any) -> int:
    s = _str(v).lower()
    return 1 if s in ("1", "true", "oui", "yes", "on") else 0


# ─── IMPORT PRODUITS ─────────────────────────────────────────────────────────

@router.post("/import/products")
async def import_products(
    file: UploadFile = File(...),
    on_conflict: str = Form("skip"),
):
    """
    Importe des produits depuis un CSV.
    on_conflict=skip  → ignorer les lignes dont le nom/barcode existe déjà
    on_conflict=update → mettre à jour les produits existants
    """
    raw = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(raw) > MAX_UPLOAD_SIZE:
        return JSONResponse(
            {"ok": False, "error": "Fichier trop volumineux (max 10 Mo)."},
            status_code=413,
        )

    try:
        rows = _parse_csv(raw)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Impossible de lire le CSV : {e}"}, status_code=400)

    if not rows:
        return JSONResponse({"ok": False, "error": "Fichier vide ou format invalide."}, status_code=400)

    imported = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    with _conn() as c:
        # Index existants : name→id et barcode→id
        existing_by_name: Dict[str, int] = {
            r["name"].strip().casefold(): r["id"]
            for r in c.execute("SELECT id, name FROM products").fetchall()
        }
        existing_by_barcode: Dict[str, int] = {
            r["barcode"].strip(): r["id"]
            for r in c.execute(
                "SELECT id, barcode FROM products WHERE barcode IS NOT NULL AND barcode != ''"
            ).fetchall()
        }

        for i, row in enumerate(rows, 1):
            name = _str(row.get("name"))
            if not name:
                errors.append(f"Ligne {i} : nom manquant, ignorée.")
                continue

            barcode = _str(row.get("barcode")) or None

            # Détection doublon
            existing_id = None
            bc = barcode.strip() if barcode else None
            if bc and bc in existing_by_barcode:
                existing_id = existing_by_barcode[bc]
            elif name.casefold() in existing_by_name:
                existing_id = existing_by_name[name.casefold()]

            if existing_id is not None:
                if on_conflict == "update":
                    try:
                        # Met à jour les champs fournis (non vides)
                        sets: List[str] = []
                        params: List[Any] = []

                        def _add(col: str, val: Any):
                            if val is not None and str(val) != "":
                                sets.append(f"{col}=?")
                                params.append(val)

                        _add("name", name)
                        _add("unit", _str(row.get("unit")) or None)
                        _add("barcode", barcode)
                        _add("min_qty", _float_or_none(row.get("min_qty")))
                        _add("default_shelf_life_days", _int_or_none(row.get("default_shelf_life_days")))
                        _add("default_freeze_shelf_days", _int_or_none(row.get("default_freeze_shelf_days")))
                        _add("category", _str(row.get("category")) or None)
                        _add("description", _str(row.get("description")) or None)

                        if "no_expiry" in row:
                            sets.append("no_expiry=?")
                            params.append(_bool_field(row["no_expiry"]))
                        if "no_freeze" in row:
                            sets.append("no_freeze=?")
                            params.append(_bool_field(row["no_freeze"]))

                        if sets:
                            params.append(existing_id)
                            c.execute(
                                f"UPDATE products SET {', '.join(sets)} WHERE id=?",
                                params,
                            )
                        updated += 1
                    except Exception as e:
                        errors.append(f"Ligne {i} ({name}) : erreur mise à jour — {e}")
                else:
                    skipped += 1
                continue

            # Nouveau produit
            try:
                unit = _str(row.get("unit")) or "pièce"
                shelf = _int_or_none(row.get("default_shelf_life_days")) or 90
                freeze_shelf = _int_or_none(row.get("default_freeze_shelf_days"))
                min_qty = _float_or_none(row.get("min_qty"))
                category = _str(row.get("category")) or None
                description = _str(row.get("description")) or None
                no_expiry = _bool_field(row.get("no_expiry", "0"))
                no_freeze = _bool_field(row.get("no_freeze", "0"))

                c.execute(
                    """INSERT INTO products
                       (name, unit, default_shelf_life_days, barcode, min_qty,
                        description, category, default_freeze_shelf_days, no_expiry, no_freeze,
                        low_stock_enabled, expiry_kind)
                       VALUES (?,?,?,?,?,?,?,?,?,?,1,'DLC')""",
                    (name, unit, shelf, barcode, min_qty,
                     description, category, freeze_shelf, no_expiry, no_freeze),
                )
                imported += 1
                # Mise à jour de l'index local pour les lignes suivantes
                existing_by_name[name.casefold()] = c.lastrowid or 0
                if barcode:
                    existing_by_barcode[barcode] = c.lastrowid or 0
            except Exception as e:
                errors.append(f"Ligne {i} ({name}) : {e}")

        c.commit()

    return JSONResponse({
        "ok": True,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    })


# ─── IMPORT LOTS ─────────────────────────────────────────────────────────────

@router.post("/import/lots")
async def import_lots(
    file: UploadFile = File(...),
    on_conflict: str = Form("skip"),
):
    """
    Importe des lots depuis un CSV.
    Colonnes attendues : product_name (ou product), product_barcode (optionnel),
                         location, qty, best_before (optionnel), frozen_on (optionnel)
    on_conflict ignoré ici (les lots ne sont pas des singletons — on crée toujours).
    """
    raw = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(raw) > MAX_UPLOAD_SIZE:
        return JSONResponse(
            {"ok": False, "error": "Fichier trop volumineux (max 10 Mo)."},
            status_code=413,
        )

    try:
        rows = _parse_csv(raw)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Impossible de lire le CSV : {e}"}, status_code=400)

    if not rows:
        return JSONResponse({"ok": False, "error": "Fichier vide ou format invalide."}, status_code=400)

    imported = 0
    errors: List[str] = []
    today = date.today().isoformat()

    with _conn() as c:
        # Index produits : name→id et barcode→id
        products_by_name: Dict[str, int] = {
            r["name"].strip().casefold(): r["id"]
            for r in c.execute("SELECT id, name FROM products").fetchall()
        }
        products_by_barcode: Dict[str, int] = {
            r["barcode"].strip(): r["id"]
            for r in c.execute(
                "SELECT id, barcode FROM products WHERE barcode IS NOT NULL AND barcode != ''"
            ).fetchall()
        }
        # Index emplacements : name→id
        locations_by_name: Dict[str, int] = {
            r["name"].strip().casefold(): r["id"]
            for r in c.execute("SELECT id, name FROM locations").fetchall()
        }

        for i, row in enumerate(rows, 1):
            # Résolution produit
            pname = _str(row.get("product_name") or row.get("product") or "")
            pbarcode = _str(row.get("product_barcode") or row.get("barcode") or "")

            product_id: int | None = None
            if pbarcode and pbarcode in products_by_barcode:
                product_id = products_by_barcode[pbarcode]
            elif pname and pname.casefold() in products_by_name:
                product_id = products_by_name[pname.casefold()]

            if product_id is None:
                errors.append(
                    f"Ligne {i} : produit introuvable "
                    f"(nom={pname!r}, barcode={pbarcode!r}). "
                    "Créez d'abord le produit."
                )
                continue

            # Résolution / création emplacement
            loc_name = _str(row.get("location") or "")
            if not loc_name:
                errors.append(f"Ligne {i} : emplacement manquant, ignorée.")
                continue

            location_id = locations_by_name.get(loc_name.casefold())
            if location_id is None:
                # Crée l'emplacement à la volée
                try:
                    new_loc_id = add_location(loc_name)
                    location_id = new_loc_id
                    locations_by_name[loc_name.casefold()] = new_loc_id
                except Exception as e:
                    errors.append(f"Ligne {i} : impossible de créer l'emplacement {loc_name!r} — {e}")
                    continue

            # Quantité
            qty = _float_or_none(row.get("qty"))
            if qty is None or qty <= 0:
                errors.append(f"Ligne {i} : quantité invalide ({row.get('qty')!r}), ignorée.")
                continue

            best_before = _str(row.get("best_before")) or None
            frozen_on = _str(row.get("frozen_on")) or None

            try:
                cur = c.execute(
                    """INSERT INTO stock_lots
                       (product_id, location_id, qty, frozen_on, best_before,
                        created_on, initial_qty, status)
                       VALUES (?,?,?,?,?,?,?,'open')""",
                    (product_id, location_id, qty, frozen_on, best_before, today, qty),
                )
                lot_id = cur.lastrowid
                c.execute(
                    "INSERT INTO movements(lot_id,type,qty,ts,note) VALUES(?,?,?,?,?)",
                    (lot_id, "IN", qty, today, "import CSV"),
                )
                imported += 1
            except Exception as e:
                errors.append(f"Ligne {i} : {e}")

        c.commit()

    return JSONResponse({
        "ok": True,
        "imported": imported,
        "skipped": 0,
        "errors": errors,
    })
