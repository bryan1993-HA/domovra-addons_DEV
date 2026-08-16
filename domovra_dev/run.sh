# domovra/run.sh
#!/usr/bin/with-contenv bash
set -euo pipefail

# ─────────────── Diagnostic Bluetooth ───────────────
echo "[Domovra] === Diagnostic Bluetooth ==="

# Cherche le socket D-Bus sur tous les chemins possibles
DBUS_FOUND=""
for path in \
  /run/dbus/system_bus_socket \
  /var/run/dbus/system_bus_socket \
  /host/run/dbus/system_bus_socket \
  /run/host/run/dbus/system_bus_socket; do
  if [ -S "$path" ]; then
    echo "[Domovra] D-Bus socket trouvé: $path"
    export DBUS_SYSTEM_BUS_ADDRESS="unix:path=${path}"
    DBUS_FOUND="$path"
    break
  fi
done
[ -z "$DBUS_FOUND" ] && echo "[Domovra] Aucun socket D-Bus trouvé"

# Contenu de /run et /var/run pour diagnostic
echo "[Domovra] /run contient: $(ls /run 2>/dev/null | tr '\n' ' ')"
echo "[Domovra] /var/run contient: $(ls /var/run 2>/dev/null | tr '\n' ' ')"

# Interfaces réseau BT visibles
echo "[Domovra] Interfaces BT: $(ls /sys/class/bluetooth 2>/dev/null | tr '\n' ' ' || echo 'aucune')"

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
