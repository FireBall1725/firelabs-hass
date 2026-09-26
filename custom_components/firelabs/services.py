"""firelabs.install_firmware: put a specific build on a device.

For bench builds and rollbacks; the update entity covers releases. The image comes
from a URL or from a file under the HA config directory.
"""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .coordinator import FirelabsCoordinator

SERVICE_INSTALL_FIRMWARE = "install_firmware"
ATTR_DEVICE_ID = "device_id"
ATTR_URL = "url"
ATTR_PATH = "path"

SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_URL): cv.url,
            vol.Optional(ATTR_PATH): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_URL, ATTR_PATH),
)


def _coordinator(hass: HomeAssistant, device_id: str) -> FirelabsCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    for entry_id in device.config_entries if device else ():
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if isinstance(coordinator, FirelabsCoordinator):
            return coordinator
    raise ServiceValidationError("Not a FireLabs device")


async def _async_install_firmware(call: ServiceCall) -> None:
    hass = call.hass
    coordinator = _coordinator(hass, call.data[ATTR_DEVICE_ID])
    if url := call.data.get(ATTR_URL):
        await coordinator.async_ota_from_url(url)
        return
    path = Path(call.data[ATTR_PATH]).resolve()
    config_dir = Path(hass.config.config_dir).resolve()
    if not (path.is_relative_to(config_dir) or hass.config.is_allowed_path(str(path))):
        raise ServiceValidationError(f"{path} is outside the Home Assistant config folder")
    try:
        blob = await hass.async_add_executor_job(path.read_bytes)
    except OSError as err:
        raise HomeAssistantError(f"Can't read {path}: {err}") from err
    await coordinator.async_install_image(blob)


def async_register_services(hass: HomeAssistant) -> None:
    # Admin only: it flashes devices and reads files from the config folder.
    if not hass.services.has_service(DOMAIN, SERVICE_INSTALL_FIRMWARE):
        async_register_admin_service(
            hass, DOMAIN, SERVICE_INSTALL_FIRMWARE, _async_install_firmware, schema=SCHEMA
        )
