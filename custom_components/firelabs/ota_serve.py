"""Hand a firmware image to a device that pulls its own update.

The plant display (ESP32-S3) installs firmware by fetching a URL (POST /api/ota),
because streaming an upload into it while flash writes keep switching its cache off
drops wifi packets and stalls. HA already has the image in memory, so it serves it
here under a random one-time token and tells the device to fetch it.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN

OTA_PATH = "/api/firelabs/ota/{token}"
OTA_TTL = 15 * 60  # seconds a served image stays available (the device retries)
_KEY = "ota_blobs"


@dataclass
class _Blob:
    data: bytes
    expires: float
    served: asyncio.Event = field(default_factory=asyncio.Event)


class FirelabsOtaView(HomeAssistantView):
    """Serves a firmware image to the device; the token is the only credential."""

    url = OTA_PATH
    name = "api:firelabs:ota"
    requires_auth = False  # the device has no HA login; the token is unguessable

    def __init__(self, blobs: dict[str, _Blob]) -> None:
        self._blobs = blobs

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        _expire(self._blobs)
        blob = self._blobs.get(token)
        if blob is None:
            return web.Response(status=404)
        resp = web.StreamResponse(
            headers={"Content-Type": "application/octet-stream",
                     "Content-Length": str(len(blob.data))}
        )
        await resp.prepare(request)
        await resp.write(blob.data)
        await resp.write_eof()
        blob.served.set()
        return resp


def _expire(blobs: dict[str, _Blob]) -> None:
    now = time.monotonic()
    for token in [t for t, b in blobs.items() if b.expires < now]:
        del blobs[token]


def _blobs(hass: HomeAssistant) -> dict[str, _Blob]:
    data = hass.data.setdefault(DOMAIN, {})
    if _KEY not in data:
        data[_KEY] = {}
        hass.http.register_view(FirelabsOtaView(data[_KEY]))
    return data[_KEY]


def async_offer(
    hass: HomeAssistant, image: bytes
) -> tuple[str | None, str, asyncio.Event]:
    """Make `image` fetchable for OTA_TTL.

    Returns a full URL (or None), the bare path, and an event set once it's served.
    The device prefers the path, resolved against the base URL of its own webhook,
    since that's an address it already reaches; HA's configured internal URL can be
    stale or unroutable from the device's network. The full URL is the fallback,
    external first because a device checking in over the external URL can reach it.
    """
    try:
        base: str | None = get_url(hass, allow_ip=True, prefer_external=True)
    except NoURLAvailableError:
        base = None
    blobs = _blobs(hass)
    _expire(blobs)
    token = secrets.token_urlsafe(24)
    blob = _Blob(image, time.monotonic() + OTA_TTL)
    blobs[token] = blob
    path = OTA_PATH.format(token=token)
    return (f"{base}{path}" if base else None), path, blob.served
