# domovra/app/routes/api.py
# ============================================================
# API REST — utilisable depuis Home Assistant sans intégration
# supplémentaire (rest_command, sensor rest, automatisations).
#
# Endpoints disponibles :
#
#   Lecture
#   -------
#   GET  /api/ha/summary                  → compteurs globaux (via ha.py)
#   GET  /api/stock/products              → liste produits + stocks
#   GET  /api/stock/low                   → produits sous le seuil min
#   GET  /api/product-info?product_id=X   → détail produit + lots FIFO
#   GET  /api/product/by_barcode?code=X   → recherche par EAN (multi-EAN)
#
#   Multi-EAN (#10)
#   ---------------
#   GET    /api/product/{id}/barcodes     → liste EAN du produit
#   POST   /api/product/{id}/barcodes     → ajouter un EAN
#   DELETE /api/product/barcodes/{bc_id}  → supprimer un EAN
#
#   Écriture (automatisations HA)
#   -----------------------------
#   POST /api/stock/consume-product       → consomme en FIFO par product_id
#   POST /api/stock/consume-lot           → consomme un lot précis par lot_id
#   POST /api/stock/add-lot               → ajoute du stock (livraison, etc.)
#
#   Interne / Scanner
#   -----------------
#   GET  /api/off?barcode=X               → recherche Open Food Facts
#   POST /api/log                         → journalisation depuis le front
# ============================================================
from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Body, Path
from fastapi.responses import JSONResponse

from config import DB_PATH, get_retention_thresholds
from db import (
    list_products,
    list_lots,
    consume_lot,
    add_lot,
    list_low_stock_products,
    get_product_barcodes,
    add_product_barcode,
    delete_product_barcode,
    find_product_by_barcode,
)
from services.events import log_event
from services.ha_entities import schedule_ha_push

router = APIRouter()
log = logging.getLogger("domovra.api")


# ─────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────

def _first_non_empty(*vals: Optional[str]) -> Optional[str]:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _fifo_key(lot: dict):
    bb = lot.get("best_before")
    return ("~", "") if not bb else ("", str(bb))


def _push_ha():
    """Déclenche un push HA après modification du stock (best-effort)."""
    try:
        schedule_ha_push()
    except Exception:
        pass


# ─────────────────────────────────────────────
# GET /api/stock/products
# ─────────────────────────────────────────────

@router.get("/api/stock/products")
def api_stock_products() -> JSONResponse:
    """
    Retourne la liste de tous les produits avec leur stock courant.

    Réponse :
        {
          "count": 12,
          "products": [
            {
              "id": 1,
              "name": "Pellets",
              "unit": "kg",
              "qty_total": 40.0,
              "lots_count": 3,
              "min_qty": 20.0,
              "low_stock": false,
              "category": "Surgelés",
              "barcode": "3017620425035"
            },
            ...
          ]
        }

    Exemple Home Assistant (sensor REST) :
        sensor:
          - platform: rest
            name: "Domovra produits"
            resource: "http://localhost:8098/api/stock/products"
            value_template: "{{ value_json.count }}"
            json_attributes:
              - products
    """
    products = list_products() or []
    lots = list_lots() or []

    qty_by_pid: Dict[int, float] = {}
    lots_by_pid: Dict[int, int] = {}
    for l in lots:
        pid = int(l.get("product_id") or 0)
        qty_by_pid[pid] = qty_by_pid.get(pid, 0.0) + float(l.get("qty") or 0)
        lots_by_pid[pid] = lots_by_pid.get(pid, 0) + 1

    result = []
    for p in products:
        pid = int(p["id"])
        qty = qty_by_pid.get(pid, 0.0)
        min_qty = p.get("min_qty")
        low = (min_qty is not None and qty <= float(min_qty))
        result.append({
            "id": pid,
            "name": p["name"],
            "unit": p.get("unit") or "pièce",
            "qty_total": round(qty, 3),
            "lots_count": lots_by_pid.get(pid, 0),
            "min_qty": min_qty,
            "low_stock": low,
            "category": p.get("category") or "",
            "barcode": p.get("barcode") or "",
        })

    result.sort(key=lambda x: x["name"].lower())
    return JSONResponse({"count": len(result), "products": result})


# ─────────────────────────────────────────────
# GET /api/stock/low
# ─────────────────────────────────────────────

