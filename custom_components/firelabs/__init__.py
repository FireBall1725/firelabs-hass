"""The FireLabs integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, WEBHOOK_MODELS, WX_STORAGE_VERSION
from .coordinator import FirelabsCoordinator
from .services import async_register_services
from .webhook import async_register_webhook, async_unregister_webhook

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_register_services(hass)
    return True


# Identity fields seeded into the coordinator for a sleepy device, so setup does
# not depend on the device being awake and reachable.
_SEED_KEYS = ("host", "mac", "model", "name", "fw")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FireLabs from a config entry."""
    if entry.data.get("model") in WEBHOOK_MODELS:
        return await _async_setup_webhook_device(hass, entry)

    coordinator = FirelabsCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_setup_webhook_device(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Weather or Plant Display: no polling, webhook-driven, options-mapped."""
    coordinator = FirelabsCoordinator(hass, entry, poll=False)
    seed = {k: entry.data[k] for k in _SEED_KEYS if entry.data.get(k) is not None}
    seed = await coordinator.async_restore_snapshot(seed)
    coordinator.async_set_updated_data(seed)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await async_register_webhook(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Re-pointing entity mappings (options) takes effect on reload.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.data.get("model") in WEBHOOK_MODELS:
        async_unregister_webhook(hass, entry)
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the persisted WX snapshot when the device is removed.

    The coordinator is already torn down by this point, so address the store by key.
    """
    if entry.data.get("model") in WEBHOOK_MODELS:
        await Store(
            hass, WX_STORAGE_VERSION, f"{DOMAIN}.wx.{entry.entry_id}"
        ).async_remove()
