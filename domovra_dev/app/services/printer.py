# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transport principal : BLE GATT via socket L2CAP ATT brut (sans D-Bus).
  Fonctionne avec host_network: true dans config.json (accès kernel BT).
  Le M110 n'imprime PAS via RFCOMM canal 1 (canal config/statut uniquement).

Transport de secours : Bluetooth RFCOMM (SPP, canal 1) — connexion OK mais
  n'imprime pas sur M110.

Protocole raster : Tomaszu97 / M110 natif — 43 bytes/ligne, EXACTEMENT 240 lignes
  (padding blanc si image plus courte). Pas de substitution 0x0A pour BLE.

Références :
  https://github.com/Tomaszu97/phomemo
  https://github.com/vivier/phomemo-tools
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import os
import struct
import time
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

def _build_payload(data: dict, ble: bool = False) -> bytes:
    """
    Construit le payload complet pour le M110.
    ble=True : pas de substitution 0x0A (BLE GATT envoie du binaire brut).
    ble=False : substitution 0x0A→0x14 requise pour RFCOMM.
    """
    from PIL import Image

    png_bytes = build_label_image(data)
    img = Image.open(BytesIO(png_bytes)).convert("1")
    w, h = img.size

    if w != PRINT_WIDTH:
        img = img.resize((PRINT_WIDTH, int(h * PRINT_WIDTH / w)), Image.LANCZOS).convert("1")
        w, h = img.size

    raw_rows: list[bytes] = []
    for y in range(h):
        row = bytearray(BYTES_PER_ROW)
        for x in range(PRINT_WIDTH):
            if img.getpixel((x, y)) == 0:
                row[x // 8] |= (0x80 >> (x % 8))
        if ble:
            raw_rows.append(bytes(row))
        else:
            # RFCOMM : substitution 0x0A → 0x14 pour éviter interprétation LF
            raw_rows.append(bytes(0x14 if b == 0x0A else b for b in row))

    logger.info("Image raster : %d lignes x %d octets (ble=%s)", h, BYTES_PER_ROW, ble)

    buf = bytearray(_HEADER)

    for block_start in range(0, max(h, 1), BLOCK_LINES):
        block_rows = raw_rows[block_start:block_start + BLOCK_LINES]
        while len(block_rows) < BLOCK_LINES:
            block_rows.append(_WHITE_ROW)
        buf += _BLOCK_MARKER
        for row in block_rows:
            buf += row

    buf += _FOOTER
    return bytes(buf)


# ── BLE GATT via socket L2CAP ATT brut (sans D-Bus) ──────────
#
# Python's socket module ne supporte pas l2_cid ni l2_bdaddr_type
# pour BTPROTO_L2CAP. On appelle connect() directement via libc/ctypes.
#
# struct sockaddr_l2 (linux/bluetooth/l2cap.h) :
#   __u16  l2_family     (AF_BLUETOOTH = 31)
#   __le16 l2_psm        (0 pour ATT fixe)
#   bdaddr_t l2_bdaddr   (6 octets, ordre inversé)
#   __le16 l2_cid        (4 = ATT channel fixe BLE)
#   __u8   l2_bdaddr_type (1 = LE_PUBLIC, 2 = LE_RANDOM)

_AF_BLUETOOTH   = 31
_BTPROTO_L2CAP  = 0
_ATT_CID        = 4
_LE_PUBLIC      = 1
_LE_RANDOM      = 2

# ATT opcodes (Bluetooth Core Spec, Vol 3 Part F)
_ATT_ERROR_RSP          = 0x01
_ATT_EXCHANGE_MTU_REQ   = 0x02
_ATT_EXCHANGE_MTU_RSP   = 0x03
_ATT_READ_BY_GRP_REQ    = 0x10
_ATT_READ_BY_GRP_RSP    = 0x11
_ATT_READ_BY_TYPE_REQ   = 0x08
_ATT_READ_BY_TYPE_RSP   = 0x09
_ATT_WRITE_CMD          = 0x52   # Write Without Response


def _mac_to_bytes(mac: str) -> bytes:
    """MAC AA:BB:CC:DD:EE:FF → bytes inversés pour BT."""
    return bytes(int(x, 16) for x in reversed(mac.split(":")))


def _l2cap_sockaddr(bdaddr: bytes, cid: int, addr_type: int) -> bytes:
    """Construit struct sockaddr_l2 en bytes."""
    return struct.pack("<HH6sHB", _AF_BLUETOOTH, 0, bdaddr, cid, addr_type)


def _raw_connect(fd: int, sockaddr_bytes: bytes) -> None:
    """Appelle connect() via libc (contourne les restrictions Python pour L2CAP BLE)."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    buf = ctypes.create_string_buffer(sockaddr_bytes)
    ret = libc.connect(fd, buf, ctypes.c_int(len(sockaddr_bytes)))
    if ret < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _ble_connect(mac: str, timeout: float = 15.0) -> socket.socket:
    """
    Ouvre une connexion BLE L2CAP ATT.
    Essaie adresse publique puis aléatoire.
    Lève OSError si les deux échouent.
    """
    bdaddr = _mac_to_bytes(mac)
    last_err: Exception | None = None

    for addr_type in (_LE_PUBLIC, _LE_RANDOM):
        s: socket.socket | None = None
        try:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            s.settimeout(timeout)
            remote = _l2cap_sockaddr(bdaddr, _ATT_CID, addr_type)
            _raw_connect(s.fileno(), remote)
            logger.info("BLE ATT connecté à %s (addr_type=%d)", mac, addr_type)
            return s
        except Exception as e:
            if s:
                try: s.close()
                except Exception: pass
            last_err = e
            logger.debug("BLE ATT addr_type=%d échoué : %s", addr_type, e)

    raise OSError(f"BLE ATT connexion impossible : {last_err}")


def _att_exchange_mtu(sock: socket.socket, wanted: int = 512) -> int:
    """Échange MTU avec le serveur ATT. Retourne le MTU négocié."""
    try:
        sock.send(struct.pack("<BH", _ATT_EXCHANGE_MTU_REQ, wanted))
        rsp = sock.recv(64)
        if len(rsp) >= 3 and rsp[0] == _ATT_EXCHANGE_MTU_RSP:
            return struct.unpack_from("<H", rsp, 1)[0]
    except Exception as e:
        logger.debug("MTU exchange échoué : %s", e)
    return 23   # MTU BLE 4.0 par défaut


def _uuid_str(raw: bytes) -> str:
    if len(raw) == 2:
        return f"0x{struct.unpack_from('<H', raw)[0]:04x}"
    if len(raw) == 16:
        h = raw[::-1].hex()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    return raw.hex()


def discover_ble_characteristics(mac: str, timeout: float = 15.0) -> dict:
    """
    Connexion BLE au M110, découverte de tous les services et caractéristiques GATT.
    Retourne un dict avec ok, mtu, addr_type, et la liste des services+chars.
    """
    bdaddr = _mac_to_bytes(mac)
    last_err: Exception | None = None
    connected_addr_type = -1

    sock: socket.socket | None = None
    for addr_type in (_LE_PUBLIC, _LE_RANDOM):
        try:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            s.settimeout(timeout)
            _raw_connect(s.fileno(), _l2cap_sockaddr(bdaddr, _ATT_CID, addr_type))
            sock = s
            connected_addr_type = addr_type
            break
        except Exception as e:
            if s:
                try: s.close()
                except Exception: pass
            last_err = e

    if sock is None:
        return {"ok": False, "error": str(last_err)}

    try:
        mtu = _att_exchange_mtu(sock)
        services = []

        # ── Découverte des services primaires (0x2800) ──
        h = 0x0001
        while h <= 0xFFFF:
            pkt = struct.pack("<BHHH", _ATT_READ_BY_GRP_REQ, h, 0xFFFF, 0x2800)
            sock.send(pkt)
            rsp = sock.recv(512)
            if not rsp or rsp[0] == _ATT_ERROR_RSP:
                break
            if rsp[0] != _ATT_READ_BY_GRP_RSP:
                break
            item_len = rsp[1]
            data = rsp[2:]
            while len(data) >= item_len:
                item = data[:item_len]
                start_h, end_h = struct.unpack_from("<HH", item)
                svc = {
                    "start": f"0x{start_h:04x}",
                    "end": f"0x{end_h:04x}",
                    "uuid": _uuid_str(item[4:item_len]),
                    "characteristics": [],
                }
                services.append(svc)
                data = data[item_len:]
                h = end_h + 1
                if end_h == 0xFFFF:
                    h = 0x10000
            else:
                break

        # ── Découverte des caractéristiques dans chaque service ──
        for svc in services:
            sh = int(svc["start"], 16)
            eh = int(svc["end"], 16)
            ch = sh
            while ch <= eh:
                cpkt = struct.pack("<BHHH", _ATT_READ_BY_TYPE_REQ, ch, eh, 0x2803)
                sock.send(cpkt)
                crsp = sock.recv(512)
                if not crsp or crsp[0] == _ATT_ERROR_RSP:
                    break
                if crsp[0] != _ATT_READ_BY_TYPE_RSP:
                    break
                clen = crsp[1]
                cdata = crsp[2:]
                while len(cdata) >= clen:
                    ci = cdata[:clen]
                    decl_h = struct.unpack_from("<H", ci)[0]
                    props = ci[2]
                    val_h = struct.unpack_from("<H", ci, 3)[0]
                    cuuid = _uuid_str(ci[5:clen])
                    prop_labels = []
                    if props & 0x02: prop_labels.append("Read")
                    if props & 0x04: prop_labels.append("WriteNoResp")
                    if props & 0x08: prop_labels.append("Write")
                    if props & 0x10: prop_labels.append("Notify")
                    if props & 0x20: prop_labels.append("Indicate")
                    svc["characteristics"].append({
                        "decl_handle": f"0x{decl_h:04x}",
                        "value_handle": f"0x{val_h:04x}",
                        "properties": f"0x{props:02x}",
                        "props_labels": prop_labels,
                        "uuid": cuuid,
                        "writable": bool(props & 0x0C),
                    })
                    cdata = cdata[clen:]
                    ch = val_h + 1
                else:
                    break

        addr_type_label = "public" if connected_addr_type == _LE_PUBLIC else "random"
        return {
            "ok": True,
            "mtu": mtu,
            "addr_type": addr_type_label,
            "services": services,
        }

    except Exception as e:
        logger.exception("Découverte BLE échouée : %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        try: sock.close()
        except Exception: pass


def send_ble_gatt(mac: str, payload: bytes, handle: int, timeout: float = 30.0) -> None:
    """
    Envoie le payload au M110 via BLE GATT Write Without Response.
    handle : handle de la caractéristique d'écriture (ex : 0x0006).
    """
    sock = _ble_connect(mac, timeout=timeout)
    try:
        mtu = _att_exchange_mtu(sock)
        chunk_size = max(1, mtu - 3)  # opcode(1) + handle(2)
        logger.info("BLE GATT envoi %d octets → handle 0x%04x, chunk=%d", len(payload), handle, chunk_size)
        sent = 0
        for i in range(0, len(payload), chunk_size):
            chunk = payload[i:i + chunk_size]
            pkt = struct.pack("<BH", _ATT_WRITE_CMD, handle) + chunk
            sock.send(pkt)
            sent += len(chunk)
            time.sleep(0.005)
        logger.info("BLE GATT terminé : %d octets envoyés", sent)
    finally:
        try: sock.close()
        except Exception: pass


async def send_to_printer_ble(mac: str, image_data: dict, handle: int) -> None:
    """Impression via BLE GATT (async wrapper)."""
    payload = _build_payload(image_data, ble=True)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_ble_gatt, mac, payload, handle)


# ── Transport RFCOMM (secours — ne fonctionne pas sur M110) ──

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
    payload = _build_payload(image_data, ble=False)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_rfcomm, mac, payload)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot (fire-and-forget, RFCOMM)."""
    import threading
    def _run():
        try:
            payload = _build_payload(lot_data, ble=False)
            _send_rfcomm(mac, payload)
        except Exception as e:
            logger.error("Impression lot échouée: %s", e)
    threading.Thread(target=_run, daemon=True).start()
