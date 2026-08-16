# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transports (par ordre de préférence) :
  1. BLE GATT via bleak — si bluetoothd tourne (hci0 via host_network)
  2. RFCOMM classique — socket AF_BLUETOOTH noyau (host_network requis)

Références protocole :
  https://github.com/Tomaszu97/phomemo
  https://github.com/vivier/phomemo-tools
  https://github.com/hkeward/phomemo_printer
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
from io import BytesIO
import socket

logger = logging.getLogger("domovra.printer")

_RFCOMM_CHANNEL = 1
_BLE_WRITE_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

# ── Protocole RFCOMM : vivier/hkeward ESC/POS (48 bytes = 384 px) ──
_R_WIDTH    = 384
_R_BYTES    = 48
_R_MAXLINES = 256

_R_HEADER = bytes([
    0x1B, 0x40,              # ESC @ — init
    0x1B, 0x61, 0x01,        # ESC a 1 — centrer
    0x1F, 0x11, 0x02, 0x04,  # activation propriétaire
])
_R_FOOTER = bytes([
    0x1B, 0x64, 0x02,
    0x1B, 0x64, 0x02,
    0x1F, 0x11, 0x08,
    0x1F, 0x11, 0x0E,
    0x1F, 0x11, 0x07,
    0x1F, 0x11, 0x09,
])

# ── Protocole BLE GATT ──
_B_INIT      = bytes([0x1b, 0x40])
_B_PRINT_END = bytes([0x1b, 0x64, 0x02])

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

def _render(data: dict, width: int):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("1", (width, 220), color=1)
    draw = ImageDraw.Draw(img)
    try:
        ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        ft = fb = fs = ImageFont.load_default()
    y = 6
    name = str(data.get("name") or data.get("article_name") or data.get("product") or "Produit")
    if len(name) > width // 9:
        name = name[:width // 9 - 3] + "..."
    draw.text((6, y), name, font=ft, fill=0); y += 28
    draw.line([(6, y), (width - 6, y)], fill=0, width=1); y += 6
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
    return img.crop((0, 0, width, y + 8))


def build_label_image(data: dict) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError:
        raise RuntimeError("Pillow non installé")
    img = _render(data, _R_WIDTH)
    buf = BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def build_test_label_image() -> bytes:
    return build_label_image(_TEST_DATA)


# ── Protocole raster ────────────────────────────────────────

def _rows(img, width: int, bpr: int) -> list[bytes]:
    from PIL import Image as PI
    img = img.convert("1")
    w, h = img.size
    if w != width:
        img = img.resize((width, int(h * width / w)), PI.LANCZOS).convert("1")
        w, h = img.size
    result = []
    for y in range(h):
        row = bytearray(bpr)
        for x in range(width):
            if img.getpixel((x, y)) == 0:
                row[x // 8] |= (0x80 >> (x % 8))
        result.append(bytes(0x14 if b == 0x0A else b for b in row))
    return result


def _rfcomm_payload(data: dict) -> bytes:
    img = _render(data, _R_WIDTH)
    rs = _rows(img, _R_WIDTH, _R_BYTES)
    buf = bytearray(_R_HEADER)
    for i in range(0, len(rs), _R_MAXLINES):
        batch = rs[i:i + _R_MAXLINES]
        buf += bytes([0x1D, 0x76, 0x30, 0x00])
        buf += struct.pack("<H", _R_BYTES)
        buf += struct.pack("<H", len(batch))
        for r in batch:
            buf += r
    buf += _R_FOOTER
    return bytes(buf)


def _ble_payload(data: dict) -> bytes:
    img = _render(data, _R_WIDTH)
    rs = _rows(img, _R_WIDTH, _R_BYTES)
    buf = bytearray(_B_INIT)
    for i in range(0, len(rs), _R_MAXLINES):
        batch = rs[i:i + _R_MAXLINES]
        buf += bytes([0x1D, 0x76, 0x30, 0x00])
        buf += struct.pack("<H", _R_BYTES)
        buf += struct.pack("<H", len(batch))
        for r in batch:
            buf += r
    buf += _B_PRINT_END
    return bytes(buf)


# ── Transport ────────────────────────────────────────────────

def _has_dbus() -> bool:
    addr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
    if addr.startswith("unix:path="):
        return os.path.exists(addr[len("unix:path="):])
    return os.path.exists("/run/dbus/system_bus_socket")


def _send_rfcomm(mac: str, payload: bytes, timeout: int = 15) -> None:
    logger.info("RFCOMM → %s (%d octets)", mac, len(payload))
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


async def _send_ble(mac: str, payload: bytes) -> None:
    from bleak import BleakClient
    logger.info("BLE → %s (%d octets)", mac, len(payload))
    async with BleakClient(mac, timeout=10.0) as client:
        if not client.is_connected:
            raise RuntimeError(f"BLE connexion échouée : {mac}")
        for i in range(0, len(payload), 182):
            await client.write_gatt_char(_BLE_WRITE_UUID, payload[i:i + 182], response=False)
            await asyncio.sleep(0.02)
    logger.info("BLE OK → %s", mac)


async def send_to_printer(mac: str, image_data: dict) -> None:
    if _has_dbus():
        logger.info("Transport: BLE GATT")
        await _send_ble(mac, _ble_payload(image_data))
    else:
        logger.info("Transport: RFCOMM")
        payload = _rfcomm_payload(image_data)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_rfcomm, mac, payload)


def print_lot(mac: str, lot_data: dict) -> None:
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