@router.get("/api/stock/low")
def api_stock_low(limit: int = Query(20, ge=1, le=100)) -> JSONResponse:
    """
    Retourne les produits dont le stock est en-dessous du seuil minimum.

    Paramètres :
        limit (int, optionnel) : nombre max de résultats (défaut 20, max 100)

    Réponse :
        {
          "count": 2,
          "products": [
            {
              "id": 5,
              "name": "Pellets",
              "unit": "kg",
              "qty_total": 12.0,
              "min_qty": 20.0,
              "delta": -8.0
            },
            ...
          ]
        }

    Exemple Home Assistant (sensor REST) :
        sensor:
          - platform: rest
            name: "Domovra ruptures"
            resource: "http://localhost:8098/api/stock/low"
            value_template: "{{ value_json.count }}"
            json_attributes:
              - products

    Exemple automatisation (notification si rupture) :
        trigger:
          - platform: state
            entity_id: sensor.domovra_ruptures
        condition:
          - condition: template
            value_template: "{{ states('sensor.domovra_ruptures') | int > 0 }}"
        action:
          - service: notify.mobile_app
            data:
              message: "⚠️ {{ states('sensor.domovra_ruptures') }} produit(s) en rupture"
    """
    low = list_low_stock_products(limit=limit) or []
    result = []
    for p in low:
        result.append({
            "id": int(p["id"]),
            "name": p["name"],
            "unit": p.get("unit") or "pièce",
            "qty_total": round(float(p.get("qty_total") or 0), 3),
            "min_qty": p.get("min_qty"),
            "delta": round(float(p.get("delta") or 0), 3),
        })
    return JSONResponse({"count": len(result), "products": result})


# ─────────────────────────────────────────────
# POST /api/stock/consume-product
# ─────────────────────────────────────────────

@router.post("/api/stock/consume-product")
def api_consume_product(
    product_id: int = Body(..., embed=True, ge=1),
    qty: float = Body(..., embed=True, gt=0),
    reason: Optional[str] = Body(None, embed=True),
) -> JSONResponse:
    """
    Consomme une quantité d'un produit en mode FIFO (lot avec la DLC la plus proche en premier).
    Si un lot est épuisé, passe automatiquement au suivant.

    Corps JSON :
        {
          "product_id": 5,
          "qty": 1.0,
          "reason": "automatisation lave-vaisselle"   // optionnel
        }

    Réponse succès :
        {
          "ok": true,
          "consumed": 1.0,
          "remaining_to_consume": 0.0,
          "lots_affected": [{"lot_id": 12, "consumed": 1.0, "remaining": 4.0}]
        }

    Réponse erreur :
        { "ok": false, "error": "product not found" }

    Exemple Home Assistant (rest_command) :
        rest_command:
          domovra_consume_product:
            url: "http://localhost:8098/api/stock/consume-product"
            method: POST
            headers:
              Content-Type: application/json
            payload: >
              {"product_id": {{ product_id }}, "qty": {{ qty }}}

    Exemple script HA (consommer 1 sac de pellets) :
        script:
          consommer_pellets:
            sequence:
              - service: rest_command.domovra_consume_product
                data:
                  product_id: 5
                  qty: 1
    """
    all_lots = list_lots() or []
    lots = [l for l in all_lots if int(l.get("product_id") or 0) == product_id]
    lots = [l for l in lots if float(l.get("qty") or 0) > 0]

    if not lots:
        products = list_products() or []
        if not any(int(p["id"]) == product_id for p in products):
            return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)
        return JSONResponse({"ok": False, "error": "no stock available"}, status_code=409)

    lots_sorted = sorted(lots, key=_fifo_key)
    remaining = float(qty)
    affected: List[Dict[str, Any]] = []

    for lot in lots_sorted:
        if remaining <= 0:
            break
        lot_id = int(lot["id"])
        lot_qty = float(lot.get("qty") or 0)
        to_consume = min(remaining, lot_qty)
        try:
            consume_lot(lot_id, to_consume, reason=reason)
        except Exception as e:
            log.error("consume_product: erreur lot_id=%s: %s", lot_id, e)
            return JSONResponse({"ok": False, "error": f"db error: {e}"}, status_code=500)
        affected.append({
            "lot_id": lot_id,
            "consumed": round(to_consume, 6),
            "remaining": round(max(0.0, lot_qty - to_consume), 6),
        })
        remaining -= to_consume

    log_event("api.consume_product", {
        "product_id": product_id,
        "qty_requested": qty,
        "qty_consumed": round(float(qty) - remaining, 6),
        "lots_affected": [a["lot_id"] for a in affected],
        "reason": reason,
    })
    _push_ha()

    return JSONResponse({
        "ok": True,
        "consumed": round(float(qty) - remaining, 6),
        "remaining_to_consume": round(max(0.0, remaining), 6),
        "lots_affected": affected,
    })


