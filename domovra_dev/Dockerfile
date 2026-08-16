ARG BUILD_FROM
FROM $BUILD_FROM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/opt/app \
    PYTHONPATH=/opt/app \
    VENV_PATH=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Dépendances système : bluez + dbus (pour bluetoothd + bleak) + Pillow
RUN apk add --no-cache \
    python3 py3-pip \
    bluez bluez-libs \
    dbus \
    zlib jpeg libpng freetype \
    ttf-dejavu

WORKDIR ${APP_HOME}
COPY app ${APP_HOME}/
COPY config.json ${APP_HOME}/config.json

RUN python3 -m venv ${VENV_PATH} \
 && ${VENV_PATH}/bin/pip install --no-cache-dir \
    "fastapi>=0.110.0,<1.0.0" \
    "uvicorn[standard]>=0.27.0,<1.0.0" \
    "jinja2>=3.1.0,<4.0.0" \
    "python-multipart>=0.0.9,<1.0.0" \
    "bleak>=0.21.0,<1.0.0" \
    "Pillow>=10.0.0,<12.0.0"

COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8098
CMD ["/run.sh"]
