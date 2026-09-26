"""Config flow for FireLabs."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_LOCATION,
    CONF_PD_ANOTHER,
    CONF_PD_LIGHT,
    CONF_PD_LIGHT_POWER,
    CONF_PD_PLANTS,
    CONF_PD_POLL,
    CONF_PD_SCREEN_FOLLOWS_LIGHT,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SLEEP_MIN,
    CONF_WEATHER_ENTITY,
    CONF_WEBHOOK_ID,
    DEFAULT_PD_POLL,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SLEEP_MIN,
    DOMAIN,
    HTTP_TIMEOUT,
    MODEL_PD,
    MODEL_WX,
    PD_PLANT_FIELDS,
    PD_PLANT_SLOTS,
    PD_READINGS,
    WEBHOOK_MODELS,
    WX_CURRENT_FIELDS,
    pd_key,
)


class FirelabsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FireLabs."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._title: str | None = None
        self._status: dict | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FirelabsOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                status = await self._probe(host)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if not status.get("mac"):
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(_uid(status["mac"]))
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    return self.async_create_entry(
                        title=_title(status), data=_entry_data(status, host)
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a device found via mDNS."""
        host = discovery_info.host
        try:
            status = await self._probe(host)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return self.async_abort(reason="cannot_connect")
        if not status.get("mac"):
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(_uid(status["mac"]))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._host = host
        self._title = _title(status)
        self._status = status
        self.context["title_placeholders"] = {"name": self._title}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._title, data=_entry_data(self._status, self._host)
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._title},
        )

    async def _probe(self, host: str) -> dict:
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(HTTP_TIMEOUT):
            resp = await session.get(f"http://{host}/api/status")
            resp.raise_for_status()
            return await resp.json()