# ─────────────────────────────────────────────
# POST /api/stock/consume-lot
# ─────────────────────────────────────────────

@router.post("/api/stock/consume-lot")
def api_consume_lot(
    lot_id: int = Body(..., embed=True, ge=1),
    qty: float = Body(..., embed=True, gt=0),
    reason: Optional[str] = Body(None, embed=True),
) -> JSONResponse:
    """
    Consomme une quantité précise d'un lot identifié par son lot_id.
    Utilisez /api/product-info pour obtenir les lot_id disponibles.

    Corps JSON :
        {
          "lot_id": 42,
          "qty": 0.5,
          "reason": "consommation manuelle"   // optionnel
        }

    Réponse succès :
        {
          "ok": true,
          "lot_id": 42,
          "before": 2.0,
          "consumed": 0.5,
          "after": 1.5,
          "closed": false
        }

    Exemple Home Assistant (rest_command) :
        rest_command:
          domovra_consume_lot:
            url: "http://localhost:8098/api/stock/consume-lot"
            method: POST
            headers:
              Content-Type: application/json
            payload: >
              {"lot_id": {{ lot_id }}, "qty": {{ qty }}}
    """
    all_lots = list_lots() or []
    lot = next((l for l in all_lots if int(l.get("id") or 0) == lot_id), None)

    if not lot:
        return JSONResponse({"ok": False, "error": "lot not found"}, status_code=404)

    before = float(lot.get("qty") or 0)
    to_consume = min(float(qty), before)

    try:
        consume_lot(lot_id, to_consume, reason=reason)
    except Exception as e:
        log.error("api_consume_lot: erreur lot_id=%s: %s", lot_id, e)
        return JSONResponse({"ok": False, "error": f"db error: {e}"}, status_code=500)

    after = round(max(0.0, before - to_consume), 6)
    log_event("api.consume_lot", {
        "lot_id": lot_id,
        "product_id": lot.get("product_id"),
        "qty_requested": qty,
        "qty_consumed": round(to_consume, 6),
        "reason": reason,
    })
    _push_ha()

    return JSONResponse({
        "ok": True,
        "lot_id": lot_id,
        "before": round(before, 6),
        "consumed": round(to_consume, 6),
        "after": after,
        "closed": after <= 0,
    })


# ─────────────────────────────────────────────
# POST /api/stock/add-lot
# ─────────────────────────────────────────────

@router.post("/api/stock/add-lot")
def api_add_lot(
    product_id: int = Body(..., embed=True, ge=1),
    location_id: int = Body(..., embed=True, ge=1),
    qty: float = Body(..., embed=True, gt=0),
    best_before: Optional[str] = Body(None, embed=True),
    frozen_on: Optional[str] = Body(None, embed=True),
) -> JSONResponse:
    """
    Ajoute un lot de stock pour un produit donné.
    Utile pour enregistrer une livraison depuis une automatisation HA.

    Corps JSON :
        {
          "product_id": 5,
          "location_id": 2,
          "qty": 10.0,
          "best_before": "2025-12-31",   // optionnel
          "frozen_on": null               // optionnel
        }

    Réponse succès :
        {
          "ok": true,
          "lot_id": 87,
          "product_id": 5,
          "location_id": 2,
          "qty": 10.0
        }

    Astuce : obtenez les product_id via GET /api/stock/products
             et les location_id via GET /api/stock/products (champ location).

    Exemple Home Assistant (rest_command) :
        rest_command:
          domovra_add_lot:
            url: "http://localhost:8098/api/stock/add-lot"
            method: POST
            headers:
              Content-Type: application/json
            payload: >
              {
                "product_id": {{ product_id }},
                "location_id": {{ location_id }},
                "qty": {{ qty }}
              }

    Exemple script HA (livraison abonnement pellets) :
        script:
          livraison_pellets:
            sequence:
              - service: rest_command.domovra_add_lot
                data:
                  product_id: 5
                  location_id: 2
                  qty: 15
              - service: notify.mobile_app
                data:
                  message: "📦 15 kg de pellets ajoutés au stock"
    """
    products = list_products() or []
    if not any(int(p["id"]) == product_id for p in products):
        return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)

    # Validation date optionnelle
    import datetime
    for field_name, field_val in [("best_before", best_before), ("frozen_on", frozen_on)]:
        if field_val:
            try:
                datetime.date.fromisoformat(field_val)
            except ValueError:
                return JSONResponse(
                    {"ok": False, "error": f"{field_name}: format invalide (YYYY-MM-DD)"},
                    status_code=400,
                )

    try:
        lot_id = add_lot(
            product_id=product_id,
            location_id=location_id,
            qty=float(qty),
            frozen_on=frozen_on or None,
            best_before=best_before or None,
        )
    except Exception as e:
        log.error("api_add_lot: erreur product_id=%s: %s", product_id, e)
        return JSONResponse({"ok": False, "error": f"db error: {e}"}, status_code=500)

    log_event("api.add_lot", {
        "lot_id": lot_id,
        "product_id": product_id,
        "location_id": location_id,
        "qty": qty,
        "best_before": best_before,
        "frozen_on": frozen_on,
    })
    _push_ha()

    return JSONResponse({
        "ok": True,
        "lot_id": lot_id,
        "product_id": product_id,
        "location_id": location_id,
        "qty": qty,
    })


