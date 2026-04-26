"""Tests for Solar Savings calculations."""

from __future__ import annotations

from decimal import Decimal

from custom_components.solar_savings.calculator import (
    SolarSavingsCalculator,
    positive_delta,
    to_decimal,
)


def test_to_decimal_filters_invalid_states() -> None:
    """Unknown and unavailable HA states should not be parsed as values."""
    assert to_decimal("unknown") is None
    assert to_decimal("unavailable") is None
    assert to_decimal("1.23") == Decimal("1.23")


def test_positive_delta_filters_resets() -> None:
    """Daily meter resets should not reduce accumulated totals."""
    assert positive_delta(Decimal("10"), Decimal("12")) == Decimal("2")
    assert positive_delta(Decimal("10"), Decimal("0")) == Decimal("0")
    assert positive_delta(None, Decimal("12")) == Decimal("0")


def test_self_consumption_and_export_revenue_are_accumulated() -> None:
    """Solar generation is first valued as avoided imports, then exports."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("100"),
        import_energy=Decimal("50"),
        export_energy=Decimal("10"),
    )

    # Between solar updates, the smart meter sees 2 kWh imports and 4 kWh exports.
    # Only 2 kWh is net exported and receives the lower export tariff.
    calc.handle_grid_update(
        import_energy=Decimal("52"),
        export_energy=Decimal("14"),
        export_price=Decimal("0.08"),
    )

    # Solar production rose by 5 kWh. 2 kWh was net exported, so 3 kWh avoided
    # grid imports and receives the higher import tariff.
    calc.handle_solar_update(
        solar_energy=Decimal("105"),
        import_price=Decimal("0.30"),
    )

    assert calc.values.self_consumption_savings == Decimal("0.90")
    assert calc.values.export_revenue == Decimal("0.16")
    assert calc.values.total_savings == Decimal("1.06")


def test_negative_solar_delta_from_daily_reset_is_ignored() -> None:
    """Daily production sensors that reset at midnight must not regress totals."""
    calc = SolarSavingsCalculator()
    calc.seed(solar_energy=Decimal("18"), import_energy=Decimal("0"), export_energy=Decimal("0"))

    calc.handle_solar_update(solar_energy=Decimal("0.2"), import_price=Decimal("0.30"))

    assert calc.values.self_consumption_savings == Decimal("0")
    assert calc.values.total_savings == Decimal("0")


def test_export_revenue_waits_for_next_solar_update() -> None:
    """Pending export revenue is exposed after solar production allocation."""
    calc = SolarSavingsCalculator()
    calc.seed(solar_energy=Decimal("10"), import_energy=Decimal("5"), export_energy=Decimal("1"))

    calc.handle_grid_update(
        import_energy=Decimal("5"),
        export_energy=Decimal("2"),
        export_price=Decimal("0.05"),
    )

    assert calc.values.export_revenue == Decimal("0")

    calc.handle_solar_update(solar_energy=Decimal("12"), import_price=Decimal("0.25"))

    assert calc.values.export_revenue == Decimal("0.05")
    assert calc.values.self_consumption_savings == Decimal("0.25")



def test_negative_export_price_reduces_export_revenue() -> None:
    """Negative export tariffs should reduce cumulative export revenue."""
    calc = SolarSavingsCalculator()
    calc.seed(solar_energy=Decimal("100"), import_energy=Decimal("0"), export_energy=Decimal("0"))

    calc.handle_grid_update(
        import_energy=Decimal("0"),
        export_energy=Decimal("2"),
        export_price=Decimal("-0.05"),
    )

    assert calc.values.export_revenue == Decimal("0")

    calc.handle_solar_update(solar_energy=Decimal("102"), import_price=Decimal("0.30"))

    assert calc.values.export_revenue == Decimal("-0.10")
    assert calc.values.total_savings == Decimal("-0.10")

def test_snapshot_roundtrip_preserves_totals() -> None:
    """Storage snapshots should survive reloads without losing accounting state."""
    calc = SolarSavingsCalculator()
    calc.seed(solar_energy=Decimal("1"), import_energy=Decimal("1"), export_energy=Decimal("1"))
    calc.handle_solar_update(solar_energy=Decimal("2"), import_price=Decimal("0.40"))

    restored = SolarSavingsCalculator.from_dict(calc.as_dict())

    assert restored.values.self_consumption_savings == Decimal("0.40")

def test_restore_public_value_uses_highest_cumulative_value() -> None:
    """RestoreSensor data should recover lost totals without rolling them back."""
    calc = SolarSavingsCalculator()

    assert calc.restore_public_value("self_consumption_savings", "12.34") is True
    assert calc.values.self_consumption_savings == Decimal("12.34")

    assert calc.restore_public_value("self_consumption_savings", "10.00") is False
    assert calc.values.self_consumption_savings == Decimal("12.34")

    assert calc.restore_public_value("export_revenue", Decimal("1.23")) is True
    assert calc.values.export_revenue == Decimal("1.23")
    assert calc.values.total_savings == Decimal("13.57")


def test_restore_does_not_set_derived_total_directly() -> None:
    """The total sensor remains derived from self-consumption and export totals."""
    calc = SolarSavingsCalculator()

    assert calc.restore_public_value("total_savings", "99") is False
    assert calc.values.total_savings == Decimal("0")
