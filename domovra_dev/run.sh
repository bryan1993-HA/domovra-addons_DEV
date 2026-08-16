# domovra/run.sh
#!/usr/bin/with-contenv bash
set -euo pipefail

# ─────────────── Stack Bluetooth ───────────────
# bluetooth:true  → HAOS expose les capabilities BT au container
# host_network:true → le container partage le namespace réseau du host
#                     → interfaces HCI visibles (hci0, etc.)
# On lance notre propre dbus + bluetoothd pour que bleak ait org.bluez.

mkdir -p /run/dbus

# 1. D-Bus system daemon
if ! pgrep -x dbus-daemon > /dev/null 2>&1; then
  dbus-daemon --system --fork 2>/dev/null \
    && echo "[Domovra] dbus-daemon OK" \
    || echo "[Domovra] WARN: dbus-daemon indisponible"
  sleep 0.5
fi

# 2. Diagnostique les interfaces HCI visibles
echo "[Domovra] Interfaces Bluetooth :"
hciconfig -a 2>/dev/null || echo "[Domovra] hciconfig: aucune interface trouvée"

# 3. S'assure que hci0 est up
hciconfig hci0 up 2>/dev/null && echo "[Domovra] hci0 up OK" \
  || echo "[Domovra] WARN: hci0 indisponible"

# 4. BlueZ daemon
if ! pgrep -x bluetoothd > /dev/null 2>&1; then
  bluetoothd --noplugin=sap 2>&1 &
  sleep 2
  pgrep -x bluetoothd > /dev/null 2>&1 \
    && echo "[Domovra] bluetoothd OK" \
    || echo "[Domovra] WARN: bluetoothd n'a pas démarré"
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
  echo "[Domovra] Version: ${DOMOVRA_VERSION:-n/a}"
fi

if [ -f "$APP_DIR/app/main.py" ]; then
  MODULE="app.main:app"
elif [ -f "$APP_DIR/main.py" ]; then
  MODULE="main:app"
else
  echo "[Domovra] ERREUR: main.py introuvable"
  exit 1
fi

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
