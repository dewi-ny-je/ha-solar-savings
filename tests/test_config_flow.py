"""Tests for Solar Savings config flow validation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from custom_components.solar_savings.config_flow import validate_input
from custom_components.solar_savings.const import (
    CONF_BATTERY_CHARGE_ENERGY_SENSOR,
    CONF_BATTERY_DISCHARGE_ENERGY_SENSOR,
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_GRID_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_SOLAR_ENERGY_SENSOR,
    SECTION_BATTERY,
)
from homeassistant.helpers import entity_registry as er

BASE_INPUT = {
    CONF_SOLAR_ENERGY_SENSOR: "sensor.solar_energy",
    CONF_IMPORT_PRICE_SENSOR: "sensor.import_price",
    CONF_EXPORT_ENERGY_SENSOR: "sensor.export_energy",
    CONF_EXPORT_PRICE_SENSOR: "sensor.export_price",
}
BATTERY_INPUT = {
    CONF_GRID_IMPORT_ENERGY_SENSOR: "sensor.grid_import_energy",
    CONF_BATTERY_CHARGE_ENERGY_SENSOR: "sensor.battery_charge_energy",
    CONF_BATTERY_DISCHARGE_ENERGY_SENSOR: "sensor.battery_discharge_energy",
}


class ExistingStates:
    """A state machine in which every entity exists."""

    def get(self, entity_id: str) -> object:  # noqa: ARG002
        """Return a placeholder state for any entity."""
        return object()


class StatesOnlyHass:
    """Enough of Home Assistant for the presence check to short-circuit."""

    states = ExistingStates()


@pytest.fixture
def any_entity_exists() -> Any:
    """Return a core object for which every selected entity is present."""
    return StatesOnlyHass()


def test_validate_input_accepts_a_complete_battery_section(
    any_entity_exists: Any,
) -> None:
    """All three battery sensors together are a valid selection."""
    errors = asyncio.run(
        validate_input(
            any_entity_exists,
            {**BASE_INPUT, SECTION_BATTERY: dict(BATTERY_INPUT)},
        )
    )

    assert errors == {}


def test_validate_input_accepts_an_empty_battery_section(
    any_entity_exists: Any,
) -> None:
    """Battery tracking stays optional."""
    errors = asyncio.run(
        validate_input(any_entity_exists, {**BASE_INPUT, SECTION_BATTERY: {}})
    )

    assert errors == {}


def test_validate_input_rejects_a_partial_battery_section(
    any_entity_exists: Any,
) -> None:
    """A scenario needs all three registers, so two of them is an error."""
    partial = dict(BATTERY_INPUT)
    del partial[CONF_BATTERY_DISCHARGE_ENERGY_SENSOR]

    errors = asyncio.run(
        validate_input(any_entity_exists, {**BASE_INPUT, SECTION_BATTERY: partial})
    )

    assert errors == {"base": "incomplete_battery_selection"}


def test_validate_input_rejects_a_battery_sensor_used_twice(
    any_entity_exists: Any,
) -> None:
    """The duplicate check spans the battery section as well."""
    battery = dict(BATTERY_INPUT)
    battery[CONF_BATTERY_CHARGE_ENERGY_SENSOR] = BASE_INPUT[CONF_EXPORT_ENERGY_SENSOR]

    errors = asyncio.run(
        validate_input(any_entity_exists, {**BASE_INPUT, SECTION_BATTERY: battery})
    )

    assert errors == {"base": "duplicate_entity"}


async def test_validate_input_accepts_registered_entity_without_state(hass) -> None:
    """Registered entities should pass validation even before state is available."""
    registry = er.async_get(hass)

    entity_ids = [
        "sensor.solar_energy",
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
            CONF_IMPORT_PRICE_SENSOR: "sensor.import_price",
            CONF_EXPORT_ENERGY_SENSOR: "sensor.export_energy",
            CONF_EXPORT_PRICE_SENSOR: "sensor.export_price",
        },
    )

    assert errors == {}


async def test_validate_input_rejects_disabled_registered_entity(hass) -> None:
    """Disabled registry entries should fail validation."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        "test",
        "solar_energy",
        suggested_object_id="solar_energy",
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    for object_id in ("import_price", "export_energy", "export_price"):
        registry.async_get_or_create(
            "sensor",
            "test",
            object_id,
            suggested_object_id=object_id,
        )

    errors = await validate_input(
        hass,
        {
            CONF_SOLAR_ENERGY_SENSOR: "sensor.solar_energy",
            CONF_IMPORT_PRICE_SENSOR: "sensor.import_price",
            CONF_EXPORT_ENERGY_SENSOR: "sensor.export_energy",
            CONF_EXPORT_PRICE_SENSOR: "sensor.export_price",
        },
    )

    assert errors == {"base": "entity_not_found"}
