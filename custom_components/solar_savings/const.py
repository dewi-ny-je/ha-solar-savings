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

# Optional battery tracking. All three registers are selected together: the
# counterfactual scenarios need the grid import to value a scenario and both
# battery registers to remove the battery from it.
SECTION_BATTERY: Final = "battery"
CONF_GRID_IMPORT_ENERGY_SENSOR: Final = "grid_import_energy_sensor"
CONF_BATTERY_CHARGE_ENERGY_SENSOR: Final = "battery_charge_energy_sensor"
CONF_BATTERY_DISCHARGE_ENERGY_SENSOR: Final = "battery_discharge_energy_sensor"
BATTERY_CONF_KEYS: Final = (
    CONF_GRID_IMPORT_ENERGY_SENSOR,
    CONF_BATTERY_CHARGE_ENERGY_SENSOR,
    CONF_BATTERY_DISCHARGE_ENERGY_SENSOR,
)

# How long the scenario split waits for an unreadable battery register before
# it gives up and assumes the battery was idle. Battery meters usually report
# every few seconds, so a gap this long means the sensor is gone rather than
# late.
BATTERY_STALE_TIMEOUT: Final = 300.0

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
