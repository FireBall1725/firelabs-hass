"""Entities for the FireLabs Weather Display (model "WX").

A sleepy, battery e-paper device. HA does not poll it; it wakes, checks in over
the webhook, and reads back a weather bundle. So this module exposes telemetry
the device reports on each check-in (battery, voltage, wake reason, last seen)
and a force-wake switch. The check-in URL to configure the device with is shown
in the config flow; the weather data mapping lives in the config entry options.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.util import dt as dt_util

from ..const import WX_AVAILABLE_WINDOW
from ..coordinator import FirelabsCoordinator
from ..entity import FirelabsEntity

# ---------- telemetry sensors ----------

@dataclass(frozen=True, kw_only=True)
class WxSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]


SENSORS: tuple[WxSensorDescription, ...] = (
    WxSensorDescription(
        key="battery", name="Battery",
        device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.get("battery"),
    ),
    WxSensorDescription(
        key="voltage", name="Battery voltage",
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("voltage"),
    ),
    WxSensorDescription(
        key="wake", name="Wake reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("wake"),
    ),
    WxSensorDescription(
        key="last_seen", name="Last seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("last_seen"),
    ),
)


class WxSensor(FirelabsEntity, SensorEntity):
    """Reports a value from the last check-in; unavailable if check-ins stop."""

    entity_description: WxSensorDescription

    def __init__(
        self, coordinator: FirelabsCoordinator, description: WxSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        last = self.coordinator.data.get("last_seen")
        return bool(last and dt_util.utcnow() - last < WX_AVAILABLE_WINDOW)

    @property
    def native_value(self) -> float | int | str | datetime | None:
        return self.entity_description.value_fn(self.coordinator.data)


# ---------- force-wake switch ----------

class ForceWakeSwitch(FirelabsEntity, SwitchEntity):
    """Hold the device awake on its next check-in (for OTA or reconfiguration)."""

    _attr_name = "Force wake"
    _attr_icon = "mdi:alarm"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FirelabsCoordinator) -> None:
        super().__init__(coordinator, "force_wake")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.force_wake)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.force_wake = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.force_wake = False
        self.async_write_ha_state()


# ---------- per-platform builders ----------

def sensors(coordinator: FirelabsCoordinator) -> list[SensorEntity]:
    return [WxSensor(coordinator, d) for d in SENSORS]


def switches(coordinator: FirelabsCoordinator) -> list[SwitchEntity]:
    return [ForceWakeSwitch(coordinator)]
