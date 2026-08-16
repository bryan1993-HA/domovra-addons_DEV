# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transport : BLE GATT via socket L2CAP ATT brut (sans D-Bus/bluetoothd).
  Le D-Bus du container ne dispose pas du contrôleur BlueZ (bluetoothd absent).
  On passe directement par le kernel BT avec host_network: true.

  Combinaison gagnante (diagnostic dev.40) :
    addr_type = LE_RANDOM  (le M110 annonce avec une adresse de type random)
    bind local avant connect
    BT_SECURITY_LOW avant connect
    select() pour timeout propre sur connect() non-bloquant

  L'imprimante DOIT être allumée et en mode attente (LED active) au moment
  de la connexion.

Protocole raster : Tomaszu97 — 43 bytes/ligne, 240 lignes.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import os
import select as _select
import struct
import time
from io import BytesIO
import socket

logger = logging.getLogger("domovra.printer")

# ── Constantes protocole M110 ─────────────────────────────────
PRINT_WIDTH   = 344
BYTES_PER_ROW = 43
BLOCK_LINES   = 240
RFCOMM_CHANNEL = 1

_HEADER = bytes([
    0x1B, 0x4E, 0x0D, 0x01,
    0x1B, 0x4E, 0x04, 0x0F,
    0x1F, 0x11, 0x0A,
])
_BLOCK_MARKER = bytes([0x1D, 0x76, 0x30, 0x00]) \
    + struct.pack("<H", BYTES_PER_ROW) \
    + struct.pack("<H", BLOCK_LINES)
_FOOTER = bytes([0x1F, 0xF0, 0x05, 0x00, 0x1F, 0xF0, 0x03, 0x00])

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

_WHITE_ROW = bytes(BYTES_PER_ROW)

# UUIDs write-without-response connus pour Phomemo M110 / M02
_KNOWN_WRITE_UUIDS = {
    "0000ff02-0000-1000-8000-00805f9b34fb",
    "0000ae01-0000-1000-8000-00805f9b34fb",
    "00008cf3-0000-1000-8000-00805f9b34fb",
}


# ── Image ────────────────────────────────────────────────────

def build_label_image(data: dict) -> bytes:
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

