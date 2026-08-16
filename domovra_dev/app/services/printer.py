# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.
Transport : BLE GATT via bleak (accès au D-Bus BlueZ du host via host_network).

Références protocole :
  https://github.com/Tomaszu97/phomemo
  https://github.com/vivier/phomemo-tools
"""
from __future__ import annotations

import asyncio
import logging
import struct
from io import BytesIO

logger = logging.getLogger("domovra.printer")

# Largeur M110 : 40 mm * 8 dots/mm = 320 px (203 DPI)
PRINT_WIDTH   = 320
BYTES_PER_ROW = PRINT_WIDTH // 8  # 40 octets

# UUIDs BLE Phomemo M110
_WRITE_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

# Commandes protocole Phomemo ESC/POS
_CMD_INIT      = bytes([0x1b, 0x40])        # ESC @ — initialise l'imprimante
_CMD_PRINT_END = bytes([0x1b, 0x64, 0x02])  # ESC d 2 — avance papier

# Données de l'étiquette de test
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


# ──────────────────────────── Image ────────────────────────────

def build_label_image(data: dict) -> bytes:
    """Génère une image PNG de l'étiquette à partir des données d'un lot."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow non installé — impossible de générer l'étiquette")

    img = Image.new("1", (PRINT_WIDTH, 220), color=1)  # 1 = blanc
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
    except Exception:
        font_title = ImageFont.load_default()
        font_body  = font_title
        font_small = font_title

    y = 6

    name = str(data.get("name") or data.get("article_name") or data.get("product") or "Produit")
    if len(name) > 24:
        name = name[:21] + "..."
    draw.text((6, y), name, font=font_title, fill=0)
    y += 30

    draw.line([(6, y), (PRINT_WIDTH - 6, y)], fill=0, width=1)
    y += 6

    qty  = data.get("qty", "")
    unit = data.get("unit", "")
    draw.text((6, y), f"Qte : {qty} {unit}".strip(), font=font_body, fill=0)
    y += 24

    best_before = data.get("best_before") or ""
    if best_before:
        status = data.get("status", "green")
        dlc_prefix = {"green": "DLC", "yellow": "DLC !", "red": "DLC !!!"}.get(status, "DLC")
        draw.text((6, y), f"{dlc_prefix} : {best_before}", font=font_body, fill=0)
        y += 24

    location = data.get("location") or ""
    if location:
        draw.text((6, y), f"Lieu : {location}", font=font_small, fill=0)
        y += 20

    brand = data.get("brand") or ""
    store = data.get("store") or ""
    info = " | ".join(filter(None, [brand, store]))
    if info:
        draw.text((6, y), info, font=font_small, fill=0)
        y += 18

    img = img.crop((0, 0, PRINT_WIDTH, y + 8))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_test_label_image() -> bytes:
    """Génère une étiquette de test (PNG)."""
    return build_label_image(_TEST_DATA)


# ──────────────────────────── Protocole ────────────────────────────

def _image_to_commands(png_bytes: bytes) -> bytes:
    """Convertit une image PNG en commandes raster Phomemo (GS v 0)."""
    from PIL import Image

    img = Image.open(BytesIO(png_bytes)).convert("1")
    w, h = img.size

    if w != PRINT_WIDTH:
        img = img.resize((PRINT_WIDTH, int(h * PRINT_WIDTH / w)), Image.LANCZOS).convert("1")
        w, h = img.size

    buf = bytearray()
    for y in range(h):
        row = bytearray(BYTES_PER_ROW)
        for x in range(PRINT_WIDTH):
            if img.getpixel((x, y)) == 0:  # 0 = noir
                row[x // 8] |= (0x80 >> (x % 8))
        # GS v 0 — impression raster (1 ligne)
        buf += bytes([0x1d, 0x76, 0x30, 0x00])
        buf += struct.pack("<HH", BYTES_PER_ROW, 1)
        buf += row

    return bytes(buf)


# ──────────────────────────── Transport BLE ────────────────────────────

async def send_to_printer(mac: str, image_data: dict) -> None:
    """
    Connexion BLE GATT via bleak + envoi des commandes d'impression.
    Utilise le BlueZ du host (accessible via host_network: true dans config.json).
    Lève une exception en cas d'erreur — à wrapper avec asyncio.wait_for().
    """
    try:
        from bleak import BleakClient
    except ImportError:
        raise RuntimeError("bleak non installé — impossible d'imprimer via BLE")

    png_bytes = build_label_image(image_data)
    commands = _CMD_INIT + _image_to_commands(png_bytes) + _CMD_PRINT_END

    logger.info("BLE → %s (%d octets)", mac, len(commands))

    async with BleakClient(mac, timeout=10.0) as client:
        if not client.is_connected:
            raise RuntimeError(f"Connexion BLE échouée : {mac}")

        chunk_size = 182
        for i in range(0, len(commands), chunk_size):
            await client.write_gatt_char(_WRITE_UUID, commands[i:i + chunk_size], response=False)
            await asyncio.sleep(0.02)

        logger.info("Impression envoyée avec succès à %s", mac)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot (fire-and-forget dans un thread dédié)."""
    import threading

    def _run():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(send_to_printer(mac, lot_data))
        except Exception as e:
            logger.error("Impression lot échouée: %s", e)
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True).start()
