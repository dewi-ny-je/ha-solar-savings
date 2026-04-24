"""Tests for Solar Savings config flow validation."""

from __future__ import annotations

from custom_components.solar_savings.config_flow import validate_input
from custom_components.solar_savings.const import (
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_SOLAR_ENERGY_SENSOR,
)
from homeassistant.helpers import entity_registry as er


async def test_validate_input_accepts_registered_entity_without_state(hass) -> None:
    """Registered entities should pass validation even before state is available."""
    registry = er.async_get(hass)

    entity_ids = [
        "sensor.solar_energy",
        "sensor.import_energy",
        "sensor.import_price",
        "sensor.export_energy",
        "sensor.export_price",
    ]

    for entity_id in entity_ids:
        registry.async_get_or_create(
            "sensor",
            "test",
            entity_id.split(".", 1)[1],
            suggested_object_id=entity_id.split(".", 1)[1],
        )

    errors = await validate_input(
        hass,
        {
            CONF_SOLAR_ENERGY_SENSOR: "sensor.solar_energy",
            CONF_IMPORT_ENERGY_SENSOR: "sensor.import_energy",
            CONF_IMPORT_PRICE_SENSOR: "sensor.import_price",
            CONF_EXPORT_ENERGY_SENSOR: "sensor.export_energy",
            CONF_EXPORT_PRICE_SENSOR: "sensor.export_price",
        },
    )

    assert errors == {}