def _build_payload(data: dict, ble: bool = True) -> bytes:
    """
    ble=True  : pas de substitution 0x0A (BLE envoie du binaire brut).
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
            raw_rows.append(bytes(0x14 if b == 0x0A else b for b in row))

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


# ── BLE GATT via raw L2CAP ATT ───────────────────────────────
#
# struct sockaddr_l2 :
#   __u16  l2_family      AF_BLUETOOTH = 31
#   __le16 l2_psm         0 (ATT fixed channel)
#   bdaddr_t l2_bdaddr    6 octets, ordre inversé
#   __le16 l2_cid         4 (ATT)
#   __u8   l2_bdaddr_type 1=LE_PUBLIC  2=LE_RANDOM
#   __u8   pad

_AF_BLUETOOTH = 31
_ATT_CID      = 4
_LE_PUBLIC    = 1
_LE_RANDOM    = 2
_SOL_BLUETOOTH   = 274
_BT_SECURITY     = 4
_BT_SECURITY_LOW = 1

_ATT_EXCHANGE_MTU_REQ = 0x02
_ATT_EXCHANGE_MTU_RSP = 0x03
_ATT_READ_BY_GRP_REQ  = 0x10
_ATT_READ_BY_GRP_RSP  = 0x11
_ATT_READ_BY_TYPE_REQ = 0x08
_ATT_READ_BY_TYPE_RSP = 0x09
_ATT_ERROR_RSP        = 0x01
_ATT_WRITE_CMD        = 0x52   # Write Without Response


class _SockaddrL2(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("l2_family",      ctypes.c_uint16),
        ("l2_psm",         ctypes.c_uint16),
        ("l2_bdaddr",      ctypes.c_uint8 * 6),
        ("l2_cid",         ctypes.c_uint16),
        ("l2_bdaddr_type", ctypes.c_uint8),
        ("_pad",           ctypes.c_uint8),
    ]


def _mac_to_bytes(mac: str) -> bytes:
    return bytes(int(x, 16) for x in reversed(mac.split(":")))


def _get_libc() -> ctypes.CDLL:
    for name in [ctypes.util.find_library("c"),
                 "libc.musl-aarch64.so.1", "libc.musl-armhf.so.1", "libc.so.6"]:
        try:
            if name is None:
                continue
            lib = ctypes.CDLL(name, use_errno=True)
            _ = lib.connect
            return lib
        except (OSError, AttributeError):
            continue
    return ctypes.CDLL(None, use_errno=True)


def _make_l2_addr(mac: str, cid: int, addr_type: int) -> _SockaddrL2:
    addr = _SockaddrL2()
    addr.l2_family = _AF_BLUETOOTH
    addr.l2_psm = 0
    raw = _mac_to_bytes(mac)
    for i, b in enumerate(raw):
        addr.l2_bdaddr[i] = b
    addr.l2_cid = cid
    addr.l2_bdaddr_type = addr_type
    return addr


def _ble_connect(mac: str, timeout: float = 35.0) -> socket.socket:
    """
    Connexion BLE L2CAP ATT via raw socket kernel.
    Combinaison : LE_RANDOM + bind + BT_SECURITY_LOW + select() pour timeout.

    L'imprimante DOIT être allumée et en mode attente (LED active).
    Lève TimeoutError si pas de réponse dans le délai.
    """
    libc = _get_libc()
    libc.connect.restype = ctypes.c_int
    libc.bind.restype    = ctypes.c_int

    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    try:
        # BT_SECURITY LOW (requis pour que le kernel tente la connexion BLE)
        try:
            s.setsockopt(_SOL_BLUETOOTH, _BT_SECURITY,
                         struct.pack("BB", _BT_SECURITY_LOW, 0))
        except OSError as e:
            logger.debug("BT_SECURITY setsockopt: %s", e)

        # Bind local (any adapter, ATT CID, LE_PUBLIC pour local)
        local = _make_l2_addr("00:00:00:00:00:00", _ATT_CID, _LE_PUBLIC)
        try:
            libc.bind(s.fileno(), ctypes.byref(local), ctypes.sizeof(local))
        except OSError as e:
            logger.debug("bind local: %s", e)

        # Non-bloquant pour pouvoir utiliser select() avec timeout
        s.setblocking(False)

        remote = _make_l2_addr(mac, _ATT_CID, _LE_RANDOM)
        ret = libc.connect(s.fileno(), ctypes.byref(remote), ctypes.sizeof(remote))
        err = ctypes.get_errno()

        if ret == 0:
            logger.info("BLE ATT connexion immédiate à %s", mac)
        elif err in (115, 36):  # EINPROGRESS / EAGAIN — connexion en cours
            logger.info("BLE ATT connexion en cours vers %s (attente jusqu'à %.0fs)…", mac, timeout)
            _, writable, _ = _select.select([], [s], [], timeout)
            if not writable:
                raise TimeoutError(
                    f"L'imprimante ne répond pas après {timeout:.0f}s. "
                    f"Vérifier qu'elle est allumée et en mode attente (LED active)."
                )
            sock_err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if sock_err != 0:
                raise OSError(sock_err, os.strerror(sock_err))
            logger.info("BLE ATT connecté à %s", mac)
        else:
            raise OSError(err, os.strerror(err))

        s.setblocking(True)
        s.settimeout(15.0)
        return s

    except Exception:
        try: s.close()
        except Exception: pass
        raise


def _att_exchange_mtu(sock: socket.socket, wanted: int = 512) -> int:
    try:
        sock.send(struct.pack("<BH", _ATT_EXCHANGE_MTU_REQ, wanted))
        rsp = sock.recv(64)
        if len(rsp) >= 3 and rsp[0] == _ATT_EXCHANGE_MTU_RSP:
            mtu = struct.unpack_from("<H", rsp, 1)[0]
            logger.info("MTU négocié : %d", mtu)
            return mtu
    except Exception as e:
        logger.debug("MTU exchange : %s", e)
    return 23


def _uuid_str(raw: bytes) -> str:
    if len(raw) == 2:
        return f"0x{struct.unpack_from('<H', raw)[0]:04x}"
    if len(raw) == 16:
        h = raw[::-1].hex()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    return raw.hex()


def _find_write_handle(sock: socket.socket) -> tuple[int | None, str | None]:
    """
    Découverte ATT rapide : retourne (handle, uuid) de la première caractéristique
    write-without-response. UUID connus Phomemo en priorité.
    """
    best_handle: int | None = None
    best_uuid: str | None = None

    h = 0x0001
    while h <= 0xFFFF:
        try:
            pkt = struct.pack("<BHHH", _ATT_READ_BY_TYPE_REQ, h, 0xFFFF, 0x2803)
            sock.send(pkt)
            rsp = sock.recv(512)
        except Exception:
            break

        if not rsp or rsp[0] == _ATT_ERROR_RSP:
            break
        if rsp[0] != _ATT_READ_BY_TYPE_RSP:
            break

        clen = rsp[1]
        cdata = rsp[2:]
        last_val_h = h

        while len(cdata) >= clen:
            ci = cdata[:clen]
            props = ci[2]
            val_h = struct.unpack_from("<H", ci, 3)[0]
            cuuid = _uuid_str(ci[5:clen])
            cdata = cdata[clen:]
            last_val_h = val_h

            # Write Without Response (0x04) ou Write (0x08)
            if props & 0x04:
                # UUID connu Phomemo : priorité absolue
                if cuuid in _KNOWN_WRITE_UUIDS:
                    logger.info("Handle Phomemo trouvé : 0x%04x (%s)", val_h, cuuid)
                    return val_h, cuuid
                # Sinon : garde comme candidat
                if best_handle is None:
                    best_handle = val_h
                    best_uuid = cuuid

        h = last_val_h + 1

    if best_handle is not None:
        logger.info("Handle write trouvé : 0x%04x (%s)", best_handle, best_uuid)
    return best_handle, best_uuid


def discover_ble(mac: str, timeout: float = 35.0) -> dict:
    """
    Connexion BLE ATT + découverte GATT complète.
    L'imprimante doit être allumée et en mode attente.
    """
    try:
        sock = _ble_connect(mac, timeout)
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Connexion BLE impossible : {e}"}

    try:
        mtu = _att_exchange_mtu(sock)
        services = []

        h = 0x0001
        while h <= 0xFFFF:
            pkt = struct.pack("<BHHH", _ATT_READ_BY_GRP_REQ, h, 0xFFFF, 0x2800)
            sock.send(pkt)
            rsp = sock.recv(512)
            if not rsp or rsp[0] == _ATT_ERROR_RSP:
                break
            if rsp[0] != _ATT_READ_BY_GRP_RSP:
                break
            item_len = rsp[1]; data = rsp[2:]; done = False
            while len(data) >= item_len:
                item = data[:item_len]
                sh, eh = struct.unpack_from("<HH", item)
                svc = {"start": f"0x{sh:04x}", "end": f"0x{eh:04x}",
                       "uuid": _uuid_str(item[4:item_len]), "characteristics": []}
                services.append(svc)
                data = data[item_len:]; h = eh + 1
                if eh == 0xFFFF: done = True
            if done: break

        for svc in services:
            sh = int(svc["start"], 16); eh = int(svc["end"], 16); ch = sh
            while ch <= eh:
                try:
                    cpkt = struct.pack("<BHHH", _ATT_READ_BY_TYPE_REQ, ch, eh, 0x2803)
                    sock.send(cpkt); crsp = sock.recv(512)
                except Exception: break
                if not crsp or crsp[0] == _ATT_ERROR_RSP: break
                if crsp[0] != _ATT_READ_BY_TYPE_RSP: break
                clen = crsp[1]; cdata = crsp[2:]
                while len(cdata) >= clen:
                    ci = cdata[:clen]
                    decl_h = struct.unpack_from("<H", ci)[0]
                    props = ci[2]; val_h = struct.unpack_from("<H", ci, 3)[0]
                    cuuid = _uuid_str(ci[5:clen])
                    labels = [n for bit, n in [(0x02, "Read"), (0x04, "WriteNoResp"),
                                               (0x08, "Write"), (0x10, "Notify"),
                                               (0x20, "Indicate")] if props & bit]
                    svc["characteristics"].append({
                        "decl_handle": f"0x{decl_h:04x}",
                        "value_handle": f"0x{val_h:04x}",
                        "props": f"0x{props:02x}",
                        "props_labels": labels,
                        "uuid": cuuid,
                        "writable": bool(props & 0x0C),
                        "known_phomemo": cuuid in _KNOWN_WRITE_UUIDS,
                    })
                    cdata = cdata[clen:]; ch = val_h + 1
                else: break

        return {"ok": True, "mtu": mtu, "services": services}

    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: sock.close()
        except Exception: pass


def print_ble(mac: str, payload: bytes, timeout: float = 35.0) -> dict:
    """
    Impression via BLE GATT raw L2CAP ATT.
    Connexion → MTU → découverte handle → envoi payload en chunks.
    L'imprimante doit être allumée et en mode attente (LED active).
    """
    try:
        sock = _ble_connect(mac, timeout)
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Connexion BLE impossible : {e}"}

    try:
        mtu = _att_exchange_mtu(sock)
        chunk = max(1, mtu - 3)

        write_handle, write_uuid = _find_write_handle(sock)

        if write_handle is None:
            # Fallback : handle 0x0002 (très commun sur imprimantes thermiques BLE simples)
            write_handle = 0x0002
            write_uuid = "inconnu (fallback 0x0002)"
            logger.warning("Aucun handle trouvé par ATT — fallback handle 0x0002")

        logger.info("Impression BLE : %d octets → handle 0x%04x (%s), chunks de %d",
                    len(payload), write_handle, write_uuid, chunk)

        sent = 0
        for i in range(0, len(payload), chunk):
            pkt = struct.pack("<BH", _ATT_WRITE_CMD, write_handle) + payload[i:i + chunk]
            sock.send(pkt)
            sent += len(payload[i:i + chunk])
            time.sleep(0.005)

        return {
            "ok": True,
            "message": f"Impression envoyée : {sent} octets via handle 0x{write_handle:04x} ({write_uuid})",
        }

    except Exception as e:
        logger.exception("print_ble : %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        try: sock.close()
        except Exception: pass


# ── Transport RFCOMM (secours — ne fonctionne pas pour l'impression M110) ──

def _send_rfcomm(mac: str, payload: bytes, timeout: int = 15) -> None:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(timeout)
    try:
        sock.connect((mac, RFCOMM_CHANNEL))
        for i in range(0, len(payload), 512):
            sock.sendall(payload[i:i + 512])
    finally:
        try: sock.close()
        except Exception: pass


async def send_to_printer(mac: str, image_data: dict) -> None:
    """Wrapper async pour compatibilité (utilise RFCOMM — ne print pas sur M110)."""
    payload = _build_payload(image_data, ble=False)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_rfcomm, mac, payload)


def print_lot(mac: str, lot_data: dict) -> None:
    """Lance l'impression d'un lot en BLE (fire-and-forget)."""
    import threading
    def _run():
        try:
            payload = _build_payload(lot_data, ble=True)
            result = print_ble(mac, payload)
            if not result.get("ok"):
                logger.error("Impression lot BLE : %s", result.get("error"))
        except Exception as e:
            logger.error("Impression lot : %s", e)
    threading.Thread(target=_run, daemon=True).start()
