# domovra/app/routes/print_route.py
"""
Routes d'impression d'étiquettes (Phomemo M110).
Transport : BLE GATT via bleak (D-Bus host), fallback raw L2CAP ATT.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from settings_store import load_settings
from db import list_lots
from config import get_retention_thresholds
from db import status_for

logger = logging.getLogger("domovra.print_route")

router = APIRouter()
_BLE_TIMEOUT = 25.0


def _get_printer_mac() -> str | None:
    s = load_settings()
    if not s.get("printer_enabled"):
        return None
    mac = s.get("printer_mac", "").strip()
    return mac if mac else None


@router.post("/api/print/lot/{lot_id}")
async def print_lot_label(request: Request, lot_id: int):
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse(
            {"ok": False, "error": "printer_disabled",
             "message": "Imprimante non configurée ou désactivée dans les Paramètres."},
            status_code=400,
        )
    WARNING_DAYS, CRITICAL_DAYS = get_retention_thresholds()
    lots = list_lots()
    lot = next((l for l in lots if l["id"] == lot_id), None)
    if not lot:
        return JSONResponse({"ok": False, "error": "not_found",
                             "message": f"Lot {lot_id} introuvable."}, status_code=404)
    lot["status"] = status_for(lot.get("best_before"), WARNING_DAYS, CRITICAL_DAYS)
    try:
        from services.printer import _build_payload, print_via_bleak
        payload = _build_payload(lot, ble=True)
        result = await asyncio.wait_for(print_via_bleak(mac, payload), timeout=_BLE_TIMEOUT)
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "timeout",
                             "message": f"Timeout ({_BLE_TIMEOUT}s) — imprimante allumée ?"}, status_code=504)
    except Exception as e:
        logger.exception("Impression lot %s: %s", lot_id, e)
        return JSONResponse({"ok": False, "error": "unexpected", "message": str(e)}, status_code=500)


@router.post("/api/print/test")
async def print_test_label(request: Request):
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse(
            {"ok": False, "error": "printer_disabled",
             "message": "Imprimante non configurée ou désactivée dans les Paramètres."},
            status_code=400,
        )
    try:
        from services.printer import _build_payload, print_via_bleak
        payload = _build_payload({
            "name": "Etiquette de test",
            "qty": "1", "unit": "pc",
            "best_before": "2099-12-31",
            "status": "green",
            "location": "Domovra M110",
            "brand": "Test", "store": "",
        }, ble=True)
        result = await asyncio.wait_for(print_via_bleak(mac, payload), timeout=_BLE_TIMEOUT)
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "timeout",
                             "message": f"Timeout ({_BLE_TIMEOUT}s) — imprimante allumée ?"}, status_code=504)
    except Exception as e:
        logger.exception("Impression test: %s", e)
        return JSONResponse({"ok": False, "error": "unexpected", "message": str(e)}, status_code=500)


@router.get("/api/print/diag")
async def ble_diagnostic(request: Request):
    """Diagnostic BLE rapide : D-Bus, bluetoothctl, bleak."""
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse({"ok": False, "message": "Imprimante non configurée."}, status_code=400)
    try:
        from services.printer import diagnose_ble
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, diagnose_ble, mac)
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Diagnostic BLE : %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/api/print/discover")
async def discover_ble(request: Request):
    """
    Découverte GATT complète : bleak en priorité, raw L2CAP ATT en fallback.
    L'imprimante doit être allumée.
    """
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse({"ok": False, "message": "Imprimante non configurée."}, status_code=400)

    # Essai 1 : bleak (D-Bus)
    try:
        from services.printer import discover_via_bleak, _setup_dbus
        dbus = _setup_dbus()
        if dbus:
            result = await asyncio.wait_for(
                discover_via_bleak(mac, timeout=20.0), timeout=25.0
            )
            if result.get("ok"):
                return JSONResponse(result)
            logger.warning("bleak discover échoué : %s — fallback raw L2CAP", result.get("error"))
    except asyncio.TimeoutError:
        logger.warning("bleak discover timeout — fallback raw L2CAP")
    except Exception as e:
        logger.warning("bleak discover exception : %s", e)

    # Fallback : raw L2CAP ATT
    try:
        from services.printer import discover_raw_ble
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, discover_raw_ble, mac),
            timeout=30.0,
        )
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "timeout 30s — imprimante allumée ?"},
                            status_code=504)
    except Exception as e:
        logger.exception("Découverte raw BLE : %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/api/print/preview/lot/{lot_id}")
async def preview_lot_label(request: Request, lot_id: int):
    WARNING_DAYS, CRITICAL_DAYS = get_retention_thresholds()
    lots = list_lots()
    lot = next((l for l in lots if l["id"] == lot_id), None)
    if not lot:
        return JSONResponse({"error": "not_found"}, status_code=404)
    lot["status"] = status_for(lot.get("best_before"), WARNING_DAYS, CRITICAL_DAYS)
    try:
        from services.printer import build_label_image
        return Response(content=build_label_image(lot), media_type="image/png")
    except Exception as e:
        logger.error("Preview lot %s: %s", lot_id, e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/print/preview/test")
async def preview_test_label(request: Request):
    try:
        from services.printer import build_test_label_image
        return Response(content=build_test_label_image(), media_type="image/png")
    except Exception as e:
        logger.error("Preview test: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)
