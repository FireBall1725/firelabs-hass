"""Per-entry check-in webhook for the Weather Display and the Plant Display.

The device POSTs its telemetry and gets its data bundle back in the same
response. A model module can supply `async_build_bundle`; the WX uses the
weather builder below. The entity-to-field mapping lives in the config entry options, so
re-pointing the display at different sensors is a settings change with no reflash.
"""
from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import models
from .const import (
    CONF_LOCATION,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SLEEP_MIN,
    CONF_WEATHER_ENTITY,
    CONF_WEBHOOK_ID,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SLEEP_MIN,
    DOMAIN,
    WX_CURRENT_FIELDS,
    WX_FORECAST_SLOTS,
)
from .coordinator import FirelabsCoordinator

_LOGGER = logging.getLogger(__name__)

_UNUSABLE = (None, "", "unknown", "unavailable")


async def async_register_webhook(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: FirelabsCoordinator
) -> None:
    """Register the check-in webhook for a webhook-driven device entry."""
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    async def handler(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            body = {}

        coordinator.async_ingest_checkin(body)
        module = models.for_model(entry.data.get("model"))
        handle = getattr(module, "async_handle_action", None)
        if handle and body.get("action"):
            await handle(hass, entry, body)
        build = getattr(module, "async_build_bundle", None) or _build_bundle
        bundle = await build(hass, entry, coordinator)

        # Force-wake is one-shot: deliver it once, then clear it so the device
        # doesn't hold awake every cycle.
        if coordinator.force_wake:
            coordinator.force_wake = False
            coordinator.async_update_listeners()

        return web.json_response(bundle)

    webhook.async_register(
        hass, DOMAIN, f"FireLabs {entry.title}", webhook_id, handler, local_only=True
    )
    _LOGGER.debug("Registered check-in webhook %s", webhook_id)


def async_unregister_webhook(hass: HomeAssistant, entry: ConfigEntry) -> None:
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)


async def _build_bundle(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: FirelabsCoordinator
) -> dict:
    opts = entry.options

    current: dict = {}
    for field, opt_key in WX_CURRENT_FIELDS.items():
        entity_id = opts.get(opt_key)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None or state.state in _UNUSABLE:
            continue
        current[field] = state.state if field == "condition" else _num(state.state)

    forecast = await _build_forecast(hass, opts.get(CONF_WEATHER_ENTITY))

    return {
        "location": opts.get(CONF_LOCATION) or hass.config.location_name or "Weather",
        "updated": dt_util.now().isoformat(timespec="seconds"),
        "units": {
            "wind": _unit(hass, opts, WX_CURRENT_FIELDS["wind"], "km/h"),
            "precip": _unit(hass, opts, WX_CURRENT_FIELDS["precip"], "mm"),
        },
        "current": current,
        "forecast": forecast,
        "settings": {
            "sleep_min": int(opts.get(CONF_SLEEP_MIN, DEFAULT_SLEEP_MIN)),
            "quiet_start": int(opts.get(CONF_QUIET_START, DEFAULT_QUIET_START)),
            "quiet_end": int(opts.get(CONF_QUIET_END, DEFAULT_QUIET_END)),
            "force_wake": coordinator.force_wake,
        },
        "ota": {"version": "0.0.0", "url": ""},  # mass-OTA fields, filled later
    }


def _unit(hass: HomeAssistant, opts: dict, opt_key: str, default: str) -> str:
    """The source sensor's unit_of_measurement, so the panel matches HA."""
    entity_id = opts.get(opt_key)
    if entity_id:
        state = hass.states.get(entity_id)
        if state:
            unit = state.attributes.get("unit_of_measurement")
            if unit:
                return unit
    return default


async def _build_forecast(hass: HomeAssistant, weather_entity: str | None) -> list:
    if not weather_entity:
        return []
    try:
        resp = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_entity, "type": "hourly"},
            blocking=True,
            return_response=True,
        )
    except Exception as err:  # service may be missing or the entity unavailable
        _LOGGER.debug("Forecast lookup for %s failed: %s", weather_entity, err)
        return []

    items = (resp or {}).get(weather_entity, {}).get("forecast", [])
    out = []
    for item in items[:WX_FORECAST_SLOTS]:
        temp = item.get("temperature")
        out.append(
            {
                "time": _fmt_hour(item.get("datetime")),
                "temp": round(temp) if isinstance(temp, (int, float)) else None,
                "cond": item.get("condition"),
            }
        )
    return out


def _num(value: str) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _fmt_hour(iso: str | None) -> str:
    """Local hour like '3PM' from an ISO datetime; empty string if unparseable."""
    if not iso:
        return ""
    dt = dt_util.parse_datetime(iso)
    if dt is None:
        return ""
    local = dt_util.as_local(dt)
    hour12 = local.hour % 12 or 12
    return f"{hour12}{'AM' if local.hour < 12 else 'PM'}"
