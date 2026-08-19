"""Tests for integration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from custom_components.solar_savings import (
    _WARNED_UNITS_BY_ENTITY,
    energy_to_kwh,
    resolve_accounting_interval,
    unique_id_for,
)
from custom_components.solar_savings.const import (
    CONF_ACCOUNTING_INTERVAL,
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_MIN_ACCOUNTING_INTERVAL,
    CONF_SOLAR_ENERGY_SENSOR,
    DEFAULT_ACCOUNTING_INTERVAL,
)


@dataclass
class FakeState:
    entity_id: str
    state: str
    attributes: dict[str, str]


def test_energy_to_kwh_converts_wh() -> None:
    state = FakeState(
        "sensor.solar_energy",
        "1500",
        {"unit_of_measurement": "Wh"},
    )

    assert energy_to_kwh(state) == Decimal("1.500")


def test_energy_to_kwh_keeps_kwh() -> None:
    state = FakeState(
        "sensor.solar_energy",
        "1.5",
        {"unit_of_measurement": "kWh"},
    )

    assert energy_to_kwh(state) == Decimal("1.5")


def test_energy_to_kwh_rejects_unknown_unit() -> None:
    state = FakeState(
        "sensor.solar_energy",
        "1500",
        {"unit_of_measurement": "foo"},
    )

    assert energy_to_kwh(state) is None


def test_energy_to_kwh_logs_unknown_unit(caplog) -> None:
    _WARNED_UNITS_BY_ENTITY.clear()
    state = FakeState(
        "sensor.grid_energy",
        "12",
        {"unit_of_measurement": "foo"},
    )

    energy_to_kwh(state)

    assert "not supported" in caplog.text


def test_energy_to_kwh_logs_unknown_unit_once_per_entity_and_unit(caplog) -> None:
    _WARNED_UNITS_BY_ENTITY.clear()
    state = FakeState(
        "sensor.grid_energy",
        "12",
        {"unit_of_measurement": "foo"},
    )

    energy_to_kwh(state)
    energy_to_kwh(state)

    assert caplog.text.count("not supported") == 1


def test_resolve_accounting_interval_reads_the_configured_value() -> None:
    """The configured settlement period is used as-is."""
    interval = 900

    assert resolve_accounting_interval({CONF_ACCOUNTING_INTERVAL: interval}) == float(
        interval
    )


def test_resolve_accounting_interval_falls_back_to_the_renamed_option() -> None:
    """Entries created before the option was renamed keep their interval."""
    legacy_interval = 120

    assert resolve_accounting_interval(
        {CONF_MIN_ACCOUNTING_INTERVAL: legacy_interval}
    ) == float(legacy_interval)


def test_resolve_accounting_interval_prefers_the_current_option() -> None:
    """The renamed option only applies when the current one is absent."""
    interval = 30
    config = {CONF_ACCOUNTING_INTERVAL: interval, CONF_MIN_ACCOUNTING_INTERVAL: 120}

    assert resolve_accounting_interval(config) == float(interval)


def test_resolve_accounting_interval_defaults_and_clamps() -> None:
    """Missing, invalid, and negative values fall back to a usable interval."""
    assert resolve_accounting_interval({}) == float(DEFAULT_ACCOUNTING_INTERVAL)
    assert resolve_accounting_interval({CONF_ACCOUNTING_INTERVAL: "nope"}) == float(
        DEFAULT_ACCOUNTING_INTERVAL
    )
    assert resolve_accounting_interval({CONF_ACCOUNTING_INTERVAL: -5}) == 0.0


def test_unique_id_ignores_the_removed_import_sensor() -> None:
    """The unique ID is built from the two energy sensors still in use."""
    config = {
        CONF_SOLAR_ENERGY_SENSOR: "sensor.solar",
        CONF_EXPORT_ENERGY_SENSOR: "sensor.export",
        CONF_IMPORT_ENERGY_SENSOR: "sensor.import",
    }

    assert unique_id_for(config) == "sensor.solar|sensor.export"
