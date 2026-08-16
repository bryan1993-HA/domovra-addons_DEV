# domovra/app/services/printer.py
"""
Service d'impression d'étiquettes pour imprimantes Phomemo M110.

Transport : BLE GATT via socket L2CAP ATT brut (sans D-Bus).
  - host_network: true requis dans config.json
  - Connexion BLE directe au kernel (pas via bluetoothd/D-Bus)
  - L'imprimante doit être allumée et en mode BLE actif

Protocole raster : Tomaszu97 — 43 bytes/ligne, 240 lignes.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import os
import struct
import subprocess
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

def _build_payload(data: dict, ble: bool = False) -> bytes:
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


# ── BLE GATT : helpers bas niveau ────────────────────────────
#
# struct sockaddr_l2 (linux/bluetooth/l2cap.h) :
#   __u16  l2_family      AF_BLUETOOTH = 31
#   __le16 l2_psm         0 pour ATT fixed channel
#   bdaddr_t l2_bdaddr    6 octets, ordre inversé
#   __le16 l2_cid         4 = ATT fixed channel BLE
#   __u8   l2_bdaddr_type 1=LE_PUBLIC  2=LE_RANDOM
#   __u8   <padding>      alignement struct à 2 octets

_AF_BLUETOOTH  = 31
_BTPROTO_L2CAP = 0
_ATT_CID       = 4
_LE_PUBLIC     = 1
_LE_RANDOM     = 2

_SOL_BLUETOOTH      = 274
_BT_SECURITY        = 4
_BT_SECURITY_LOW    = 1
_BT_CHANNEL_POLICY  = 10
_BT_CHANNEL_POLICY_LE_PREFERRED = 2

_ATT_ERROR_RSP        = 0x01
_ATT_EXCHANGE_MTU_REQ = 0x02
_ATT_EXCHANGE_MTU_RSP = 0x03
_ATT_READ_BY_GRP_REQ  = 0x10
_ATT_READ_BY_GRP_RSP  = 0x11
_ATT_READ_BY_TYPE_REQ = 0x08
_ATT_READ_BY_TYPE_RSP = 0x09
_ATT_WRITE_CMD        = 0x52


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
    addr._pad = 0
    return addr


def _set_connect_timeout(s: socket.socket, seconds: int) -> None:
    """SO_SNDTIMEO : timeout pour connect() bloquant."""
    timeval = struct.pack("ll", seconds, 0)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, timeval)
    except OSError:
        pass


def _ble_connect(mac: str, addr_type: int, timeout: int = 10) -> socket.socket:
    """
    Ouvre une connexion BLE L2CAP ATT vers le M110.
    L'imprimante doit être allumée et en mode BLE actif.
    Lève OSError si connexion impossible dans le délai.
    """
    libc = _get_libc()
    libc.connect.restype = ctypes.c_int
    libc.bind.restype    = ctypes.c_int

    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    try:
        # SO_SNDTIMEO : timeout bloquant pour connect()
        _set_connect_timeout(s, timeout)

        # BT_SECURITY LOW (requis sur certains kernels avant connect)
        try:
            s.setsockopt(_SOL_BLUETOOTH, _BT_SECURITY,
                         struct.pack("BB", _BT_SECURITY_LOW, 0))
        except OSError:
            pass

        # Bind local (any adapter, ATT CID, LE_PUBLIC)
        local = _make_l2_addr("00:00:00:00:00:00", _ATT_CID, _LE_PUBLIC)
        try:
            libc.bind(s.fileno(), ctypes.byref(local), ctypes.sizeof(local))
        except OSError:
            pass

        # Connexion distante
        remote = _make_l2_addr(mac, _ATT_CID, addr_type)
        ret = libc.connect(s.fileno(), ctypes.byref(remote), ctypes.sizeof(remote))
        if ret < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))

        # Restaure un timeout de lecture/écriture classique
        s.settimeout(float(timeout))
        logger.info("BLE ATT connecté à %s (addr_type=%d)", mac, addr_type)
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
            return struct.unpack_from("<H", rsp, 1)[0]
    except Exception:
        pass
    return 23


def _uuid_str(raw: bytes) -> str:
    if len(raw) == 2:
        return f"0x{struct.unpack_from('<H', raw)[0]:04x}"
    if len(raw) == 16:
        h = raw[::-1].hex()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    return raw.hex()


# ── Diagnostic complet ────────────────────────────────────────

def _run_cmd(cmd: list[str], timeout: int = 5) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(),
                "stderr": r.stderr.strip(), "rc": r.returncode}
    except FileNotFoundError:
        return {"ok": False, "error": f"{cmd[0]}: introuvable"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _find_sockets(paths: list[str]) -> list[str]:
    import stat
    found = []
    for base in paths:
        try:
            for root, _dirs, files in os.walk(base):
                for fn in files:
                    full = os.path.join(root, fn)
                    try:
                        if stat.S_ISSOCK(os.stat(full).st_mode):
                            found.append(full)
                    except OSError:
                        pass
        except OSError:
            pass
    return found


def diagnose_ble(mac: str) -> dict:
    """
    Diagnostic BLE complet. Chaque connect L2CAP est limité à 5s (SO_SNDTIMEO).
    L'imprimante DOIT être allumée et active pendant ce test.
    """
    results: dict = {"mac": mac, "note": "Imprimante doit etre allumee pendant ce test", "tests": {}}
    t = results["tests"]

    # 1. Interfaces HCI
    try:
        t["hci_devices"] = os.listdir("/sys/class/bluetooth")
    except OSError as e:
        t["hci_devices"] = str(e)

    # 2. Sockets D-Bus / Unix
    t["dbus_sockets"] = _find_sockets(["/run", "/var/run", "/tmp"])

    # 3. Commandes BT
    for cmd_name in ["bluetoothctl", "hciconfig", "hcitool", "gatttool", "btmgmt"]:
        t[f"cmd_{cmd_name}"] = _run_cmd(["which", cmd_name])

    # 4. hciconfig
    t["hciconfig"] = _run_cmd(["hciconfig", "-a"], timeout=5)

    # 5. btmgmt info
    t["btmgmt_info"] = _run_cmd(["btmgmt", "info"], timeout=5)

    # 6. Création socket L2CAP
    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        s.close()
        t["l2cap_socket_create"] = {"ok": True}
    except OSError as e:
        t["l2cap_socket_create"] = {"ok": False, "errno": e.errno, "msg": str(e)}

    # 7. Connexions L2CAP ATT : variantes (5s timeout chacune)
    libc = _get_libc()
    libc.connect.restype = ctypes.c_int
    libc.bind.restype    = ctypes.c_int
    CONN_TIMEOUT = 5

    def _try(label: str, addr_type: int, do_bind: bool, do_security: bool):
        s = None
        try:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                              socket.BTPROTO_L2CAP)
            _set_connect_timeout(s, CONN_TIMEOUT)

            if do_security:
                try:
                    s.setsockopt(_SOL_BLUETOOTH, _BT_SECURITY,
                                 struct.pack("BB", _BT_SECURITY_LOW, 0))
                except OSError as e:
                    t[label + "_setsockopt"] = {"errno": e.errno, "msg": str(e)}

            if do_bind:
                local = _make_l2_addr("00:00:00:00:00:00", _ATT_CID, _LE_PUBLIC)
                try:
                    libc.bind(s.fileno(), ctypes.byref(local), ctypes.sizeof(local))
                except OSError as e:
                    t[label + "_bind"] = {"errno": e.errno, "msg": str(e)}

            remote = _make_l2_addr(mac, _ATT_CID, addr_type)
            ret = libc.connect(s.fileno(), ctypes.byref(remote), ctypes.sizeof(remote))
            if ret < 0:
                err = ctypes.get_errno()
                t[label] = {"ok": False, "errno": err, "msg": os.strerror(err)}
            else:
                t[label] = {"ok": True, "msg": "CONNEXION BLE ATT REUSSIE"}
        except OSError as e:
            t[label] = {"ok": False, "errno": e.errno, "msg": str(e)}
        except Exception as e:
            t[label] = {"ok": False, "msg": str(e)}
        finally:
            if s:
                try: s.close()
                except Exception: pass

    _try("ble_public_no_bind",  _LE_PUBLIC, False, False)
    _try("ble_public_bind",     _LE_PUBLIC, True,  False)
    _try("ble_public_full",     _LE_PUBLIC, True,  True)
    _try("ble_random_no_bind",  _LE_RANDOM, False, False)
    _try("ble_random_bind",     _LE_RANDOM, True,  False)
    _try("ble_random_full",     _LE_RANDOM, True,  True)

    # 8. RFCOMM canaux 2-5 (3s timeout)
    rfcomm = {}
    for ch in [2, 3, 4, 5]:
        try:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            s.settimeout(3)
            s.connect((mac, ch))
            rfcomm[ch] = "connecte"
            s.close()
        except OSError as e:
            rfcomm[ch] = f"errno={e.errno} {e.strerror}"
        except Exception as e:
            rfcomm[ch] = str(e)
    t["rfcomm_channels_2_5"] = rfcomm

    return results


# ── Découverte GATT ───────────────────────────────────────────

def discover_ble_characteristics(mac: str, timeout: int = 15) -> dict:
    """Connexion BLE ATT + découverte GATT complète. Imprimante doit être allumée."""
    sock = None
    connected_type = -1

    for addr_type in (_LE_PUBLIC, _LE_RANDOM):
        try:
            sock = _ble_connect(mac, addr_type, timeout=timeout)
            connected_type = addr_type
            break
        except OSError as e:
            logger.debug("discover addr_type=%d : %s", addr_type, e)

    if sock is None:
        return {"ok": False,
                "error": "Connexion BLE impossible — imprimante allumée ? Réessayer après avoir appuyé sur le bouton de l'imprimante."}

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
            item_len = rsp[1]
            data = rsp[2:]
            done = False
            while len(data) >= item_len:
                item = data[:item_len]
                sh, eh = struct.unpack_from("<HH", item)
                svc = {"start": f"0x{sh:04x}", "end": f"0x{eh:04x}",
                       "uuid": _uuid_str(item[4:item_len]), "characteristics": []}
                services.append(svc)
                data = data[item_len:]
                h = eh + 1
                if eh == 0xFFFF:
                    done = True
            if done:
                break

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
                    labels = []
                    if props & 0x02: labels.append("Read")
                    if props & 0x04: labels.append("WriteNoResp")
                    if props & 0x08: labels.append("Write")
                    if props & 0x10: labels.append("Notify")
                    if props & 0x20: labels.append("Indicate")
                    svc["characteristics"].append({
                        "decl_handle": f"0x{decl_h:04x}",
                        "value_handle": f"0x{val_h:04x}",
                        "props": f"0x{props:02x}",
                        "props_labels": labels,
                        "uuid": cuuid,
                        "writable": bool(props & 0x0C),
                    })
                    cdata = cdata[clen:]
                    ch = val_h + 1
                else:
                    break

        return {
            "ok": True,
            "mtu": mtu,
            "addr_type": "public" if connected_type == _LE_PUBLIC else "random",
            "services": services,
        }

    except Exception as e:
        logger.exception("Découverte GATT : %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        try: sock.close()
        except Exception: pass


# ── Impression BLE GATT ───────────────────────────────────────

def send_ble_gatt(mac: str, payload: bytes, handle: int, timeout: int = 20) -> None:
    """Envoi payload vers M110 via BLE GATT Write Without Response."""
    for addr_type in (_LE_PUBLIC, _LE_RANDOM):
        try:
            sock = _ble_connect(mac, addr_type, timeout=timeout)
            break
        except OSError:
            pass
    else:
        raise OSError("Connexion BLE GATT impossible")

    try:
        mtu = _att_exchange_mtu(sock)
        chunk = max(1, mtu - 3)
        for i in range(0, len(payload), chunk):
            pkt = struct.pack("<BH", _ATT_WRITE_CMD, handle) + payload[i:i + chunk]
            sock.send(pkt)
            time.sleep(0.005)
    finally:
        try: sock.close()
        except Exception: pass


async def send_to_printer_ble(mac: str, image_data: dict, handle: int) -> None:
    payload = _build_payload(image_data, ble=True)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_ble_gatt, mac, payload, handle)


# ── Transport RFCOMM (secours — ne fonctionne pas sur M110) ──

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
    payload = _build_payload(image_data, ble=False)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_rfcomm, mac, payload)


def print_lot(mac: str, lot_data: dict) -> None:
    import threading
    def _run():
        try:
            payload = _build_payload(lot_data, ble=False)
            _send_rfcomm(mac, payload)
        except Exception as e:
            logger.error("Impression lot échouée: %s", e)
    threading.Thread(target=_run, daemon=True).start()
