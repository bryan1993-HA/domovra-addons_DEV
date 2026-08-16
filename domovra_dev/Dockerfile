ARG BUILD_FROM
FROM $BUILD_FROM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/opt/app \
    PYTHONPATH=/opt/app \
    VENV_PATH=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Dépendances système : Pillow (étiquettes) + bluez pour socket RFCOMM noyau
RUN apk add --no-cache \
    python3 py3-pip \
    bluez bluez-libs \
    zlib jpeg libpng freetype \
    ttf-dejavu

WORKDIR ${APP_HOME}
# Code de l'app
COPY app ${APP_HOME}/
# ⬅️ Ajoute le manifest de l'add-on dans l'image
COPY config.json ${APP_HOME}/config.json

# Dépendances Python (versions pinées pour builds reproductibles)
RUN python3 -m venv ${VENV_PATH} \
 && ${VENV_PATH}/bin/pip install --no-cache-dir \
    "fastapi>=0.110.0,<1.0.0" \
    "uvicorn[standard]>=0.27.0,<1.0.0" \
    "jinja2>=3.1.0,<4.0.0" \
    "python-multipart>=0.0.9,<1.0.0" \
    "Pillow>=10.0.0,<12.0.0"

COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8098
CMD ["/run.sh"]
