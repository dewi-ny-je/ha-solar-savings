"""Tests for the battery-aware, scenario based accounting model.

The model values three grid positions with the same two tariffs: what actually
happened, what would have happened without the battery, and what would have
happened without the battery and without the panels. Every published figure is
a difference between two of them, so the tests below assert both the figures
and the identities that tie them together.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from custom_components.solar_savings.calculator import (
    ENERGY_VALUE_KEYS,
    SolarSavingsCalculator,
)

DAY_IMPORT = Decimal("0.30")
DAY_EXPORT = Decimal("0.08")
NIGHT_IMPORT = Decimal("0.35")
NIGHT_EXPORT = Decimal("0.06")


def battery_calculator() -> SolarSavingsCalculator:
    """Return a calculator seeded with every register at zero."""
    calc = SolarSavingsCalculator(track_battery=True)
    calc.seed(
        solar_energy=Decimal("0"),
        export_energy=Decimal("0"),
        grid_import_energy=Decimal("0"),
        battery_charge_energy=Decimal("0"),
        battery_discharge_energy=Decimal("0"),
    )
    return calc


def run_window(
    calc: SolarSavingsCalculator,
    *,
    readings: dict[str, str],
    import_price: Decimal,
    export_price: Decimal,
) -> None:
    """Observe one window of meter readings and settle it."""
    calc.observe(**{key: Decimal(value) for key, value in readings.items()})
    calc.split_scenarios()
    calc.settle_pending_accounting(
        import_price=import_price,
        export_price=export_price,
    )


def assert_scenarios_are_consistent(calc: SolarSavingsCalculator) -> None:
    """Assert the savings are exactly the differences between the scenarios."""
    values = calc.values
    assert values.total_savings == values.solar_savings + values.battery_savings
    assert values.solar_savings == (
        values.cost_without_battery_and_solar - values.cost_without_battery
    )
    assert values.battery_savings == values.cost_without_battery - values.actual_cost
    assert values.total_savings == (
        values.cost_without_battery_and_solar - values.actual_cost
    )


def test_grid_charged_battery_is_valued_at_the_arbitrage_it_earned() -> None:
    """Charging from the grid costs money; discharging later avoids more."""
    calc = battery_calculator()

    # Cheap hour: 5 kWh bought from the grid and stored.
    run_window(
        calc,
        readings={"grid_import_energy": "5", "battery_charge_energy": "5"},
        import_price=Decimal("0.10"),
        export_price=Decimal("0.02"),
    )

    # Without the battery those 5 kWh would never have been imported.
    assert calc.values.battery_savings == Decimal("-0.50")
    assert calc.values.actual_cost == Decimal("0.50")
    assert calc.values.cost_without_battery == Decimal("0")

    # Peak hour: the house runs on the battery instead of the grid.
    run_window(
        calc,
        readings={"battery_discharge_energy": "5"},
        import_price=Decimal("0.40"),
        export_price=Decimal("0.05"),
    )

    assert calc.values.battery_savings == Decimal("1.50")
    assert calc.values.solar_savings == Decimal("0")
    assert calc.values.total_savings == Decimal("1.50")
    assert calc.values.virtual_import_without_battery == Decimal("5")
    assert_scenarios_are_consistent(calc)


def test_solar_charged_battery_does_not_eat_into_solar_savings() -> None:
    """Stored solar is credited to the battery, not deducted from the panels."""
    calc = battery_calculator()

    # A sunny, empty house: 10 kWh generated, 4 kWh exported, 6 kWh stored.
    run_window(
        calc,
        readings={
            "solar_energy": "10",
            "export_energy": "4",
            "battery_charge_energy": "6",
        },
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )

    # Without the battery all 10 kWh would have been exported, so that is what
    # the panels earned, and the battery gave up the export price to store it.
    assert calc.values.solar_savings == Decimal("0.80")
    assert calc.values.self_consumption_savings == Decimal("0")
    assert calc.values.battery_savings == Decimal("-0.48")

    # After sunset the stored energy runs the house.
    run_window(
        calc,
        readings={"battery_discharge_energy": "6"},
        import_price=NIGHT_IMPORT,
        export_price=NIGHT_EXPORT,
    )

    assert calc.values.solar_savings == Decimal("0.80")
    assert calc.values.battery_savings == Decimal("1.62")
    assert calc.values.total_savings == Decimal("2.42")
    assert_scenarios_are_consistent(calc)


def test_self_consumption_split_survives_a_battery() -> None:
    """Generation that served the house directly keeps the import tariff."""
    calc = battery_calculator()

    # 10 kWh generated, 3 kWh exported, 2 kWh stored: 5 kWh served the house.
    run_window(
        calc,
        readings={
            "solar_energy": "10",
            "export_energy": "3",
            "battery_charge_energy": "2",
        },
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )

    # Without the battery, 5 kWh would have been exported instead of 3.
    assert calc.values.export_revenue == Decimal("0.40")
    assert calc.values.self_consumption_savings == Decimal("1.50")
    assert calc.values.solar_savings == Decimal("1.90")
    assert calc.values.battery_savings == Decimal("-0.16")
    assert_scenarios_are_consistent(calc)


def test_battery_discharged_into_the_grid_is_not_credited_to_solar() -> None:
    """Export that the panels did not produce must not become export revenue."""
    calc = battery_calculator()

    run_window(
        calc,
        readings={"export_energy": "4", "battery_discharge_energy": "4"},
        import_price=NIGHT_IMPORT,
        export_price=NIGHT_EXPORT,
    )

    assert calc.values.solar_savings == Decimal("0")
    assert calc.values.export_revenue == Decimal("0")
    # Selling stored energy is revenue the house would not have had.
    assert calc.values.battery_savings == Decimal("0.24")
    assert_scenarios_are_consistent(calc)


def test_idle_battery_reproduces_the_solar_only_model() -> None:
    """With the battery idle, the scenarios collapse onto the actual meters."""
    with_battery = battery_calculator()
    without_battery = SolarSavingsCalculator()
    without_battery.seed(solar_energy=Decimal("0"), export_energy=Decimal("0"))

    # Both registers advance inside one window, which is exactly the case a
    # net-position model would get wrong.
    run_window(
        with_battery,
        readings={
            "solar_energy": "10",
            "export_energy": "5",
            "grid_import_energy": "3",
        },
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )
    without_battery.observe_export_update(export_energy=Decimal("5"))
    without_battery.handle_solar_update(
        solar_energy=Decimal("10"),
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )

    assert with_battery.values.battery_savings == Decimal("0")
    assert with_battery.values.solar_savings == without_battery.values.total_savings
    assert (
        with_battery.values.self_consumption_savings
        == without_battery.values.self_consumption_savings
    )
    assert with_battery.values.export_revenue == without_battery.values.export_revenue
    assert_scenarios_are_consistent(with_battery)


def test_solar_savings_are_the_total_when_no_battery_is_tracked() -> None:
    """A solar-only entry publishes the same total under the new names."""
    calc = SolarSavingsCalculator()
    calc.seed(solar_energy=Decimal("0"), export_energy=Decimal("0"))

    calc.observe_export_update(export_energy=Decimal("4"))
    calc.handle_solar_update(
        solar_energy=Decimal("10"),
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )

    values = calc.values
    assert values.solar_savings == values.total_savings == Decimal("2.12")
    assert values.battery_savings == Decimal("0")
    # Without the grid-import register a scenario cannot be priced, so the
    # absolute costs stay untouched while the differences stay exact.
    assert values.actual_cost == Decimal("0")
    assert values.cost_without_battery_and_solar == Decimal("0")


def test_unreadable_battery_register_holds_the_energy_unsplit() -> None:
    """Grid energy waits for the battery reading instead of being misattributed."""
    calc = battery_calculator()

    # The meter reports while the battery sensor is unavailable.
    calc.observe(grid_import_energy=Decimal("2"))
    assert calc.split_scenarios() is True
    snapshot = calc.as_dict()
    assert snapshot["pending_import_energy"] == "2"

    # Held instead: the caller simply does not split until the battery reports.
    calc.observe(grid_import_energy=Decimal("5"))
    assert calc.as_dict()["unsplit_grid_import_energy"] == "3"

    calc.observe(battery_discharge_energy=Decimal("3"))
    calc.split_scenarios()
    calc.settle_pending_accounting(
        import_price=NIGHT_IMPORT,
        export_price=NIGHT_EXPORT,
    )

    # The 3 kWh the battery supplied is recognised even though the two
    # registers reported it at different moments.
    assert calc.values.battery_savings == Decimal("1.05")


def test_settlement_is_deferred_until_both_tariffs_are_known() -> None:
    """Scenario energy is held, not dropped, while a tariff is unknown."""
    calc = battery_calculator()
    calc.observe(
        grid_import_energy=Decimal("2"),
        battery_discharge_energy=Decimal("1"),
    )
    calc.split_scenarios()

    assert (
        calc.settle_pending_accounting(import_price=None, export_price=None) is False
    )
    assert calc.as_dict()["pending_import_energy"] == "2"

    assert (
        calc.settle_pending_accounting(
            import_price=NIGHT_IMPORT,
            export_price=NIGHT_EXPORT,
        )
        is True
    )
    assert calc.values.actual_cost == Decimal("0.70")
    assert calc.values.battery_savings == Decimal("0.35")


def test_slow_solar_register_does_not_shift_money_between_tariffs() -> None:
    """Export awaiting a solar reading keeps the split boundary-independent."""
    calc = battery_calculator()

    # The smart meter reports the export before the solar counter catches up.
    run_window(
        calc,
        readings={"export_energy": "2"},
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )
    assert calc.values.export_revenue == Decimal("0.16")
    assert calc.values.self_consumption_savings == Decimal("0")
    assert calc.as_dict()["unallocated_export_energy"] == "2"

    run_window(
        calc,
        readings={"solar_energy": "5"},
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )

    assert calc.values.self_consumption_savings == Decimal("0.90")
    assert calc.values.solar_savings == Decimal("1.06")
    assert_scenarios_are_consistent(calc)


@pytest.mark.parametrize("value_key", ENERGY_VALUE_KEYS)
def test_energy_totals_reject_negative_restores(value_key: str) -> None:
    """Virtual meters count kWh, so a negative restored value is not usable."""
    calc = battery_calculator()

    assert calc.restore_public_value(value_key, "-1") is False
    assert calc.restore_public_value(value_key, "12.5") is True
    assert getattr(calc.values, value_key) == Decimal("12.5")


def test_savings_totals_are_derived_and_not_settable() -> None:
    """Solar and total savings follow the totals they are calculated from."""
    calc = battery_calculator()

    for value_key in ("solar_savings", "total_savings"):
        with pytest.raises(ValueError, match="settable"):
            calc.set_public_value(value_key, Decimal("10"))

    calc.set_public_value("self_consumption_savings", Decimal("4"))
    calc.set_public_value("export_revenue", Decimal("1"))
    calc.set_public_value("battery_savings", Decimal("2.5"))

    assert calc.values.solar_savings == Decimal("5")
    assert calc.values.total_savings == Decimal("7.5")


def test_settlement_boundaries_do_not_change_the_totals() -> None:
    """Valuing every reading and valuing once per hour must agree."""
    per_reading = battery_calculator()
    once = battery_calculator()
    readings = [
        {"solar_energy": "2", "export_energy": "1", "battery_charge_energy": "1"},
        {"solar_energy": "5", "export_energy": "2", "battery_charge_energy": "3"},
        {"solar_energy": "9", "export_energy": "4", "battery_charge_energy": "5"},
    ]

    for reading in readings:
        run_window(
            per_reading,
            readings=reading,
            import_price=DAY_IMPORT,
            export_price=DAY_EXPORT,
        )
        once.observe(**{key: Decimal(value) for key, value in reading.items()})
        once.split_scenarios()

    once.settle_pending_accounting(
        import_price=DAY_IMPORT,
        export_price=DAY_EXPORT,
    )

    assert once.values == per_reading.values
