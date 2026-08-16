# domovra/run.sh
#!/usr/bin/with-contenv bash
set -euo pipefail

# ─────────────── Stack Bluetooth (dbus + bluetoothd) ───────────────
# bluetooth:true dans config.json donne accès au HCI adapter.
# On démarre notre propre D-Bus + BlueZ pour que bleak puisse l'utiliser.

mkdir -p /run/dbus

# 1. D-Bus system daemon
if ! pgrep -x dbus-daemon > /dev/null 2>&1; then
  echo "[Domovra] Démarrage dbus-daemon..."
  dbus-daemon --system --fork 2>/dev/null \
    && echo "[Domovra] dbus-daemon OK" \
    || echo "[Domovra] WARN: dbus-daemon indisponible"
  sleep 0.5
fi

# 2. BlueZ daemon (enregistre org.bluez sur D-Bus)
if ! pgrep -x bluetoothd > /dev/null 2>&1; then
  echo "[Domovra] Démarrage bluetoothd..."
  # --noplugin=sap évite l'erreur SIM Access Profile sans carte SIM
  bluetoothd --noplugin=sap &
  sleep 2
  if pgrep -x bluetoothd > /dev/null 2>&1; then
    echo "[Domovra] bluetoothd OK"
  else
    echo "[Domovra] WARN: bluetoothd indisponible — impression BLE désactivée"
  fi
fi

# ─────────────── Préparation app ───────────────
mkdir -p /data
export DB_PATH="/data/domovra.sqlite3"
echo "[Domovra] DB_PATH=${DB_PATH}"

if [ -d "/opt/app" ]; then
  APP_DIR="/opt/app"
elif [ -d "/app" ]; then
  APP_DIR="/app"
else
  echo "[Domovra] ERREUR: répertoire applicatif introuvable"
  exit 1
fi
cd "$APP_DIR"

if [ -z "${DOMOVRA_VERSION:-}" ] && [ -f "$APP_DIR/config.json" ]; then
  DOMOVRA_VERSION="$(python3 -c "import json; print(json.load(open('$APP_DIR/config.json')).get('version',''))" 2>/dev/null || true)"
  export DOMOVRA_VERSION
  echo "[Domovra] Version détectée: ${DOMOVRA_VERSION:-n/a}"
else
  echo "[Domovra] Version (ENV): ${DOMOVRA_VERSION:-n/a}"
fi

if [ -f "$APP_DIR/app/main.py" ]; then
  MODULE="app.main:app"
elif [ -f "$APP_DIR/main.py" ]; then
  MODULE="main:app"
else
  echo "[Domovra] ERREUR: main.py introuvable"
  exit 1
fi
echo "[Domovra] Module = ${MODULE} | APP_DIR = ${APP_DIR}"

if [ -x "/opt/venv/bin/uvicorn" ]; then
  UVICORN="/opt/venv/bin/uvicorn"
else
  UVICORN="$(command -v uvicorn || true)"
fi

# ─────────────── Lancement ───────────────
if [ -n "${UVICORN:-}" ]; then
  exec "${UVICORN}" "${MODULE}" \
    --host 0.0.0.0 \
    --port 8098 \
    --app-dir "${APP_DIR}" \
    --proxy-headers
else
  exec python3 -m uvicorn "${MODULE}" \
    --host 0.0.0.0 \
    --port 8098 \
    --app-dir "${APP_DIR}" \
    --proxy-headers
fi
