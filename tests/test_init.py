"""Tests for integration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from custom_components.solar_savings import energy_to_kwh


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
    state = FakeState(
        "sensor.grid_energy",
        "12",
        {"unit_of_measurement": "foo"},
    )

    energy_to_kwh(state)

    assert "not supported" in caplog.text
