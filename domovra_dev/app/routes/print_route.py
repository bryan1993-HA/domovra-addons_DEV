# domovra/app/routes/print_route.py
"""
Routes d'impression d'étiquettes BLE (Phomemo M110 et compatibles).
"""
from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from settings_store import load_settings
from db import list_lots
from config import get_retention_thresholds
from db import status_for

logger = logging.getLogger("domovra.print_route")

router = APIRouter()

# Timeout BLE pour les impressions synchrones (test + aperçu)
_BLE_TIMEOUT = 20


def _get_printer_mac() -> str | None:
    """Retourne la MAC configurée si l'impression est activée, sinon None."""
    s = load_settings()
    if not s.get("printer_enabled"):
        return None
    mac = s.get("printer_mac", "").strip()
    return mac if mac else None


@router.post("/api/print/lot/{lot_id}")
async def print_lot_label(request: Request, lot_id: int):
    """Lance l'impression de l'étiquette d'un lot en BLE (fire-and-forget)."""
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
        return JSONResponse(
            {"ok": False, "error": "not_found", "message": f"Lot {lot_id} introuvable."},
            status_code=404,
        )

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
    """Lance une impression de test synchrone — retourne le vrai résultat BLE."""
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
        return JSONResponse({"ok": True, "message": f"Etiquette de test imprimée ({mac})."})
    except asyncio.TimeoutError:
        msg = f"Timeout ({_BLE_TIMEOUT}s) — imprimante non répondue. Vérifiez qu'elle est allumée et à portée."
        logger.error("Impression test timeout: %s", mac)
        return JSONResponse({"ok": False, "error": "timeout", "message": msg}, status_code=504)
    except RuntimeError as e:
        logger.error("Impression test: %s", e)
        return JSONResponse({"ok": False, "error": "print_error", "message": str(e)}, status_code=500)
    except Exception as e:
        logger.exception("Impression test inattendue: %s", e)
        return JSONResponse({"ok": False, "error": "unexpected", "message": str(e)}, status_code=500)


@router.get("/api/print/preview/lot/{lot_id}")
async def preview_lot_label(request: Request, lot_id: int):
    """Retourne l'image PNG de prévisualisation de l'étiquette (sans imprimer)."""
    WARNING_DAYS, CRITICAL_DAYS = get_retention_thresholds()
    lots = list_lots()
    lot = next((l for l in lots if l["id"] == lot_id), None)
    if not lot:
        return JSONResponse({"error": "not_found"}, status_code=404)

    lot["status"] = status_for(lot.get("best_before"), WARNING_DAYS, CRITICAL_DAYS)

    try:
        from services.printer import build_label_image
        png_bytes = build_label_image(lot)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        logger.error("Preview lot %s: %s", lot_id, e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/print/preview/test")
async def preview_test_label(request: Request):
    """Retourne l'image PNG de l'étiquette de test (sans imprimer)."""
    try:
        from services.printer import build_test_label_image
        png_bytes = build_test_label_image()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        logger.error("Preview test: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)
