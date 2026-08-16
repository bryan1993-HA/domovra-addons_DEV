# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transport (essayé dans l'ordre) :
  1. BLE GATT via bleak — si DBUS_SYSTEM_BUS_ADDRESS est défini (host_network + D-Bus host)
  2. RFCOMM classique — socket AF_BLUETOOTH noyau (host_network requis)
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
from io import BytesIO

logger = logging.getLogger("domovra.printer")

PRINT_WIDTH   = 320
BYTES_PER_ROW = PRINT_WIDTH // 8

_RFCOMM_CHANNEL = 1
_WRITE_UUID     = "0000ae02-0000-1000-8000-00805f9b34fb"

_CMD_INIT      = bytes([0x1b, 0x40])
_CMD_PRINT_END = bytes([0x1b, 0x64, 0x02])

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
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow non installé")

    img = Image.new("1", (PRINT_WIDTH, 220), color=1)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = font_small = font_title

    y = 6
    name = str(data.get("name") or data.get("article_name") or data.get("product") or "Produit")
    if len(name) > 24:
        name = name[:21] + "..."
    draw.text((6, y), name, font=font_title, fill=0); y += 30
    draw.line([(6, y), (PRINT_WIDTH - 6, y)], fill=0, width=1); y += 6

    qty = data.get("qty", ""); unit = data.get("unit", "")
    draw.text((6, y), f"Qte : {qty} {unit}".strip(), font=font_body, fill=0); y += 24

    best_before = data.get("best_before") or ""
    if best_before:
        status = data.get("status", "green")
        dlc_prefix = {"green": "DLC", "yellow": "DLC !", "red": "DLC !!!"}.get(status, "DLC")
        draw.text((6, y), f"{dlc_prefix} : {best_before}", font=font_body, fill=0); y += 24

    location = data.get("location") or ""
    if location:
        draw.text((6, y), f"Lieu : {location}", font=font_small, fill=0); y += 20

    brand = data.get("brand") or ""; store = data.get("store") or ""
    info = " | ".join(filter(None, [brand, store]))
    if info:
        draw.text((6, y), info, font=font_small, fill=0); y += 18

    img = img.crop((0, 0, PRINT_WIDTH, y + 8))
    buf = BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def build_test_label_image() -> bytes:
    return build_label_image(_TEST_DATA)


# ──────────────────────────── Protocole ────────────────────────────

def _image_to_commands(png_bytes: bytes) -> bytes:
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
            if img.getpixel((x, y)) == 0:
                row[x // 8] |= (0x80 >> (x % 8))
        buf += bytes([0x1d, 0x76, 0x30, 0x00])
        buf += struct.pack("<HH", BYTES_PER_ROW, 1)
        buf += row
    return bytes(buf)


# ──────────────────────────── Transport ────────────────────────────

def _has_dbus() -> bool:
    """Vérifie si un socket D-Bus système est accessible."""
    addr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
    if addr.startswith("unix:path="):
        path = addr[len("unix:path="):]
        return os.path.exists(path)
    return os.path.exists("/run/dbus/system_bus_socket")


async def _send_ble(mac: str, commands: bytes) -> None:
    """Envoi BLE via bleak (nécessite D-Bus BlueZ accessible)."""
    from bleak import BleakClient
    logger.info("BLE GATT → %s (%d octets)", mac, len(commands))
    async with BleakClient(mac, timeout=10.0) as client:
        if not client.is_connected:
            raise RuntimeError(f"Connexion BLE échouée : {mac}")
        for i in range(0, len(commands), 182):
            await client.write_gatt_char(_WRITE_UUID, commands[i:i + 182], response=False)
            await asyncio.sleep(0.02)
    logger.info("BLE OK → %s", mac)


def _send_rfcomm(mac: str, commands: bytes, timeout: int = 15) -> None:
    """Envoi Bluetooth classique RFCOMM (pas de D-Bus, host_network requis)."""
    logger.info("RFCOMM → %s canal %d (%d octets)", mac, _RFCOMM_CHANNEL, len(commands))
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(timeout)
    try:
        sock.connect((mac, _RFCOMM_CHANNEL))
        for i in range(0, len(commands), 512):
            sock.sendall(commands[i:i + 512])
        logger.info("RFCOMM OK → %s", mac)
    finally:
        try:
            sock.close()
        except Exception:
            pass


async def send_to_printer(mac: str, image_data: dict) -> None:
    """
    Envoie l'étiquette à l'imprimante.
    Tente BLE si D-Bus disponible, sinon RFCOMM.
    """
    png_bytes = build_label_image(image_data)
    commands = _CMD_INIT + _image_to_commands(png_bytes) + _CMD_PRINT_END

    if _has_dbus():
        logger.info("Transport: BLE (D-Bus disponible)")
        await _send_ble(mac, commands)
    else:
        logger.info("Transport: RFCOMM (pas de D-Bus)")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_rfcomm, mac, commands)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot (fire-and-forget)."""
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
