# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transport : Bluetooth RFCOMM (SPP, canal 1).
Protocole : Tomaszu97 / M110 natif — 43 bytes/ligne, EXACTEMENT 240 lignes
            (padding blanc si image plus courte).

Références :
  https://github.com/Tomaszu97/phomemo
  https://github.com/vivier/phomemo-tools
"""
from __future__ import annotations

import asyncio
import logging
import struct
from io import BytesIO
import socket

logger = logging.getLogger("domovra.printer")

# ── Constantes protocole M110 ─────────────────────────────────
PRINT_WIDTH   = 344    # pixels (43 bytes × 8)
BYTES_PER_ROW = 43     # octets par ligne
BLOCK_LINES   = 240    # nombre fixe de lignes par bloc (padding blanc si besoin)
RFCOMM_CHANNEL = 1

# Header propriétaire M110 (Tomaszu97)
_HEADER = bytes([
    0x1B, 0x4E, 0x0D, 0x01,  # vitesse impression : 0x01 (lent) à 0x05 (rapide)
    0x1B, 0x4E, 0x04, 0x0F,  # densité impression : 0x01 à 0x0F
    0x1F, 0x11, 0x0A,         # type média : 0x0A = étiquettes avec espaces
])

# Bloc GS v 0 avec 240 lignes fixes
_BLOCK_MARKER = bytes([0x1D, 0x76, 0x30, 0x00]) \
    + struct.pack("<H", BYTES_PER_ROW) \
    + struct.pack("<H", BLOCK_LINES)

# Footer propriétaire M110 (déclenche l'impression + avance papier)
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

# Ligne blanche (padding) : 43 zéros
_WHITE_ROW = bytes(BYTES_PER_ROW)


# ── Image ────────────────────────────────────────────────────

def build_label_image(data: dict) -> bytes:
    """Génère une image PNG de l'étiquette (pour prévisualisation)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow non installé")

    img = Image.new("1", (PRINT_WIDTH, 220), color=1)
    draw = ImageDraw.Draw(img)

    try:
        ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        ft = fb = fs = ImageFont.load_default()

    y = 6
    name = str(data.get("name") or data.get("article_name") or data.get("product") or "Produit")
    if len(name) > 26:
        name = name[:23] + "..."
    draw.text((6, y), name, font=ft, fill=0); y += 28
    draw.line([(6, y), (PRINT_WIDTH - 6, y)], fill=0, width=1); y += 6

    qty = data.get("qty", ""); unit = data.get("unit", "")
    draw.text((6, y), f"Qte : {qty} {unit}".strip(), font=fb, fill=0); y += 22

    bb = data.get("best_before") or ""
    if bb:
        st = data.get("status", "green")
        pfx = {"green": "DLC", "yellow": "DLC !", "red": "DLC !!!"}.get(st, "DLC")
        draw.text((6, y), f"{pfx} : {bb}", font=fb, fill=0); y += 22

    loc = data.get("location") or ""
    if loc:
        draw.text((6, y), f"Lieu : {loc}", font=fs, fill=0); y += 18

    info = " | ".join(filter(None, [data.get("brand") or "", data.get("store") or ""]))
    if info:
        draw.text((6, y), info, font=fs, fill=0); y += 16

    img = img.crop((0, 0, PRINT_WIDTH, y + 8))
    buf = BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def build_test_label_image() -> bytes:
    return build_label_image(_TEST_DATA)


# ── Protocole raster M110 ────────────────────────────────────

def _build_payload(data: dict) -> bytes:
    """
    Construit le payload complet pour le M110.
    Produit EXACTEMENT BLOCK_LINES (240) lignes de BYTES_PER_ROW (43) octets
    en paddant avec des lignes blanches si l'image est plus courte.
    Plusieurs blocs si plus de 240 lignes (rare).
    """
    from PIL import Image

    # Génère l'image à la bonne largeur
    png_bytes = build_label_image(data)
    img = Image.open(BytesIO(png_bytes)).convert("1")
    w, h = img.size

    # Redimensionne si nécessaire
    if w != PRINT_WIDTH:
        img = img.resize((PRINT_WIDTH, int(h * PRINT_WIDTH / w)), Image.LANCZOS).convert("1")
        w, h = img.size

    # Convertit chaque ligne en bytes raster
    raw_rows: list[bytes] = []
    for y in range(h):
        row = bytearray(BYTES_PER_ROW)
        for x in range(PRINT_WIDTH):
            if img.getpixel((x, y)) == 0:  # 0 = noir dans PIL "1"
                row[x // 8] |= (0x80 >> (x % 8))
        # Substitution obligatoire : 0x0A (LF RFCOMM) → 0x14
        raw_rows.append(bytes(0x14 if b == 0x0A else b for b in row))

    logger.info("Image raster : %d lignes × %d octets", h, BYTES_PER_ROW)

    buf = bytearray(_HEADER)

    # Découpe en blocs de BLOCK_LINES lignes (padding blanc en fin de dernier bloc)
    for block_start in range(0, max(h, 1), BLOCK_LINES):
        block_rows = raw_rows[block_start:block_start + BLOCK_LINES]
        # Pad à exactement BLOCK_LINES lignes avec du blanc
        while len(block_rows) < BLOCK_LINES:
            block_rows.append(_WHITE_ROW)

        buf += _BLOCK_MARKER  # 1D 76 30 00 + 2B 00 + F0 00
        for row in block_rows:
            buf += row

    buf += _FOOTER
    return bytes(buf)


# ── Transport RFCOMM ─────────────────────────────────────────

def _send_rfcomm(mac: str, payload: bytes, timeout: int = 15) -> None:
    logger.info("RFCOMM → %s canal %d (%d octets)", mac, RFCOMM_CHANNEL, len(payload))
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(timeout)
    try:
        sock.connect((mac, RFCOMM_CHANNEL))
        for i in range(0, len(payload), 512):
            sock.sendall(payload[i:i + 512])
        logger.info("RFCOMM OK → %s", mac)
    finally:
        try:
            sock.close()
        except Exception:
            pass


async def send_to_printer(mac: str, image_data: dict) -> None:
    payload = _build_payload(image_data)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_rfcomm, mac, payload)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot (fire-and-forget)."""
    import threading
    def _run():
        try:
            payload = _build_payload(lot_data)
            _send_rfcomm(mac, payload)
        except Exception as e:
            logger.error("Impression lot échouée: %s", e)
    threading.Thread(target=_run, daemon=True).start()
