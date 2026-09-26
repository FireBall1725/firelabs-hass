"""Constants for the FireLabs integration."""

from datetime import timedelta

DOMAIN = "firelabs"
MANUFACTURER = "FireLabs"
DEFAULT_SCAN_INTERVAL = 5  # seconds
HTTP_TIMEOUT = 8  # seconds

# Firmware updates. The device reports its running version as `fw` in
# /api/status; the latest release is read from the model's GitHub repo, and
# install pushes the release .bin to the device's existing /update endpoint.
GITHUB_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
FIRMWARE_REPOS: dict[str, str] = {
    "S31": "FireBall1725/firelabs-s31-firmware",
    "WX": "FireBall1725/firelabs-weather-display",
    "PD": "FireBall1725/firelabs-plant-display",
}
RELEASE_TTL = 6 * 3600  # seconds; GitHub unauthenticated is 60 req/hr/IP
LATEST_POLL_INTERVAL = timedelta(hours=6)
OTA_DOWNLOAD_TIMEOUT = 60  # seconds to pull the .bin from GitHub
OTA_UPLOAD_TIMEOUT = 120  # seconds to upload + flash on the device
RELEASES_KEY = "firelabs_releases"  # shared release cache in hass.data

# Weather Display (model "WX"). Unlike the S31 this is a sleepy device: HA does
# not poll it. It wakes, POSTs telemetry to a per-entry webhook, and gets a
# weather bundle back in the response. The integration mostly configures the
# device and serves it data.
MODEL_WX = "WX"
CONF_WEBHOOK_ID = "webhook_id"

# Current-conditions bundle fields mapped to the option key holding the source
# entity_id. Every field is optional; an unmapped or unavailable source is just
# left out of the bundle.
WX_CURRENT_FIELDS: dict[str, str] = {
    "temp": "ent_temp",
    "feels_like": "ent_feels_like",
    "condition": "ent_condition",
    "humidity": "ent_humidity",
    "wind": "ent_wind",
    "gust": "ent_gust",
    "precip": "ent_precip",
    "uv": "ent_uv",
    "high": "ent_high",
    "low": "ent_low",
    "pop": "ent_pop",
}
CONF_WEATHER_ENTITY = "ent_weather"  # a weather.* entity drives the hourly strip
WX_FORECAST_SLOTS = 5
CONF_LOCATION = "location"  # header label; falls back to the HA location name

CONF_SLEEP_MIN = "sleep_min"
CONF_QUIET_START = "quiet_start"
CONF_QUIET_END = "quiet_end"
DEFAULT_SLEEP_MIN = 30
DEFAULT_QUIET_START = 21
DEFAULT_QUIET_END = 6

# A check-in is expected about every sleep_min (30 by default). Mark the device
# unavailable after it misses roughly three cycles.
WX_AVAILABLE_WINDOW = timedelta(minutes=95)

# A sleepy device only reports on its check-in, so its telemetry must survive an HA
# restart: persist the last check-in and restore it on startup instead of showing
# everything as unavailable (and a false firmware update) until the device next wakes.
WX_STORAGE_VERSION = 1
WX_SNAPSHOT_KEYS = (
    "battery", "voltage", "fw", "wake", "rssi", "last_seen",
    # PD display controls, owned by HA entities
    "backlight", "brightness", "auto_dim", "dim_after", "dim_level",
)
WX_SAVE_DELAY = 5  # seconds; debounce snapshot writes

# Plant Display (model "PD"). Mains powered and always on, but it talks to HA the
# same way as the WX: it POSTs to a per-entry webhook every poll interval and gets
# a plant bundle back. The plants and their entities live in the entry options.
MODEL_PD = "PD"
WEBHOOK_MODELS = (MODEL_WX, MODEL_PD)
# Devices that fetch their own firmware from a URL instead of taking an upload.
PULL_OTA_MODELS = (MODEL_PD,)

PD_PLANT_SLOTS = 12  # the overview pages three cards at a time
# Per-plant option keys are f"plant{n}_{field}". Readings map a bundle field to
# the option field holding its sensor.
PD_READINGS: dict[str, str] = {
    "moisture": "moisture",
    "temp": "temperature",
    "lux": "illuminance",
    "ec": "conductivity",
    "battery": "battery",
}
PD_PLANT_FIELDS = (
    "name", "species", *PD_READINGS.values(), "floor", "ceiling", "watered",
)
CONF_PD_LIGHT = "ent_light"
CONF_PD_LIGHT_POWER = "ent_light_power"
CONF_PD_POLL = "poll_sec"
CONF_PD_SCREEN_FOLLOWS_LIGHT = "screen_follows_light"
CONF_PD_ANOTHER = "another"  # options-flow only: show one more plant page
# Plants from the plants-hass integration, by config entry id. When set, the
# per-plant entity pickers are not used.
CONF_PD_PLANTS = "plants"
DEFAULT_PD_POLL = 60

PD_HISTORY_HOURS = 168  # the week of hourly moisture behind each card's curve
PD_HISTORY_TTL = 600  # seconds; statistics only change on the hour

# It checks in every minute or so; a few missed polls means it's gone.
PD_AVAILABLE_WINDOW = timedelta(minutes=10)


def pd_key(n: int, field: str) -> str:
    """Option key for plant slot n (1-based)."""
    return f"plant{n}_{field}"
