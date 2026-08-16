# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes via BLE pour imprimantes Phomemo M110.
Protocole reverse-engineered : https://gist.github.com/bdm-k/fe903491a051251db688866b7d554065

Largeur papier M110 : 40 mm → 384 pixels à 203 DPI.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from io import BytesIO

logger = logging.getLogger("domovra.printer")

# Largeur en pixels du M110 (203 DPI, 40mm)
PRINT_WIDTH = 384

# UUIDs BLE Phomemo (reverse-engineered, communs à M110/M120/M02)
_SERVICE_UUID  = "0000af30-0000-1000-8000-00805f9b34fb"
_WRITE_UUID    = "0000ae02-0000-1000-8000-00805f9b34fb"
_NOTIFY_UUID   = "0000ae01-0000-1000-8000-00805f9b34fb"

# Commandes protocole Phomemo
_CMD_INIT      = bytes([0x1b, 0x40])          # ESC @ — init imprimante
_CMD_PRINT_END = bytes([0x1b, 0x64, 0x04])    # ESC d 4 — fin impression + coupe

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


def _build_image_commands(img_1bit) -> bytes:
    """
    Convertit une image PIL 1-bit (mode '1') en commandes binaires Phomemo.
    Chaque ligne = commande GS v 0 + largeur (48 octets pour 384px) + données.
    """
    from PIL import Image

    w, h = img_1bit.size
    if w != PRINT_WIDTH:
        img_1bit = img_1bit.resize((PRINT_WIDTH, int(h * PRINT_WIDTH / w)), Image.LANCZOS)

    img_1bit = img_1bit.convert("1")
    w, h = img_1bit.size

    bytes_per_row = PRINT_WIDTH // 8  # 48 octets
    buf = bytearray()

    for y in range(h):
        row = bytearray(bytes_per_row)
        for x in range(PRINT_WIDTH):
            pixel = img_1bit.getpixel((x, y))
            # pixel = 0 → noir (imprimé), pixel = 255 → blanc
            if pixel == 0:
                row[x // 8] |= (0x80 >> (x % 8))
        # GS v 0 — commande impression raster ligne par ligne
        buf += bytes([0x1d, 0x76, 0x30, 0x00])
        buf += struct.pack("<HH", bytes_per_row, 1)
        buf += row

    return bytes(buf)


def build_label_image(data: dict) -> bytes:
    """
    Génère une image d'étiquette à partir des données d'un lot.
    Retourne les bytes PNG de l'image (pour prévisualisation ou impression).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow non installé — impossible de générer l'étiquette")

    img = Image.new("1", (PRINT_WIDTH, 220), color=1)  # 1 = blanc
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font_title = ImageFont.load_default()
        font_body  = font_title
        font_small = font_title

    y = 8

    name = str(data.get("name") or data.get("article_name") or data.get("product") or "Produit")
    if len(name) > 28:
        name = name[:25] + "..."
    draw.text((8, y), name, font=font_title, fill=0)
    y += 36

    draw.line([(8, y), (PRINT_WIDTH - 8, y)], fill=0, width=1)
    y += 8

    qty  = data.get("qty", "")
    unit = data.get("unit", "")
    draw.text((8, y), f"Qte : {qty} {unit}".strip(), font=font_body, fill=0)
    y += 28

    best_before = data.get("best_before") or ""
    if best_before:
        status = data.get("status", "green")
        dlc_prefix = {"green": "DLC", "yellow": "DLC !", "red": "DLC !!!"}.get(status, "DLC")
        draw.text((8, y), f"{dlc_prefix} : {best_before}", font=font_body, fill=0)
        y += 28

    location = data.get("location") or ""
    if location:
        draw.text((8, y), f"Lieu : {location}", font=font_small, fill=0)
        y += 24

    brand = data.get("brand") or ""
    store = data.get("store") or ""
    info = " | ".join(filter(None, [brand, store]))
    if info:
        draw.text((8, y), info, font=font_small, fill=0)
        y += 22

    img = img.crop((0, 0, PRINT_WIDTH, y + 10))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_test_label_image() -> bytes:
    """Génère une étiquette de test."""
    return build_label_image(_TEST_DATA)


async def send_to_printer(mac: str, image_data: dict) -> None:
    """
    Connexion BLE + envoi des commandes d'impression (async).
    Lève une exception en cas d'erreur (à wrapper avec asyncio.wait_for pour le timeout).
    """
    try:
        from bleak import BleakClient
    except ImportError:
        raise RuntimeError("bleak non installé — impossible d'imprimer via BLE")

    from PIL import Image
    img_bytes = build_label_image(image_data)
    img = Image.open(BytesIO(img_bytes)).convert("1")
    commands = _CMD_INIT + _build_image_commands(img) + _CMD_PRINT_END

    logger.info("Connexion BLE → %s (%d octets)", mac, len(commands))
    async with BleakClient(mac, timeout=10.0) as client:
        if not client.is_connected:
            raise RuntimeError(f"Impossible de se connecter à {mac}")

        chunk_size = 182
        for i in range(0, len(commands), chunk_size):
            chunk = commands[i:i + chunk_size]
            await client.write_gatt_char(_WRITE_UUID, chunk, response=False)
            await asyncio.sleep(0.02)

        logger.info("Impression envoyée avec succès à %s", mac)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot (fire-and-forget dans un thread asyncio dédié)."""
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
