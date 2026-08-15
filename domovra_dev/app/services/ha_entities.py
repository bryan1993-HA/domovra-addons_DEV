# domovra/app/services/ha_entities.py
"""
Push Domovra sensors to Home Assistant Supervisor API.
Uses stdlib urllib — no extra deps needed.
Silently no-ops when SUPERVISOR_TOKEN is absent (dev / Docker standalone).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger("domovra.ha_entities")

_HA_BASE = "http://supervisor/core/api"

SENSORS = [
    {
        "entity_id": "sensor.domovra_total_products",
        "friendly_name": "Domovra — Produits",
        "icon": "mdi:package-variant",
        "key": "products",
    },
    {
        "entity_id": "sensor.domovra_total_lots",
        "friendly_name": "Domovra — Lots en stock",
        "icon": "mdi:archive-outline",
        "key": "lots",
    },
    {
        "entity_id": "sensor.domovra_low_stock",
        "friendly_name": "Domovra — Stock faible",
        "icon": "mdi:alert-circle-outline",
        "key": "low_stock",
    },
    {
        "entity_id": "sensor.domovra_expiring_soon",
        "friendly_name": "Domovra — Expirant bientôt",
        "icon": "mdi:clock-alert-outline",
        "key": "soon",
    },
    {
        "entity_id": "sensor.domovra_expired",
        "friendly_name": "Domovra — Expirés / urgents",
        "icon": "mdi:alert",
        "key": "urgent",
    },
]


def _get_token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN") or None


def _push_state(token: str, entity_id: str, state: Any, attributes: dict) -> bool:
    url = f"{_HA_BASE}/states/{entity_id}"
    payload = json.dumps({"state": str(state), "attributes": attributes}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except urllib.error.URLError as e:
        logger.warning("HA push failed for %s: %s", entity_id, e)
        return False
    except Exception as e:
        logger.error("HA push unexpected error for %s: %s", entity_id, e)
        return False


def push_sensors(summary: dict) -> None:
    """Push all 5 sensors to HA. Silently no-ops if SUPERVISOR_TOKEN is not set."""
    token = _get_token()
    if not token:
        return

    pushed = 0
    for sensor in SENSORS:
        value = summary.get(sensor["key"], 0)
        attributes = {
            "friendly_name": sensor["friendly_name"],
            "icon": sensor["icon"],
        }
        if _push_state(token, sensor["entity_id"], value, attributes):
            pushed += 1

    if pushed:
        logger.info("HA sensors pushed: %d/%d", pushed, len(SENSORS))


def refresh_and_push() -> None:
    """Fetch current summary from DB and push all sensors to HA."""
    try:
        from routes.ha import ha_summary
        summary = ha_summary()
        push_sensors(summary)
    except Exception as e:
        logger.error("HA refresh_and_push error: %s", e)


def schedule_ha_push() -> None:
    """Fire-and-forget: push HA sensors in a background daemon thread."""
    t = threading.Thread(target=refresh_and_push, daemon=True)
    t.start()
