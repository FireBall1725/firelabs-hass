"""Entities and data bundle for the FireLabs Plant Display (model "PD").

A mains-powered 800x480 LVGL panel. Like the Weather Display it never exposes
data for HA to poll: it POSTs a check-in to its webhook every poll interval and
reads back a bundle with up to three plants (current readings and how old each
one is, the moisture floor and ceiling, last watered, and a week of hourly mean
moisture for the curve). The plant-to-entity mapping lives in the entry options.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_PD_LIGHT,
    CONF_PD_LIGHT_POWER,
    CONF_PD_PLANTS,
    CONF_PD_POLL,
    CONF_PD_SCREEN_FOLLOWS_LIGHT,
    DEFAULT_PD_POLL,
    PD_AVAILABLE_WINDOW,
    PD_HISTORY_HOURS,
    PD_HISTORY_TTL,
    PD_PLANT_SLOTS,
    PD_READINGS,
    pd_key,
)
from ..coordinator import FirelabsCoordinator
from ..entity import FirelabsEntity

_UNUSABLE = (None, "", "unknown", "unavailable")


# ---------- telemetry sensors ----------

@dataclass(frozen=True, kw_only=True)
class PdSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]


SENSORS: tuple[PdSensorDescription, ...] = (
    PdSensorDescription(
        key="last_seen", name="Last seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("last_seen"),
    ),
    PdSensorDescription(
        key="rssi", name="WiFi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("rssi"),
    ),
)


class PdSensor(FirelabsEntity, SensorEntity):
    """Reports a value from the last check-in; unavailable if check-ins stop."""

    entity_description: PdSensorDescription

    def __init__(
        self, coordinator: FirelabsCoordinator, description: PdSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        last = self.coordinator.data.get("last_seen")
        return bool(last and dt_util.utcnow() - last < PD_AVAILABLE_WINDOW)

    @property
    def native_value(self) -> float | int | str | datetime | None:
        return self.entity_description.value_fn(self.coordinator.data)


def sensors(coordinator: FirelabsCoordinator) -> list[SensorEntity]:
    return [PdSensor(coordinator, d) for d in SENSORS]


# ---------- display controls ----------
#
# The panel's backlight is a CH422G pin, on or off only, so brightness and the auto-dim
# level are software dimming on the display (a black overlay), not backlight PWM.

DISPLAY_DEFAULTS: dict[str, Any] = {
    "backlight": True,
    "brightness": 100,
    "auto_dim": False,
    "dim_after": 60,
    "dim_level": 30,
}
_DISPLAY_PUSH = "/api/display"


def display_settings(coordinator: FirelabsCoordinator) -> dict[str, Any]:
    data = coordinator.data or {}
    return {k: data.get(k, v) for k, v in DISPLAY_DEFAULTS.items()}


class _PdControl(FirelabsEntity):
    """Available while the display is checking in."""

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        last = self.coordinator.data.get("last_seen")
        return bool(last and dt_util.utcnow() - last < PD_AVAILABLE_WINDOW)


class PdSwitch(_PdControl, SwitchEntity):
    def __init__(
        self, coordinator: FirelabsCoordinator, key: str, name: str, icon: str,
        category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._attr_entity_category = category

    @property
    def is_on(self) -> bool:
        return bool(display_settings(self.coordinator)[self._key])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_settings({self._key: True}, _DISPLAY_PUSH)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_settings({self._key: False}, _DISPLAY_PUSH)


class PdNumber(_PdControl, NumberEntity):
    def __init__(
        self, coordinator: FirelabsCoordinator, key: str, name: str, icon: str,
        lo: float, hi: float, step: float, unit: str, mode: NumberMode,
        category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = mode
        self._attr_entity_category = category

    @property
    def native_value(self) -> float:
        return display_settings(self.coordinator)[self._key]

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_update_settings({self._key: int(value)}, _DISPLAY_PUSH)


def switches(coordinator: FirelabsCoordinator) -> list[SwitchEntity]:
    return [
        PdSwitch(coordinator, "backlight", "Backlight", "mdi:monitor"),
        PdSwitch(coordinator, "auto_dim", "Auto dim", "mdi:monitor-arrow-down",
                 EntityCategory.CONFIG),
    ]


def numbers(coordinator: FirelabsCoordinator) -> list[NumberEntity]:
    return [
        PdNumber(coordinator, "brightness", "Brightness", "mdi:brightness-6",
                 10, 100, 5, PERCENTAGE, NumberMode.SLIDER),
        PdNumber(coordinator, "dim_after", "Dim after", "mdi:timer-outline",
                 5, 3600, 5, UnitOfTime.SECONDS, NumberMode.BOX, EntityCategory.CONFIG),
        PdNumber(coordinator, "dim_level", "Dim level", "mdi:brightness-4",
                 5, 90, 5, PERCENTAGE, NumberMode.SLIDER, EntityCategory.CONFIG),
    ]


# ---------- actions ----------

async def async_handle_action(
    hass: HomeAssistant, entry: ConfigEntry, body: dict
) -> None:
    """Run a command the display sent with its check-in.

    `toggle_light`: tapping the grow light pill. The device holds no HA token, so
    commands ride in on the webhook like the telemetry does.
    """
    if body.get("action") != "toggle_light":
        return
    entity_id = entry.options.get(CONF_PD_LIGHT)
    before = _state(hass, entity_id)
    if not entity_id or before is None or before.state in _UNUSABLE:
        return
    await hass.services.async_call(
        "homeassistant", "toggle", {"entity_id": entity_id}, blocking=True
    )
    _light_toggled_from[entry.entry_id] = (before.state, time.monotonic())


# A switch polled by its integration may not show the new state by the time the
# bundle is built; report the toggled value until it does, so the pill doesn't
# flip back for a cycle.
_light_toggled_from: dict[str, tuple[str, float]] = {}
_TOGGLE_GRACE = 10  # seconds


# ---------- bundle ----------

# Hourly statistics per entry: {entry_id: (fetched_monotonic, start_utc, {entity: [..]})}
_history_cache: dict[str, tuple[float, datetime, dict[str, list]]] = {}


async def async_build_bundle(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: FirelabsCoordinator
) -> dict:
    """The plant bundle the firmware parses (see its Bundle.h for the contract)."""
    opts = entry.options
    now = dt_util.utcnow()

    chosen = opts.get(CONF_PD_PLANTS) or []
    if chosen:
        plants = await _async_plants_from_objects(hass, chosen)
    else:
        plants = await _async_plants_from_pickers(hass, entry, now)

    bundle: dict[str, Any] = {
        "updated": _local_iso(now),
        "plants": plants,
        "settings": {
            "poll_sec": int(opts.get(CONF_PD_POLL, DEFAULT_PD_POLL)),
            "screen_follows_light": bool(opts.get(CONF_PD_SCREEN_FOLLOWS_LIGHT, False)),
            "display": display_settings(coordinator),
        },
        "ota": {"version": "0.0.0", "url": ""},  # mass-OTA fields, filled later
    }

    light = _state(hass, opts.get(CONF_PD_LIGHT))
    if light is not None and light.state not in _UNUSABLE:
        on = light.state == "on"
        toggled = _light_toggled_from.get(entry.entry_id)
        if toggled and time.monotonic() - toggled[1] < _TOGGLE_GRACE and light.state == toggled[0]:
            on = not on
        bundle["light"] = {"on": on}
        power = _num(_state(hass, opts.get(CONF_PD_LIGHT_POWER)))
        if power is not None:
            bundle["light"]["power"] = round(power, 1)
    return bundle


def plant_objects(hass: HomeAssistant) -> dict[str, Any]:
    """Plants from the plants-hass integration, by config entry id (empty if not installed)."""
    return hass.data.get("plants", {}).get("plants", {})


async def _async_plants_from_objects(hass: HomeAssistant, entry_ids: list[str]) -> list[dict]:
    """Bundle plants from plants-hass objects: they already own band, status and history."""
    registry = plant_objects(hass)
    out: list[dict] = []
    for entry_id in entry_ids:
        plant = registry.get(entry_id)
        if plant is None:
            continue
        snap = plant.snapshot()
        start, history = await plant.async_history()
        item: dict[str, Any] = {
            "name": snap["name"],
            "species": snap["species"],
            "sensor": snap["sensor"],
            "icon": snap.get("icon") or "",
            # Custom marks: 34x34 A8 bytes, base64; the built-ins are in the firmware.
            "icon_mask": snap.get("icon_display") or "",
            "floor": snap["floor"],
            "ceiling": snap["ceiling"],
            "history_start": _local_iso(start),
            "history": history,
        }
        for field, reading in PD_READINGS.items():
            r = snap["readings"].get(reading) or {}
            if r.get("value") is not None:
                item[field] = r["value"]
                item[f"{field}_age"] = r.get("age") or 0
        watered = dt_util.parse_datetime(snap["last_watered"] or "")
        if watered:
            item["watered"] = _local_iso(watered)
        out.append(item)
    return out


async def _async_plants_from_pickers(
    hass: HomeAssistant, entry: ConfigEntry, now: datetime
) -> list[dict]:
    """The original setup: one entity picker per reading, per plant slot."""
    opts = entry.options
    slots = [n for n in range(1, PD_PLANT_SLOTS + 1) if opts.get(pd_key(n, "moisture"))]
    start, history = await _async_history(
        hass, entry, [opts[pd_key(n, "moisture")] for n in slots]
    )

    plants = []
    for n in slots:
        moisture_id = opts[pd_key(n, "moisture")]
        plant: dict[str, Any] = {
            "name": opts.get(pd_key(n, "name")) or _entity_name(hass, moisture_id) or f"Plant {n}",
            "species": opts.get(pd_key(n, "species")) or "",
            "sensor": _sensor_label(hass, moisture_id),
            "history_start": _local_iso(start),
            "history": history.get(moisture_id, []),
        }
        for field, opt in PD_READINGS.items():
            state = _state(hass, opts.get(pd_key(n, opt)))
            value = _num(state)
            if value is None:
                continue
            plant[field] = value
            plant[f"{field}_age"] = _age(state, now)
        for field in ("floor", "ceiling"):
            value = _num(_state(hass, opts.get(pd_key(n, field))))
            if value is not None:
                plant[field] = value
        watered = _when(_state(hass, opts.get(pd_key(n, "watered"))))
        if watered:
            plant["watered"] = watered
        plants.append(plant)
    return plants


async def _async_history(
    hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str]
) -> tuple[datetime, dict[str, list]]:
    """A week of hourly mean moisture per sensor, None where the recorder has none.

    Long-term statistics, not raw history: one row per hour already averaged, and
    they outlive the recorder's purge window. Cached because they only move on the
    hour and the display checks in every minute.
    """
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=PD_HISTORY_HOURS
    )
    cached = _history_cache.get(entry.entry_id)
    if (
        cached
        and cached[1] == start
        and time.monotonic() - cached[0] < PD_HISTORY_TTL
        and set(entity_ids) <= set(cached[2])
    ):
        return start, cached[2]

    result: dict[str, list] = {}
    if entity_ids and "recorder" in hass.config.components:
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass, start, None, set(entity_ids), "hour", None, {"mean"},
        )
        start_ts = start.timestamp()
        for entity_id in entity_ids:
            series: list[float | None] = [None] * PD_HISTORY_HOURS
            for row in stats.get(entity_id, []):
                row_start = row.get("start")
                if isinstance(row_start, datetime):
                    row_start = row_start.timestamp()
                if row_start is None or row.get("mean") is None:
                    continue
                i = int((row_start - start_ts) // 3600)
                if 0 <= i < PD_HISTORY_HOURS:
                    series[i] = round(row["mean"], 1)
            # Drop trailing empties (the current, unfinished hour) so the curve
            # ends on real data.
            while series and series[-1] is None:
                series.pop()
            result[entity_id] = series
    _history_cache[entry.entry_id] = (time.monotonic(), start, result)
    return start, result


def _state(hass: HomeAssistant, entity_id: str | None) -> State | None:
    return hass.states.get(entity_id) if entity_id else None


def _num(state: State | None) -> float | None:
    if state is None or state.state in _UNUSABLE:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _age(state: State, now: datetime) -> int:
    """Seconds since the sensor last reported, even if the value didn't change."""
    reported = getattr(state, "last_reported", None) or state.last_updated
    return max(0, int((now - reported).total_seconds()))


def _when(state: State | None) -> str | None:
    """Local ISO time from a timestamp sensor or an input_datetime."""
    if state is None or state.state in _UNUSABLE:
        return None
    parsed = dt_util.parse_datetime(state.state)
    if parsed is None:
        return None
    if parsed.tzinfo is None:  # input_datetime states are naive local time
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return _local_iso(parsed)


def _local_iso(value: datetime) -> str:
    """Local wall-clock without an offset; the firmware reads it as-is."""
    return dt_util.as_local(value).strftime("%Y-%m-%dT%H:%M:%S")


def _entity_name(hass: HomeAssistant, entity_id: str) -> str | None:
    """The plant's name from its sensor device, e.g. a device renamed to "Jade"."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device:
            return device.name_by_user or device.name
    return None


def _sensor_label(hass: HomeAssistant, entity_id: str) -> str:
    """The sensor's own device name (e.g. "Plant Sensor 6C31"), not the user's rename."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device and device.name:
            return device.name
    return ""
