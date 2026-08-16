# domovra/app/routes/print_route.py
"""
Routes d'impression d'etiquettes (Phomemo M110).
Transport : BLE GATT via raw L2CAP ATT (sans D-Bus/bluetoothd).
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from settings_store import load_settings
from db import list_lots
from config import get_retention_thresholds
from db import status_for

logger = logging.getLogger("domovra.print_route")

router = APIRouter()
_BLE_TIMEOUT = 35.0


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
             "message": "Imprimante non configuree ou desactivee dans les Parametres."},
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
        from services.printer import _build_payload, print_ble
        payload = _build_payload(lot, ble=True)

        result: dict = {}
        exc: list[Exception] = []
        done = threading.Event()

        def _run():
            try:
                result.update(print_ble(mac, payload, timeout=_BLE_TIMEOUT))
            except Exception as e:
                exc.append(e)
            finally:
                done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, done.wait, _BLE_TIMEOUT + 5)

        if exc:
            raise exc[0]
        if not result:
            return JSONResponse({"ok": False, "error": "timeout",
                                 "message": f"Timeout ({_BLE_TIMEOUT:.0f}s) — imprimante allumee ?"}, status_code=504)
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Impression lot %s: %s", lot_id, e)
        return JSONResponse({"ok": False, "error": "unexpected", "message": str(e)}, status_code=500)


@router.post("/api/print/test")
async def print_test_label(request: Request):
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse(
            {"ok": False, "error": "printer_disabled",
             "message": "Imprimante non configuree ou desactivee dans les Parametres."},
            status_code=400,
        )
    try:
        from services.printer import _build_payload, print_ble
        payload = _build_payload({
            "name": "Etiquette de test",
            "qty": "1", "unit": "pc",
            "best_before": "2099-12-31",
            "status": "green",
            "location": "Domovra M110",
            "brand": "Test", "store": "",
        }, ble=True)

        result: dict = {}
        exc: list[Exception] = []
        done = threading.Event()

        def _run():
            try:
                result.update(print_ble(mac, payload, timeout=_BLE_TIMEOUT))
            except Exception as e:
                exc.append(e)
            finally:
                done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, done.wait, _BLE_TIMEOUT + 5)

        if exc:
            raise exc[0]
        if not result:
            return JSONResponse({"ok": False, "error": "timeout",
                                 "message": f"Timeout ({_BLE_TIMEOUT:.0f}s) — imprimante allumee ?"}, status_code=504)
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Impression test: %s", e)
        return JSONResponse({"ok": False, "error": "unexpected", "message": str(e)}, status_code=500)


@router.get("/api/print/diag")
async def ble_diagnostic(request: Request):
    """Diagnostic BLE : teste la connexion L2CAP ATT raw."""
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse({"ok": False, "message": "Imprimante non configuree."}, status_code=400)
    try:
        from services.printer import _ble_connect
        import asyncio
        loop = asyncio.get_event_loop()

        result: dict = {}
        exc: list[Exception] = []
        done = threading.Event()

        def _run():
            try:
                sock = _ble_connect(mac, timeout=15.0)
                sock.close()
                result["ok"] = True
                result["message"] = f"Connexion BLE ATT reussie vers {mac}"
            except TimeoutError as e:
                result["ok"] = False
                result["error"] = str(e)
            except OSError as e:
                result["ok"] = False
                result["error"] = f"Erreur BLE : {e}"
            except Exception as e:
                result["ok"] = False
                result["error"] = str(e)
            finally:
                done.set()

        threading.Thread(target=_run, daemon=True).start()
        await loop.run_in_executor(None, done.wait, 20)
        if not result:
            result = {"ok": False, "error": "Timeout diagnostic 20s"}
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Diagnostic BLE : %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/api/print/discover")
async def discover_ble(request: Request):
    """
    Decouverte GATT complete via raw L2CAP ATT.
    L'imprimante doit etre allumee et en mode attente.
    """
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse({"ok": False, "message": "Imprimante non configuree."}, status_code=400)

    try:
        from services.printer import discover_ble as _discover
        import asyncio
        loop = asyncio.get_event_loop()

        result: dict = {}
        exc: list[Exception] = []
        done = threading.Event()

        def _run():
            try:
                result.update(_discover(mac, timeout=_BLE_TIMEOUT))
            except Exception as e:
                exc.append(e)
                result["ok"] = False
                result["error"] = str(e)
            finally:
                done.set()

        threading.Thread(target=_run, daemon=True).start()
        await loop.run_in_executor(None, done.wait, _BLE_TIMEOUT + 5)

        if not result:
            return JSONResponse({"ok": False, "error": f"Timeout {_BLE_TIMEOUT:.0f}s — imprimante allumee ?"},
                                status_code=504)
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Decouverte BLE : %s", e)
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