# ─────────────────────────────────────────────
# GET /api/product-info
# ─────────────────────────────────────────────

@router.get("/api/product-info")
def api_product_info(product_id: int = Query(..., ge=1)) -> JSONResponse:
    """
    Retourne le détail d'un produit : stock total, lots triés FIFO, marque, unité.
    Utilisé par le front pour la consommation rapide, et utilisable depuis HA.

    Paramètres :
        product_id (int) : identifiant du produit

    Réponse :
        {
          "product_id": 5,
          "unit": "kg",
          "brand": "",
          "total_qty": 40.0,
          "lots_count": 3,
          "fifo": {
            "lot_id": 12,
            "best_before": "2025-03-01",
            "location": "Cave"
          },
          "lots": [...]
        }
    """
    products = list_products() or []
    prod = next((p for p in products if int(p.get("id", 0)) == product_id), None)
    if not prod:
        return JSONResponse({"error": "not found"}, status_code=404)

    unit_prod = _first_non_empty(prod.get("unit"))
    brand_prod = _first_non_empty(prod.get("brand"), prod.get("brands"))

    all_lots = list_lots() or []
    lots = [l for l in all_lots if int(l.get("product_id", 0)) == product_id and float(l.get("qty") or 0) > 0]
    total_qty = sum(float(l.get("qty") or 0) for l in lots)
    lots_sorted = sorted(lots, key=_fifo_key)

    fifo_lot = lots_sorted[0] if lots_sorted else None
    fifo_payload = {
        "lot_id": fifo_lot.get("id") if fifo_lot else None,
        "best_before": fifo_lot.get("best_before") if fifo_lot else None,
        "location": fifo_lot.get("location") if fifo_lot else None,
    }

    brand_final = brand_prod
    if not brand_final:
        for l in lots_sorted:
            b = _first_non_empty(l.get("brand"))
            if b:
                brand_final = b
                break

    lots_payload = []
    for l in lots_sorted:
        lots_payload.append({
            "lot_id": l.get("id"),
            "qty": float(l.get("qty") or 0),
            "unit": _first_non_empty(l.get("unit"), unit_prod) or "",
            "best_before": l.get("best_before"),
            "location": l.get("location"),
            "location_id": l.get("location_id"),
            "brand": _first_non_empty(l.get("brand")) or "",
            "ean": _first_non_empty(l.get("ean"), l.get("barcode")) or "",
            "store": l.get("store"),
            "frozen_on": l.get("frozen_on"),
            "created_on": l.get("created_on"),
        })

    return JSONResponse({
        "product_id": product_id,
        "unit": unit_prod or "",
        "brand": brand_final or "",
        "total_qty": total_qty,
        "fifo": fifo_payload,
        "lots_count": len(lots_payload),
        "lots": lots_payload,
    })


# ─────────────────────────────────────────────
# GET /api/product/by_barcode  (#10 multi-EAN)
# ─────────────────────────────────────────────

@router.get("/api/product/by_barcode")
def api_product_by_barcode(code: str) -> JSONResponse:
    """
    Recherche un produit par code-barres EAN.
    Cherche dans tous les EAN associés au produit (multi-EAN, #10),
    avec fallback sur products.barcode pour la rétro-compatibilité.
    """
    code = (code or "").strip().replace(" ", "")
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)

    result = find_product_by_barcode(code)
    if not result:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "id": result["id"],
        "name": result["name"],
        "barcode": result.get("barcode") or code,
    })


# ─────────────────────────────────────────────
# GET /api/product/{product_id}/barcodes
# ─────────────────────────────────────────────

