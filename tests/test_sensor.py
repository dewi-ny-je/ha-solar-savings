"""Tests for which sensors an entry publishes."""

from __future__ import annotations

from custom_components.solar_savings.calculator import (
    ENERGY_VALUE_KEYS,
    SETTABLE_VALUE_KEYS,
    SolarSavingsValues,
)
from custom_components.solar_savings.sensor import SENSOR_DESCRIPTIONS, descriptions_for


def test_every_sensor_reads_an_existing_value() -> None:
    """Sensor descriptions and calculator values must not drift apart."""
    published = {description.value_key for description in SENSOR_DESCRIPTIONS}

    assert published == set(SolarSavingsValues.__dataclass_fields__)


def test_settable_keys_are_all_published() -> None:
    """Every settable total needs an entity to target the action at."""
    published = {description.value_key for description in SENSOR_DESCRIPTIONS}

    assert set(SETTABLE_VALUE_KEYS) <= published
    assert set(ENERGY_VALUE_KEYS) <= set(SETTABLE_VALUE_KEYS)


def test_a_solar_only_entry_keeps_its_original_sensors() -> None:
    """Without a battery the entry publishes the solar split, as before."""
    enabled = {
        description.key
        for description in descriptions_for(track_battery=False)
        if description.entity_registry_enabled_default
    }

    assert enabled == {
        "self_consumption_savings",
        "export_revenue",
        "solar_savings",
        "battery_savings",
        "total_savings",
    }


def test_a_battery_entry_leads_with_the_three_savings() -> None:
    """With a battery the solar split and the scenario details start hidden."""
    descriptions = descriptions_for(track_battery=True)
    enabled = {
        description.key
        for description in descriptions
        if description.entity_registry_enabled_default
    }

    assert enabled == {"solar_savings", "battery_savings", "total_savings"}
    # The details are still created, so they can be switched on per entity.
    assert {"actual_cost", "virtual_import_without_solar"} <= {
        description.key for description in descriptions
    }
