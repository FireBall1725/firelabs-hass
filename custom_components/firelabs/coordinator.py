"""Polling coordinator for a FireLabs device."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import ota_serve
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HTTP_TIMEOUT,
    OTA_DOWNLOAD_TIMEOUT,
    OTA_UPLOAD_TIMEOUT,
    PULL_OTA_MODELS,
    WX_SAVE_DELAY,
    WX_SNAPSHOT_KEYS,
    WX_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class FirelabsCoordinator(DataUpdateCoordinator[dict]):
    """Holds a FireLabs device's state.

    Wired devices (the S31) are polled over HTTP. Sleepy devices (the WX) pass
    ``poll=False``: there's no polling interval, the data is seeded from the
    config entry, and the webhook pushes each check-in in via
    ``async_ingest_checkin``.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, *, poll: bool = True
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data['host']}",
            update_interval=(
                timedelta(seconds=DEFAULT_SCAN_INTERVAL) if poll else None
            ),
        )
        self.host: str = entry.data["host"]
        self.model: str | None = entry.data.get("model")
        self._session = async_get_clientsession(hass)
        # WX only: the force-wake switch lives in HA and rides back in the bundle.
        self.force_wake: bool = False
        # WX only: persist the last check-in so a restart restores telemetry instead
        # of going unavailable until the sleepy device next wakes. Wired devices poll
        # on startup, so they need no store.
        self._store: Store | None = (
            None
            if poll
            else Store(hass, WX_STORAGE_VERSION, f"{DOMAIN}.wx.{entry.entry_id}")
        )

    @property
    def base_url(self) -> str:
        return f"http://{self.host}"

    async def async_restore_snapshot(self, seed: dict) -> dict:
        """Merge the last persisted check-in into a sleepy device's startup seed.

        Identity comes from the config entry; the telemetry (battery, voltage, fw,
        wake reason, last-seen) is whatever the device last reported, so a restart
        doesn't blank the sensors or flag a false firmware update before the next
        check-in lands.
        """
        if self._store is None:
            return seed
        stored = await self._store.async_load()
        if not stored:
            return seed
        restored = dict(seed)
        for key in WX_SNAPSHOT_KEYS:
            if stored.get(key) is not None:
                restored[key] = stored[key]
        # last_seen round-trips through JSON as an ISO string; the availability
        # window and the timestamp sensor both need a real datetime back.
        last = restored.get("last_seen")
        if isinstance(last, str):
            restored["last_seen"] = dt_util.parse_datetime(last)
        return restored

    def _save_snapshot(self, data: dict) -> None:
        """Debounce-persist the telemetry fields of the latest check-in."""
        if self._store is None:
            return
        snapshot: dict = {}
        for key in WX_SNAPSHOT_KEYS:
            value = data.get(key)
            if isinstance(value, datetime):
                value = value.isoformat()
            if value is not None:
                snapshot[key] = value
        self._store.async_delay_save(lambda: snapshot, WX_SAVE_DELAY)

    async def _async_update_data(self) -> dict:
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                resp = await self._session.get(f"{self.base_url}/api/status")
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error talking to {self.host}: {err}") from err
        self._reconcile_sw_version(data)
        return data

    def _reconcile_sw_version(self, data: dict) -> None:
        """Push the running firmware into the device registry when it changes.

        HA reads sw_version from device_info only at registration, so an OTA would
        otherwise show the old version until a reload. Updating the registry here
        keeps it current within a poll, including updates driven by the update
        entity.
        """
        mac = data.get("mac")
        fw = data.get("fw")
        if not mac or not fw:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, mac.replace(":", "").lower())}
        )
        if device and device.sw_version != fw:
            registry.async_update_device(device.id, sw_version=fw)

    async def async_command(self, path: str, payload: dict) -> None:
        """POST a command, then refresh so HA reflects the new state quickly."""
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                resp = await self._session.post(f"{self.base_url}{path}", json=payload)
                resp.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Command {path} failed: {err}") from err
        await self.async_request_refresh()

    async def async_ota_from_url(self, url: str) -> None:
        """Download a firmware image over TLS and install it on the device."""
        try:
            async with asyncio.timeout(OTA_DOWNLOAD_TIMEOUT):
                resp = await self._session.get(url)
                resp.raise_for_status()
                blob = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Failed to download firmware: {err}") from err
        await self.async_install_image(blob)

    async def async_install_image(self, blob: bytes) -> None:
        """Install a firmware image: the display pulls it, everything else is pushed."""
        if self.model in PULL_OTA_MODELS:
            await self._async_ota_pull(blob)
        else:
            await self._async_ota_push(blob)

    async def _async_ota_push(self, blob: bytes) -> None:
        """Upload the image as multipart to /update (the web UI's path); S31 and WX."""
        form = aiohttp.FormData()
        form.add_field(
            "firmware", blob, filename="firmware.bin",
            content_type="application/octet-stream",
        )
        try:
            async with asyncio.timeout(OTA_UPLOAD_TIMEOUT):
                resp = await self._session.post(f"{self.base_url}/update", data=form)
                resp.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Failed to flash {self.host}: {err}") from err

    async def _async_ota_pull(self, blob: bytes) -> None:
        """Serve the image on a one-time URL and have the device fetch it (/api/ota).

        Returns once the device has downloaded the whole image; it installs and
        reboots by itself, and the next check-in reports the new version.
        """
        url, path, served = ota_serve.async_offer(self.hass, blob)
        payload = {"path": path, **({"url": url} if url else {})}
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                resp = await self._session.post(f"{self.base_url}/api/ota", json=payload)
                resp.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"{self.host} did not accept the update: {err}") from err
        try:
            async with asyncio.timeout(OTA_UPLOAD_TIMEOUT):
                await served.wait()
        except asyncio.TimeoutError as err:
            raise HomeAssistantError(f"{self.host} never downloaded the update") from err

    async def async_update_settings(self, changes: dict, push_path: str | None = None) -> None:
        """Store device settings owned by HA entities, and push them to the device.

        The push is best effort: the next check-in bundle carries the same values, so
        a device that missed it (asleep, rebooting) still catches up within a poll.
        """
        data = dict(self.data or {})
        data.update(changes)
        self.async_set_updated_data(data)
        self._save_snapshot(data)
        if not push_path:
            return
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                resp = await self._session.post(f"{self.base_url}{push_path}", json=changes)
                resp.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Push to %s%s failed, next check-in carries it: %s",
                          self.host, push_path, err)

    def async_ingest_checkin(self, body: dict) -> None:
        """Fold a check-in into the coordinator data and notify entities.

        Called from the webhook handler. The WX sends battery, voltage, firmware
        version, and wake reason; the PD sends version, wifi signal, and uptime.
        We stamp a last-seen time that drives availability.
        """
        data = dict(self.data or {})
        if isinstance(body.get("battery"), (int, float)):
            data["battery"] = body["battery"]
        if isinstance(body.get("voltage"), (int, float)):
            data["voltage"] = body["voltage"]
        if body.get("version"):
            data["fw"] = body["version"]
        if body.get("wake"):
            data["wake"] = body["wake"]
        if isinstance(body.get("rssi"), (int, float)):
            data["rssi"] = body["rssi"]
        if isinstance(body.get("uptime"), (int, float)):
            data["uptime"] = body["uptime"]
        if isinstance(body.get("backlight"), bool):
            data["backlight"] = body["backlight"]  # a touch can turn it back on
        data["last_seen"] = dt_util.utcnow()
        self._reconcile_sw_version(data)
        self.async_set_updated_data(data)
        self._save_snapshot(data)
