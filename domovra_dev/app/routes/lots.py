# app/routes/lots.py
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from utils.http import ingress_base, render as render_with_env
from services.events import log_event
from services.ha_entities import schedule_ha_push
from config import get_retention_thresholds
from db import (
    list_lots, list_locations, list_products,
    add_lot, update_lot, delete_lot, consume_lot,
    status_for
)

router = APIRouter()

@router.get("/lots", response_class=HTMLResponse)
def lots_page(
    request: Request,
    product: str = Query("", alias="product"),
    location: str = Query("", alias="location"),
    status: str = Query("", alias="status"),
):
    base = ingress_base(request)

    WARNING_DAYS, CRITICAL_DAYS = get_retention_thresholds()

    items = list_lots()
    for it in items:
        it["status"] = status_for(it.get("best_before"), WARNING_DAYS, CRITICAL_DAYS)

    if product:
        needle = product.casefold()
        items = [i for i in items if needle in (i.get("product", "").casefold())]
    if location:
        items = [i for i in items if i.get("location") == location]
    if status:
        items = [i for i in items if i.get("status") == status]

    return render_with_env(
        request.app.state.templates,
        "lots.html",
        BASE=base,
        page="lots",
        request=request,
        items=items,
        locations=list_locations(),
        products=list_products(),
    )


@router.post("/lot/add")
def lot_add_action(request: Request,
                   product_id: int = Form(...),
                   location_id: int = Form(...),
                   qty: float = Form(...),
                   frozen_on: str = Form(""),
                   best_before: str = Form("")):
    base = ingress_base(request)
    q = float(qty)
    if q <= 0:
        return RedirectResponse(base + "lots?error=qty_invalid",
                                status_code=303, headers={"Cache-Control": "no-store"})
    add_lot(product_id, location_id, q, frozen_on or None, best_before or None)
    log_event("lot.add", {
        "product_id": product_id, "location_id": location_id, "qty": q,
        "frozen_on": frozen_on or None, "best_before": best_before or None
    })
    schedule_ha_push()
    return RedirectResponse(base + "lots?added=1",
                            status_code=303, headers={"Cache-Control": "no-store"})


@router.post("/lot/update")
def lot_update_action(request: Request,
                      lot_id: int = Form(...),
                      qty: float = Form(...),
                      location_id: int = Form(...),
                      frozen_on: str = Form(""),
                      best_before: str = Form("")):
    base = ingress_base(request)
    try:
        q = float(qty)
    except Exception:
        q = 0.0
    if q < 0:
        return RedirectResponse(base + "lots?error=qty_invalid",
                                status_code=303, headers={"Cache-Control": "no-store"})
    update_lot(lot_id, q, int(location_id), frozen_on or None, best_before or None)
    log_event("lot.update", {
        "lot_id": lot_id, "qty": q, "location_id": int(location_id),
        "frozen_on": frozen_on or None, "best_before": best_before or None
    })
    schedule_ha_push()
    return RedirectResponse(base + "lots?updated=1",
                            status_code=303, headers={"Cache-Control": "no-store"})


@router.post("/lot/consume")
def lot_consume_action(request: Request, lot_id: int = Form(...), qty: float = Form(...)):
    base = ingress_base(request)
    q = float(qty)
    if q <= 0:
        return RedirectResponse(base + "lots?error=qty_invalid",
                                status_code=303, headers={"Cache-Control": "no-store"})
    consume_lot(lot_id, q)
    log_event("lot.consume", {"lot_id": lot_id, "qty": q})
    schedule_ha_push()
    return RedirectResponse(base + "lots",
                            status_code=303, headers={"Cache-Control": "no-store"})


@router.post("/lot/delete")
def lot_delete_action(request: Request, lot_id: int = Form(...)):
    try:
        affected = delete_lot(int(lot_id))  # retourne rowcount (0 ou 1)
    except Exception as e:
        return JSONResponse({"error": "delete_failed", "lot_id": lot_id, "detail": str(e)}, status_code=500)

    # Idempotent : si déjà supprimé, on ne casse pas l'UX
    base = ingress_base(request)
    if not affected:
        return RedirectResponse(base + "lots?deleted=1", status_code=303, headers={"Cache-Control": "no-store"})

    try:
        log_event("lot.delete", {"lot_id": lot_id})
    except Exception:
        pass

    schedule_ha_push()
    return RedirectResponse(base + "lots?deleted=1", status_code=303, headers={"Cache-Control": "no-store"})


# --- Debug JSON : même data que la page /lots --------------------------------
@router.get("/_debug/lots")
def debug_lots(
    request: Request,
    product: str = Query("", alias="product"),
    location: str = Query("", alias="location"),
    status: str = Query("", alias="status"),
):
    WARNING_DAYS, CRITICAL_DAYS = get_retention_thresholds()

    items = list_lots()

    for it in items:
        it["status"] = status_for(it.get("best_before"), WARNING_DAYS, CRITICAL_DAYS)

    if product:
        needle = product.casefold()
        items = [i for i in items if needle in (i.get("product", "").casefold())]
    if location:
        items = [i for i in items if i.get("location") == location]
    if status:
        items = [i for i in items if i.get("status") == status]

    counts = {"total": len(items), "by_status": {"green": 0, "yellow": 0, "red": 0}}
    for it in items:
        s = it.get("status") or "green"
        if s not in counts["by_status"]:
            counts["by_status"][s] = 0
        counts["by_status"][s] += 1

    locations = list_locations()
    products = list_products()

    return JSONResponse({
        "filters_applied": {"product": product, "location": location, "status": status},
        "counts": counts,
        "items": items,
        "filter_options": {
            "locations": [{"id": l["id"], "name": l["name"]} for l in locations],
            "products": [{"id": p["id"], "name": p["name"]} for p in products],
            "status": ["green", "yellow", "red"]
        }
    })
