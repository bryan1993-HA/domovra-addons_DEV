# config.py
import os
import time

# --- Seuils DLC --------------------------------------------------------------
# Priorité :
#   1. /data/options.json  (config HA Supervisor — UI add-on)
#   2. settings_store      (réglages in-app)
#   3. Variables d'env WARNING_DAYS / CRITICAL_DAYS
#   4. Défauts codés en dur (30 / 14 jours)


def _options_json_thresholds():
    """Lit /data/options.json écrit par le Supervisor HA lors de la configuration add-on.
    Retourne (warning, critical) si les deux valeurs sont > 0, sinon None."""
    try:
        import json
        with open("/data/options.json") as f:
            opts = json.load(f)
        w = int(opts.get("retention_days_warning") or 0)
        c = int(opts.get("retention_days_critical") or 0)
        if w > 0 and c > 0:
            return w, c
    except Exception:
        pass
    return None


try:
    from settings_store import load_settings  # type: ignore

    def get_retention_thresholds():
        # 1. HA Supervisor options
        ha = _options_json_thresholds()
        if ha:
            return ha
        # 2. In-app settings
        s = load_settings() or {}
        w = int(s.get("retention_days_warning", 30))
        c = int(s.get("retention_days_critical", 14))
        return w, c

except Exception:
    def _env_int(name, default):
        try:
            v = os.environ.get(name)
            return int(v) if v not in (None, "") else default
        except Exception:
            return default

    def get_retention_thresholds():
        ha = _options_json_thresholds()
        if ha:
            return ha
        return _env_int("WARNING_DAYS", 30), _env_int("CRITICAL_DAYS", 14)


# Back-compat : certaines parties peuvent encore importer ces constantes
WARNING_DAYS, CRITICAL_DAYS = get_retention_thresholds()

# --- Base de données ---------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", "/data/domovra.sqlite3")

# --- Timestamp de démarrage (utilisé par utils/jinja) ------------------------
try:
    START_TS = int(os.environ.get("START_TS") or int(time.time()))
except Exception:
    START_TS = int(time.time())