class FirelabsOptionsFlow(OptionsFlow):
    """Map HA entities to a display's bundle and set the device's settings."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._data: dict[str, Any] = {}
        self._plant = 0

    def _webhook_url(self) -> str:
        webhook_id = self._entry.data.get(CONF_WEBHOOK_ID)
        return (
            webhook.async_generate_url(self.hass, webhook_id, allow_external=False)
            if webhook_id
            else ""
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        model = self._entry.data.get("model")
        if model == MODEL_PD:
            return await self.async_step_pd()
        if model != MODEL_WX:
            return self.async_abort(reason="no_options")
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_wx_options_schema(self._entry.options),
            description_placeholders={"webhook_url": self._webhook_url()},
        )

    async def async_step_pd(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Plant Display, first page: which plants, the grow light, the refresh interval.

        Picking plants from the plants-hass integration skips the per-plant pages.
        """
        choices = {
            entry_id: plant.name
            for entry_id, plant in self.hass.data.get("plants", {}).get("plants", {}).items()
        }
        if user_input is not None:
            self._data.update(user_input)
            if user_input.get(CONF_PD_PLANTS):
                return self.async_create_entry(title="", data=self._data)
            self._data.pop(CONF_PD_PLANTS, None)
            self._plant = 1
            return await self.async_step_plant()
        return self.async_show_form(
            step_id="pd",
            data_schema=_pd_options_schema(self._entry.options, choices),
            description_placeholders={"webhook_url": self._webhook_url()},
        )

    async def async_step_plant(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One page per plant; "add another" moves on to the next slot.

        A slot with no moisture sensor is left off the display. Unticking "add
        another" ends the list, which also drops any later plants.
        """
        if user_input is not None:
            for field in PD_PLANT_FIELDS:
                value = user_input.get(field)
                if value not in (None, ""):
                    self._data[pd_key(self._plant, field)] = value
            if not user_input.get(CONF_PD_ANOTHER) or self._plant >= PD_PLANT_SLOTS:
                return self.async_create_entry(title="", data=self._data)
            self._plant += 1
        return self.async_show_form(
            step_id="plant",
            data_schema=_pd_plant_schema(self._entry.options, self._plant),
            description_placeholders={"n": str(self._plant)},
        )


def _wx_options_schema(opts: dict) -> vol.Schema:
    def entity(domains: list[str]) -> selector.EntitySelector:
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domains)
        )

    def hour() -> selector.NumberSelector:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=23, step=1, mode=selector.NumberSelectorMode.BOX
            )
        )

    fields: dict[Any, Any] = {}
    fields[
        vol.Optional(CONF_LOCATION, description={"suggested_value": opts.get(CONF_LOCATION)})
    ] = selector.TextSelector()
    for field, key in WX_CURRENT_FIELDS.items():
        domains = ["sensor", "weather"] if field == "condition" else ["sensor"]
        fields[vol.Optional(key, description={"suggested_value": opts.get(key)})] = entity(domains)

    fields[
        vol.Optional(
            CONF_WEATHER_ENTITY,
            description={"suggested_value": opts.get(CONF_WEATHER_ENTITY)},
        )
    ] = entity(["weather"])

    fields[
        vol.Optional(CONF_SLEEP_MIN, default=opts.get(CONF_SLEEP_MIN, DEFAULT_SLEEP_MIN))
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=5, max=240, step=5, mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    )
    fields[
        vol.Optional(CONF_QUIET_START, default=opts.get(CONF_QUIET_START, DEFAULT_QUIET_START))
    ] = hour()
    fields[
        vol.Optional(CONF_QUIET_END, default=opts.get(CONF_QUIET_END, DEFAULT_QUIET_END))
    ] = hour()

    return vol.Schema(fields)


def _pd_options_schema(opts: dict, plants: dict[str, str]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if plants:
        fields[
            vol.Optional(CONF_PD_PLANTS, description={"suggested_value": opts.get(CONF_PD_PLANTS)})
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=k, label=v) for k, v in plants.items()],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    return vol.Schema(
        {
            **fields,
            vol.Optional(
                CONF_PD_LIGHT, description={"suggested_value": opts.get(CONF_PD_LIGHT)}
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "light", "input_boolean"])
            ),
            vol.Optional(
                CONF_PD_LIGHT_POWER,
                description={"suggested_value": opts.get(CONF_PD_LIGHT_POWER)},
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"])),
            vol.Optional(
                CONF_PD_SCREEN_FOLLOWS_LIGHT,
                default=opts.get(CONF_PD_SCREEN_FOLLOWS_LIGHT, True),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_PD_POLL, default=opts.get(CONF_PD_POLL, DEFAULT_PD_POLL)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=600, step=15, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
    )


def _pd_plant_schema(opts: dict, n: int) -> vol.Schema:
    def entity(domains: list[str]) -> selector.EntitySelector:
        return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))

    def opt(field: str) -> vol.Optional:
        return vol.Optional(field, description={"suggested_value": opts.get(pd_key(n, field))})

    fields: dict[Any, Any] = {
        opt("name"): selector.TextSelector(),
        opt("species"): selector.TextSelector(),
    }
    for reading in PD_READINGS.values():
        fields[opt(reading)] = entity(["sensor"])
    fields[opt("floor")] = entity(["input_number", "number", "sensor"])
    fields[opt("ceiling")] = entity(["input_number", "number", "sensor"])
    fields[opt("watered")] = entity(["input_datetime", "sensor"])
    if n < PD_PLANT_SLOTS:
        # Default to continuing when the next slot is already set up, so editing
        # plant 1 of 5 doesn't silently drop plants 2 to 5.
        fields[
            vol.Optional(CONF_PD_ANOTHER, default=bool(opts.get(pd_key(n + 1, "moisture"))))
        ] = selector.BooleanSelector()
    return vol.Schema(fields)


def _uid(mac: str) -> str:
    return mac.replace(":", "").lower()


def _title(status: dict) -> str:
    return status.get("name") or f"FireLabs {status.get('model', '')}".strip()


def _entry_data(status: dict, host: str) -> dict:
    data = {
        CONF_HOST: host,
        "mac": status.get("mac"),
        "model": status.get("model"),
        "name": status.get("name"),
        "fw": status.get("fw"),
    }
    if status.get("model") in WEBHOOK_MODELS:
        data[CONF_WEBHOOK_ID] = webhook.async_generate_id()
    return {k: v for k, v in data.items() if v is not None}
