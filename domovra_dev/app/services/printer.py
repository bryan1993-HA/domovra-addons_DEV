# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transport : Bluetooth RFCOMM (SPP, canal 1) via socket AF_BLUETOOTH noyau.
Requiert host_network:true dans config.json.

Protocole M110 (reverse-engineered) :
  https://github.com/Tomaszu97/phomemo
  https://github.com/vivier/phomemo-tools
  https://github.com/hkeward/phomemo_printer
"""
from __future__ import annotations

import asyncio
import logging
import struct
from io import BytesIO
import socket

logger = logging.getLogger("domovra.printer")

# ── Constantes M110 ──────────────────────────────────────────
PRINT_WIDTH    = 344          # pixels (43 bytes × 8 bits)
BYTES_PER_ROW  = 43           # octets par ligne raster
MAX_BLOCK_LINES = 240         # lignes max par bloc GS v 0

_RFCOMM_CHANNEL = 1

# Header propriétaire M110
_HEADER = bytes([
    0x1B, 0x4E, 0x0D, 0x01,  # vitesse d'impression (0x01=lent … 0x05=rapide)
    0x1B, 0x4E, 0x04, 0x0F,  # densité d'impression (0x01–0x0F)
    0x1F, 0x11, 0x0A,         # type de média : 0x0A=étiquettes avec espaces
])

# Footer propriétaire M110
_FOOTER = bytes([
    0x1F, 0xF0, 0x05, 0x00,
    0x1F, 0xF0, 0x03, 0x00,
])

_TEST_DATA: dict = {
    "name": "Etiquette de test",
    "qty": "1",
    "unit": "pc",
    "best_before": "2099-12-31",
    "status": "green",
    "location": "Domovra M110",
    "brand": "Test",
    "store": "",
}


# ── Image ────────────────────────────────────────────────────

def build_label_image(data: dict) -> bytes:
    """Génère une image PNG de l'étiquette."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow non installé")

    img = Image.new("1", (PRINT_WIDTH, 220), color=1)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = font_small = font_title

    y = 6
    name = str(data.get("name") or data.get("article_name") or data.get("product") or "Produit")
    if len(name) > 26:
        name = name[:23] + "..."
    draw.text((6, y), name, font=font_title, fill=0); y += 28
    draw.line([(6, y), (PRINT_WIDTH - 6, y)], fill=0, width=1); y += 6

    qty = data.get("qty", ""); unit = data.get("unit", "")
    draw.text((6, y), f"Qte : {qty} {unit}".strip(), font=font_body, fill=0); y += 22

    best_before = data.get("best_before") or ""
    if best_before:
        status = data.get("status", "green")
        dlc_prefix = {"green": "DLC", "yellow": "DLC !", "red": "DLC !!!"}.get(status, "DLC")
        draw.text((6, y), f"{dlc_prefix} : {best_before}", font=font_body, fill=0); y += 22

    location = data.get("location") or ""
    if location:
        draw.text((6, y), f"Lieu : {location}", font=font_small, fill=0); y += 18

    brand = data.get("brand") or ""; store = data.get("store") or ""
    info = " | ".join(filter(None, [brand, store]))
    if info:
        draw.text((6, y), info, font=font_small, fill=0); y += 16

    img = img.crop((0, 0, PRINT_WIDTH, y + 8))
    buf = BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def build_test_label_image() -> bytes:
    return build_label_image(_TEST_DATA)


# ── Protocole raster M110 ────────────────────────────────────

def _png_to_raster_rows(png_bytes: bytes) -> list[bytes]:
    """
    Convertit une image PNG en liste de lignes raster M110.
    - 43 octets par ligne (344 px)
    - MSB = pixel gauche, bit=1 pour noir
    - Substitution 0x0A → 0x14 (évite interprétation RFCOMM comme LF)
    """
    from PIL import Image

    img = Image.open(BytesIO(png_bytes)).convert("1")
    w, h = img.size

    if w != PRINT_WIDTH:
        img = img.resize((PRINT_WIDTH, int(h * PRINT_WIDTH / w)), Image.LANCZOS).convert("1")
        w, h = img.size

    rows = []
    for y in range(h):
        row = bytearray(BYTES_PER_ROW)
        for x in range(PRINT_WIDTH):
            if img.getpixel((x, y)) == 0:  # 0 = noir
                row[x // 8] |= (0x80 >> (x % 8))
        # Substitution obligatoire : 0x0A (LF) → 0x14
        rows.append(bytes(0x14 if b == 0x0A else b for b in row))

    return rows


def _build_print_payload(png_bytes: bytes) -> bytes:
    """
    Construit le payload complet à envoyer via RFCOMM.
    Format :
        HEADER
        [GS v 0 + lignes_par_bloc + n_lignes + données…] × N blocs
        FOOTER
    """
    rows = _png_to_raster_rows(png_bytes)
    buf = bytearray(_HEADER)

    # Découpe en blocs de MAX_BLOCK_LINES lignes
    for i in range(0, len(rows), MAX_BLOCK_LINES):
        batch = rows[i:i + MAX_BLOCK_LINES]
        n = len(batch)
        # GS v 0 : mode 0, largeur en octets (LE16), hauteur en lignes (LE16)
        buf += bytes([0x1D, 0x76, 0x30, 0x00])
        buf += struct.pack("<H", BYTES_PER_ROW)   # 43 = 0x2B 0x00
        buf += struct.pack("<H", n)                # nombre de lignes
        for row in batch:
            buf += row

    buf += _FOOTER
    return bytes(buf)


# ── Transport RFCOMM ─────────────────────────────────────────

def _send_via_rfcomm(mac: str, payload: bytes, timeout: int = 15) -> None:
    logger.info("RFCOMM → %s canal %d (%d octets)", mac, _RFCOMM_CHANNEL, len(payload))
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(timeout)
    try:
        sock.connect((mac, _RFCOMM_CHANNEL))
        for i in range(0, len(payload), 512):
            sock.sendall(payload[i:i + 512])
        logger.info("RFCOMM OK → %s", mac)
    finally:
        try:
            sock.close()
        except Exception:
            pass


async def send_to_printer(mac: str, image_data: dict) -> None:
    """Async : génère l'image, construit le payload, envoie via RFCOMM."""
    png_bytes = build_label_image(image_data)
    payload = _build_print_payload(png_bytes)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_via_rfcomm, mac, payload)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot (fire-and-forget)."""
    import threading

    def _run():
        try:
            png_bytes = build_label_image(lot_data)
            payload = _build_print_payload(png_bytes)
            _send_via_rfcomm(mac, payload)
        except Exception as e:
            logger.error("Impression lot échouée: %s", e)

    threading.Thread(target=_run, daemon=True).start()
