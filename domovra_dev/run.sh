# domovra/run.sh
#!/usr/bin/with-contenv bash
set -euo pipefail

# ─────────────── Diagnostic Bluetooth ───────────────
echo "[Domovra] === Diagnostic Bluetooth ==="

# Cherche le socket D-Bus
DBUS_FOUND=""
for path in /run/dbus/system_bus_socket /var/run/dbus/system_bus_socket; do
  if [ -S "$path" ]; then
    echo "[Domovra] D-Bus socket trouvé: $path"
    export DBUS_SYSTEM_BUS_ADDRESS="unix:path=${path}"
    DBUS_FOUND="$path"
    break
  fi
done
[ -z "$DBUS_FOUND" ] && echo "[Domovra] Aucun socket D-Bus (RFCOMM sera utilisé)"

# Interface HCI
echo "[Domovra] Interfaces BT: $(ls /sys/class/bluetooth 2>/dev/null | tr '\n' ' ' || echo 'aucune')"

# Tente de démarrer dbus+bluetoothd si hci0 visible mais pas de D-Bus
if [ -z "$DBUS_FOUND" ] && [ -d "/sys/class/bluetooth/hci0" ]; then
  echo "[Domovra] hci0 détecté — démarrage dbus + bluetoothd pour BLE"
  mkdir -p /run/dbus
  dbus-daemon --system --fork 2>/dev/null \
    && echo "[Domovra] dbus-daemon OK" \
    || echo "[Domovra] WARN: dbus-daemon indisponible"
  sleep 0.5

  # bluetoothd sur Alpine : /usr/lib/bluetooth/bluetoothd
  BTDAEMON=""
  for p in /usr/lib/bluetooth/bluetoothd /usr/libexec/bluetooth/bluetoothd; do
    [ -x "$p" ] && BTDAEMON="$p" && break
  done
  command -v bluetoothd > /dev/null 2>&1 && BTDAEMON=bluetoothd

  if [ -n "$BTDAEMON" ]; then
    $BTDAEMON --noplugin=sap 2>&1 &
    sleep 2
    if [ -S "/run/dbus/system_bus_socket" ] && pgrep -x bluetoothd > /dev/null 2>&1; then
      echo "[Domovra] bluetoothd OK — BLE disponible"
      export DBUS_SYSTEM_BUS_ADDRESS="unix:path=/run/dbus/system_bus_socket"
    else
      echo "[Domovra] WARN: bluetoothd n'a pas démarré — RFCOMM uniquement"
    fi
  else
    echo "[Domovra] WARN: bluetoothd introuvable — RFCOMM uniquement"
  fi
fi

echo "[Domovra] === Fin diagnostic ==="

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
