# domovra/app/settings_store.py
import json
import os
import tempfile
import shutil
import threading
import time
import logging
from typing import Any, Dict

LOGGER = logging.getLogger("domovra.settings_store")

DATA_DIR = "/data"
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# Cache en mémoire : évite de relire le fichier à chaque render
# Durée de vie : 10 secondes. Invalidé immédiatement après un save.
_cache_lock = threading.Lock()
_cache_data: Dict[str, Any] | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 10.0  # secondes

DEFAULTS: Dict[str, Any] = {
    "theme": "auto",                 # auto | light | dark
    "sidebar_compact": False,        # bool

    # Toasts (désormais persistés)
    "toast_duration": 3000,          # int >= 500
    "toast_ok": "#4caf50",           # hex #rgb | #rrggbb
    "toast_warn": "#ffb300",
    "toast_error": "#ef5350",

    # Seuils DLC gérés par l'UI
    "retention_days_warning": 30,
    "retention_days_critical": 14,

    # Panel Avancé
    "enable_scanner": True,          # bool — active le scanner caméra dans Achats
    "enable_off_block": True,        # bool — affiche le bloc EAN/OFF dans Achats
    "ha_notifications": False,       # bool — notifications HA (non implémenté)

    # Journal
    "log_retention_days": 30,        # int >= 0
    "log_consumption": True,         # bool
    "log_add_remove": True,          # bool

    # Divers
    "ask_move_on_delete": False,     # bool
}

def _is_hex_color(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s.startswith("#"):
        return False
    h = s[1:]
    return len(h) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in h)

def _only_known_keys(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Ne conserve que les clés connues de DEFAULTS."""
    return {k: raw.get(k, DEFAULTS[k]) for k in DEFAULTS.keys()}

def _coerce_types(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fusionne avec DEFAULTS et applique des validations/coercitions."""
    clean_in = _only_known_keys(raw or {})
    out = DEFAULTS.copy()
    out.update(clean_in)

    if out["theme"] not in ("auto", "light", "dark"):
        out["theme"] = "auto"

    for k in ("sidebar_compact", "enable_scanner", "enable_off_block",
              "ha_notifications", "log_consumption", "log_add_remove",
              "ask_move_on_delete"):
        out[k] = bool(out.get(k, DEFAULTS[k]))

    def _int_ge0(v, dflt):
        try:
            return max(0, int(v))
        except Exception:
            return dflt

    out["toast_duration"] = max(500, _int_ge0(out.get("toast_duration"), DEFAULTS["toast_duration"]))
    out["retention_days_warning"] = _int_ge0(
        out.get("retention_days_warning", DEFAULTS["retention_days_warning"]),
        DEFAULTS["retention_days_warning"],
    )
    out["retention_days_critical"] = _int_ge0(
        out.get("retention_days_critical", DEFAULTS["retention_days_critical"]),
        DEFAULTS["retention_days_critical"],
    )
    out["log_retention_days"] = _int_ge0(
        out.get("log_retention_days", DEFAULTS["log_retention_days"]),
        DEFAULTS["log_retention_days"],
    )

    # Garde-fou logique : rouge <= jaune
    if out["retention_days_critical"] > out["retention_days_warning"]:
        out["retention_days_critical"] = out["retention_days_warning"]

    for k in ("toast_ok", "toast_warn", "toast_error"):
        v = str(out.get(k, DEFAULTS[k])).strip()
        out[k] = v if _is_hex_color(v) else DEFAULTS[k]

    return out


def _invalidate_cache() -> None:
    global _cache_data, _cache_ts
    with _cache_lock:
        _cache_data = None
        _cache_ts = 0.0


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix="settings.", suffix=".tmp"
    )
    os.close(fd)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    shutil.move(tmp_path, path)

def load_settings() -> Dict[str, Any]:
    global _cache_data, _cache_ts

    # Lecture depuis le cache si encore frais
    now = time.monotonic()
    with _cache_lock:
        if _cache_data is not None and (now - _cache_ts) < _CACHE_TTL:
            return _cache_data.copy()

    ensure_data_dir()
    if not os.path.exists(SETTINGS_PATH):
        LOGGER.info("settings.json introuvable, création avec valeurs par défaut")
        save_settings(DEFAULTS)
        result = DEFAULTS.copy()
        with _cache_lock:
            _cache_data = result.copy()
            _cache_ts = time.monotonic()
        return result

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)

        unknown = set(raw.keys()) - set(DEFAULTS.keys())
        missing = set(DEFAULTS.keys()) - set(raw.keys())
        if unknown or missing:
            if unknown:
                LOGGER.info("Nettoyage des clés obsolètes dans settings.json: %s", sorted(unknown))
            if missing:
                LOGGER.info("Ajout des nouvelles clés dans settings.json: %s", sorted(missing))
            cleaned = _coerce_types(raw)
            _atomic_write_json(SETTINGS_PATH, cleaned)
            result = cleaned
        else:
            result = _coerce_types(raw)

        LOGGER.debug("Chargement settings: %s", result)
        with _cache_lock:
            _cache_data = result.copy()
            _cache_ts = time.monotonic()
        return result

    except Exception as e:
        LOGGER.exception("Erreur de lecture settings.json: %s", e)
        return DEFAULTS.copy()

def save_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Enregistre uniquement les clés officielles, avec coercition/validation."""
    ensure_data_dir()
    try:
        filtered = _only_known_keys(payload or {})
        data = _coerce_types(filtered)
        _atomic_write_json(SETTINGS_PATH, data)
        LOGGER.info("Paramètres enregistrés: %s", data)
        # Invalide le cache immédiatement
        _invalidate_cache()
        return data
    except Exception as e:
        LOGGER.exception("Erreur d'écriture settings.json: %s", e)
        raise