@router.get("/api/product/{product_id}/barcodes")
def api_list_barcodes(product_id: int = Path(..., ge=1)) -> JSONResponse:
    """
    Retourne tous les codes-barres associés à un produit.

    Réponse :
        {
          "product_id": 5,
          "barcodes": [
            {"id": 1, "barcode": "3017620425035", "label": ""},
            {"id": 2, "barcode": "3017620425036", "label": "Grand format"}
          ]
        }
    """
    products = list_products() or []
    if not any(int(p["id"]) == product_id for p in products):
        return JSONResponse({"error": "product not found"}, status_code=404)

    barcodes = get_product_barcodes(product_id)
    return JSONResponse({"product_id": product_id, "barcodes": barcodes})


# ─────────────────────────────────────────────
# POST /api/product/{product_id}/barcodes
# ─────────────────────────────────────────────

@router.post("/api/product/{product_id}/barcodes")
def api_add_barcode(
    product_id: int = Path(..., ge=1),
    barcode: str = Body(..., embed=True),
    label: Optional[str] = Body(None, embed=True),
) -> JSONResponse:
    """
    Ajoute un code-barres EAN à un produit.

    Corps JSON :
        { "barcode": "3017620425036", "label": "Grand format" }

    Réponse succès :
        { "ok": true, "id": 42, "barcode": "3017620425036" }

    Réponse si barcode déjà utilisé :
        { "ok": false, "error": "barcode already exists" }
    """
    products = list_products() or []
    if not any(int(p["id"]) == product_id for p in products):
        return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)

    barcode_clean = "".join(ch for ch in (barcode or "") if ch.isdigit())
    if not barcode_clean:
        return JSONResponse({"ok": False, "error": "barcode invalide (chiffres uniquement)"}, status_code=400)

    bc_id = add_product_barcode(product_id, barcode_clean, label or "")
    if bc_id is None:
        return JSONResponse({"ok": False, "error": "barcode already exists"}, status_code=409)

    log_event("product.barcode.add", {"product_id": product_id, "barcode": barcode_clean, "label": label})
    return JSONResponse({"ok": True, "id": bc_id, "barcode": barcode_clean})


# ─────────────────────────────────────────────
# DELETE /api/product/barcodes/{bc_id}
# ─────────────────────────────────────────────

@router.delete("/api/product/barcodes/{bc_id}")
def api_delete_barcode(bc_id: int = Path(..., ge=1)) -> JSONResponse:
    """
    Supprime un code-barres par son id (obtenu via GET /api/product/{id}/barcodes).

    Réponse succès :
        { "ok": true }

    Réponse si non trouvé :
        { "ok": false, "error": "not found" }
    """
    deleted = delete_product_barcode(bc_id)
    if not deleted:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    log_event("product.barcode.delete", {"bc_id": bc_id})
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────
# GET /api/off  (Open Food Facts)
# ─────────────────────────────────────────────

@router.get("/api/off")
def api_off(barcode: str) -> JSONResponse:
    """Recherche un produit sur Open Food Facts par code-barres."""
    import urllib.request
    import urllib.error

    barcode = (barcode or "").strip()
    if not barcode:
        return JSONResponse({"ok": False, "error": "missing barcode"}, status_code=400)

    if not re.match(r"^\d{8,14}$", barcode):
        log.warning("api_off: barcode invalide: %r", barcode)
        return JSONResponse({"ok": False, "error": "invalid barcode"}, status_code=400)

    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Domovra/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data: Dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return JSONResponse({"ok": False, "error": "offline"}, status_code=502)
    except Exception:
        return JSONResponse({"ok": False, "error": "parse"}, status_code=500)

    if not isinstance(data, dict) or data.get("status") != 1:
        return JSONResponse({"ok": False, "error": "notfound"}, status_code=404)

    p: Dict[str, Any] = data.get("product", {}) or {}
    return JSONResponse({
        "ok": True,
        "barcode": barcode,
        "name": p.get("product_name") or "",
        "brand": p.get("brands") or "",
        "quantity": p.get("quantity") or "",
        "image": p.get("image_front_url") or p.get("image_url") or "",
    })


# ─────────────────────────────────────────────
# POST /api/log  (journalisation front)
# ─────────────────────────────────────────────

@router.post("/api/log")
def api_log(
    kind: str = Body(...),
    payload: Dict[str, Any] = Body(default_factory=dict),
) -> JSONResponse:
    """Écrit un événement dans le journal depuis le front."""
    try:
        log.debug("api_log kind=%s payload=%s", kind, payload)
        log_event(kind, payload or {})
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"ok": False}, status_code=500)
