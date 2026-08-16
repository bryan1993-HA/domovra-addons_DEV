# domovra/app/routes/print_route.py
"""
Routes d'impression d'étiquettes (Phomemo M110).
Transport : BLE GATT via L2CAP ATT brut (sans D-Bus).
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
_BLE_TIMEOUT = 20


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
        from services.printer import print_lot
        print_lot(mac, lot)
        return JSONResponse({"ok": True, "message": "Impression lancée."})
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
        from services.printer import send_to_printer, _TEST_DATA
        await asyncio.wait_for(send_to_printer(mac, _TEST_DATA), timeout=_BLE_TIMEOUT)
        return JSONResponse({"ok": True, "message": f"Etiquette de test envoyée ({mac})."})
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "timeout",
                             "message": f"Timeout ({_BLE_TIMEOUT}s) — imprimante non répondue."}, status_code=504)
    except Exception as e:
        logger.exception("Impression test: %s", e)
        return JSONResponse({"ok": False, "error": "unexpected", "message": str(e)}, status_code=500)


@router.post("/api/print/rawtest")
async def print_raw_test(request: Request):
    """Diagnostic RFCOMM : envoie du texte ESC/POS brut via canal 1."""
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse({"ok": False, "message": "Imprimante non configurée."}, status_code=400)

    import socket as _socket

    def _send():
        payload = (
            b"\x1b\x40"
            b"\x1b\x61\x01"
            b"Test RFCOMM M110\n"
            b"\x1b\x64\x05"
        )
        sock = _socket.socket(_socket.AF_BLUETOOTH, _socket.SOCK_STREAM, _socket.BTPROTO_RFCOMM)
        sock.settimeout(10)
        try:
            sock.connect((mac, 1))
            sock.sendall(payload)
            return None
        except Exception as exc:
            return str(exc)
        finally:
            try: sock.close()
            except Exception: pass

    loop = asyncio.get_event_loop()
    err = await loop.run_in_executor(None, _send)
    if err:
        return JSONResponse({"ok": False, "message": f"Erreur : {err}"}, status_code=500)
    return JSONResponse({"ok": True, "message": "Texte test envoyé via RFCOMM (rien ne sort = normal sur M110)."})


@router.get("/api/print/discover")
async def discover_ble(request: Request):
    """
    Découverte BLE GATT du M110 via socket L2CAP ATT brut.
    Retourne tous les services et caractéristiques trouvés.
    """
    mac = _get_printer_mac()
    if not mac:
        return JSONResponse({"ok": False, "message": "Imprimante non configurée."}, status_code=400)
    try:
        from services.printer import discover_ble_characteristics
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, discover_ble_characteristics, mac),
            timeout=20,
        )
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "timeout",
                             "message": "Timeout (20s) — imprimante BLE non trouvée."}, status_code=504)
    except Exception as e:
        logger.exception("Découverte BLE : %s", e)
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
