"""Constants for the Solar Savings integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solar_savings"
PLATFORMS: Final = ["sensor"]

CONF_SOLAR_ENERGY_SENSOR: Final = "solar_energy_sensor"
CONF_IMPORT_ENERGY_SENSOR: Final = "import_energy_sensor"
CONF_IMPORT_PRICE_SENSOR: Final = "import_price_sensor"
CONF_EXPORT_ENERGY_SENSOR: Final = "export_energy_sensor"
CONF_EXPORT_PRICE_SENSOR: Final = "export_price_sensor"

STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.state"
STORAGE_SAVE_DELAY: Final = 10

SIGNAL_UPDATED: Final = f"{DOMAIN}_updated"
