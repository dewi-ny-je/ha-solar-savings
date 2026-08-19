"""Constants for the Solar Savings integration."""

from __future__ import annotations

from enum import IntFlag
from typing import Final

DOMAIN: Final = "solar_savings"
PLATFORMS: Final = ["sensor"]


class SolarSavingsEntityFeature(IntFlag):
    """Supported features for Solar Savings entities."""

    SET_VALUE = 1


CONF_SOLAR_ENERGY_SENSOR: Final = "solar_energy_sensor"
CONF_IMPORT_PRICE_SENSOR: Final = "import_price_sensor"
CONF_EXPORT_ENERGY_SENSOR: Final = "export_energy_sensor"
CONF_EXPORT_PRICE_SENSOR: Final = "export_price_sensor"

# Selected by config entry version 1, when self-consumption was derived from
# net export instead of from the energy balance. Kept so the migration can
# strip it from existing entries.
CONF_IMPORT_ENERGY_SENSOR: Final = "import_energy_sensor"

CONF_ACCOUNTING_INTERVAL: Final = "accounting_interval"

# Key used before the accounting interval became a fixed settlement period
# instead of a throttle on solar-driven settlements. Existing config entries
# keep working by falling back to it.
CONF_MIN_ACCOUNTING_INTERVAL: Final = "minimum_accounting_interval"

DEFAULT_ACCOUNTING_INTERVAL: Final = 60

CONFIG_ENTRY_VERSION: Final = 2

SERVICE_SET_VALUE: Final = "set_value"
ATTR_VALUE: Final = "value"

STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.state"
STORAGE_SAVE_DELAY: Final = 10

SIGNAL_UPDATED: Final = f"{DOMAIN}_updated"
