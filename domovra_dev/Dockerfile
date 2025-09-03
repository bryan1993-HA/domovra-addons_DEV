ARG BUILD_FROM
FROM $BUILD_FROM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/opt/app \
    PYTHONPATH=/opt/app \
    VENV_PATH=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apk add --no-cache python3 py3-pip

WORKDIR ${APP_HOME}
# Code de l'app
COPY app ${APP_HOME}/
# ⬅️ Ajoute le manifest de l'add-on dans l'image
COPY config.json ${APP_HOME}/config.json

# Dépendances (inclut python-multipart)
RUN python3 -m venv ${VENV_PATH} \
 && ${VENV_PATH}/bin/pip install --no-cache-dir \
    fastapi "uvicorn[standard]" jinja2 python-multipart

COPY run.sh /run.sh
RUN chmod +x /run.sh

# (optionnel) garder 8099 ou aligne avec run.sh (8098). Ça n'impacte pas Ingress.
EXPOSE 8098
CMD ["/run.sh"]
