# FireLabs for Home Assistant

A local Home Assistant integration for FireLabs devices. It talks to each device over its local HTTP API, so it needs no MQTT, no broker, and no cloud. Entities are built from what the device reports at `/api/status`, so this one integration covers the whole FireLabs line rather than a single model.

[![Open your Home Assistant instance and open the FireLabs repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=FireLabsCA&repository=firelabs-hass&category=integration)
[![Open your Home Assistant instance and start setting up FireLabs.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=firelabs)

## Supported devices

| Device | Notes |
|---|---|
| Sonoff S31 (FireLabs firmware) | relay plus power metering |
| Sonoff S31 Lite (FireLabs firmware) | relay only; no power metering |

More FireLabs devices get added here as they ship. A device works as soon as it runs FireLabs firmware and serves the standard `/api/status` fields; the integration creates the entities each device advertises and skips the rest.

## Requirements

- A device running FireLabs firmware, already on your wifi.
- Home Assistant 2024.4 or newer.

## Install (HACS)

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/FireLabsCA/firelabs-hass` with category **Integration**.
3. Install **FireLabs**, then restart Home Assistant.
4. Devices on your network are found automatically: Home Assistant shows a "discovered" card and you just confirm. To add one by hand instead, go to **Settings → Devices & Services → Add Integration**, search **FireLabs**, and enter the device's IP or `fl-<name>.local` hostname.

A device is identified by its MAC, so its IP can change without breaking the device or its history.

## Entities

The integration adds whatever a device supports. For the S31 that is:

| Entity | Type | Notes |
|---|---|---|
| Relay | switch | the outlet; primary control |
| Power, Voltage, Current, Energy today | sensor | metered devices only |
| Fault | binary_sensor | relay on but drawing ~0 W |
| No-load indicator | switch | config; metered devices only |
| Restore mode | select | Off / On / Last |
| Identify | switch | blink the LED to find the device |
| WiFi signal, Uptime | sensor | diagnostic, disabled by default |
| Restart | button | reboot the device |

On a device without a power-metering chip (such as the S31 Lite) the power sensors, fault, and no-load entities are left out automatically.

## How it works

Home Assistant polls `GET /api/status` every 5 seconds and writes changes with `POST /api/relay`, `/api/config`, `/api/identify`, and `/api/restart`. Discovery uses the `_firelabs._tcp` mDNS service the firmware advertises. Everything stays on your LAN.

## MQTT or this integration

The firmware also supports MQTT Discovery. Use one or the other, not both, to avoid two copies of the same entities. This integration is the path for setups that don't run MQTT.

## Support

Questions, updates, and works in progress: [FireBall Codes on Discord](https://discord.gg/QpV82CFfVD).

If this saved you some time, you can [buy me a sushi roll](https://ko-fi.com/fireball1725).

## License

[GNU AGPL-3.0](LICENSE).
