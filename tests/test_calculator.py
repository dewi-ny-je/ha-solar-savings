"""Tests for Solar Savings calculations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.solar_savings.calculator import (
    SolarSavingsCalculator,
    positive_delta,
    to_decimal,
    to_finite_decimal,
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
    )

    # Solar production rose by 5 kWh. 2 kWh was net exported, so 3 kWh avoided
    # grid imports and receives the higher import tariff.
    calc.handle_solar_update(
        solar_energy=Decimal("105"),
        import_price=Decimal("0.30"),
        export_price=Decimal("0.08"),
    )

    assert calc.values.self_consumption_savings == Decimal("0.90")
    assert calc.values.export_revenue == Decimal("0.16")
    assert calc.values.total_savings == Decimal("1.06")


def test_negative_solar_delta_from_daily_reset_is_ignored() -> None:
    """Daily production sensors that reset at midnight must not regress totals."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("18"),
        import_energy=Decimal("0"),
        export_energy=Decimal("0"),
    )

    calc.handle_solar_update(solar_energy=Decimal("0.2"), import_price=Decimal("0.30"))

    assert calc.values.self_consumption_savings == Decimal("0")
    assert calc.values.total_savings == Decimal("0")


def test_export_revenue_waits_for_next_solar_update() -> None:
    """Pending export revenue is exposed after solar production allocation."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("10"),
        import_energy=Decimal("5"),
        export_energy=Decimal("1"),
    )

    calc.handle_grid_update(
        import_energy=Decimal("5"),
        export_energy=Decimal("2"),
    )

    assert calc.values.export_revenue == Decimal("0")

    calc.handle_solar_update(
        solar_energy=Decimal("12"),
        import_price=Decimal("0.25"),
        export_price=Decimal("0.05"),
    )

    assert calc.values.export_revenue == Decimal("0.05")
    assert calc.values.self_consumption_savings == Decimal("0.25")



def test_negative_export_price_reduces_export_revenue() -> None:
    """Negative export tariffs should reduce cumulative export revenue."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("100"),
        import_energy=Decimal("0"),
        export_energy=Decimal("0"),
    )

    calc.handle_grid_update(
        import_energy=Decimal("0"),
        export_energy=Decimal("2"),
    )

    assert calc.values.export_revenue == Decimal("0")

    calc.handle_solar_update(
        solar_energy=Decimal("102"),
        import_price=Decimal("0.30"),
        export_price=Decimal("-0.05"),
    )

    assert calc.values.export_revenue == Decimal("-0.10")
    assert calc.values.total_savings == Decimal("-0.10")

def test_snapshot_roundtrip_preserves_totals() -> None:
    """Storage snapshots should survive reloads without losing accounting state."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("1"),
        import_energy=Decimal("1"),
        export_energy=Decimal("1"),
    )
    calc.handle_solar_update(solar_energy=Decimal("2"), import_price=Decimal("0.40"))

    restored = SolarSavingsCalculator.from_dict(calc.as_dict())

    assert restored.values.self_consumption_savings == Decimal("0.40")

def test_restore_public_value_uses_restored_source_value() -> None:
    """RestoreSensor data should recover signed source totals."""
    calc = SolarSavingsCalculator()

    assert calc.restore_public_value("self_consumption_savings", "12.34") is True
    assert calc.values.self_consumption_savings == Decimal("12.34")

    assert calc.restore_public_value("self_consumption_savings", "10.00") is True
    assert calc.values.self_consumption_savings == Decimal("10.00")

    assert calc.restore_public_value("export_revenue", Decimal("-1.23")) is True
    assert calc.values.export_revenue == Decimal("-1.23")
    assert calc.values.total_savings == Decimal("8.77")


def test_restore_public_value_ignores_invalid_and_unchanged_values() -> None:
    """RestoreSensor data should ignore non-numeric and unchanged values."""
    calc = SolarSavingsCalculator()

    assert calc.restore_public_value("self_consumption_savings", "unavailable") is False
    assert calc.restore_public_value("self_consumption_savings", "0") is False
    assert calc.values.self_consumption_savings == Decimal("0")


def test_restore_does_not_set_derived_total_directly() -> None:
    """The total sensor remains derived from self-consumption and export totals."""
    calc = SolarSavingsCalculator()

    assert calc.restore_public_value("total_savings", "99") is False
    assert calc.values.total_savings == Decimal("0")


def test_to_finite_decimal_preserves_string_precision() -> None:
    """String input keeps exact precision instead of degrading through a float."""
    assert to_finite_decimal("123.456789012345678") == Decimal("123.456789012345678")


def test_to_finite_decimal_converts_numbers() -> None:
    """Ints and floats are accepted as finite Decimals without artefacts."""
    assert to_finite_decimal(100) == Decimal("100")
    assert to_finite_decimal(123.45) == Decimal("123.45")
    assert to_finite_decimal("-3.20") == Decimal("-3.20")


def test_to_finite_decimal_rejects_non_finite() -> None:
    """NaN and infinities must be rejected before they can be persisted."""
    assert to_finite_decimal(float("nan")) is None
    assert to_finite_decimal(float("inf")) is None
    assert to_finite_decimal(float("-inf")) is None
    assert to_finite_decimal("nan") is None
    assert to_finite_decimal("inf") is None


def test_to_finite_decimal_rejects_invalid_input() -> None:
    """Non-numeric, empty, boolean, and missing inputs return None."""
    assert to_finite_decimal("abc") is None
    assert to_finite_decimal("") is None
    assert to_finite_decimal(None) is None
    assert to_finite_decimal(True) is None


def test_set_public_value_overwrites_self_consumption_savings() -> None:
    """Manually setting a source total replaces the stored value."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("1"),
        import_energy=Decimal("1"),
        export_energy=Decimal("1"),
    )
    calc.handle_solar_update(solar_energy=Decimal("5"), import_price=Decimal("0.30"))

    assert calc.set_public_value("self_consumption_savings", Decimal("42.50")) is True
    assert calc.values.self_consumption_savings == Decimal("42.50")
    assert calc.values.export_revenue == Decimal("0")
    assert calc.values.total_savings == Decimal("42.50")


def test_set_public_value_accepts_negative_export_revenue() -> None:
    """Export revenue can be set to a negative value to correct credits."""
    calc = SolarSavingsCalculator()

    assert calc.set_public_value("export_revenue", Decimal("-3.20")) is True
    assert calc.values.export_revenue == Decimal("-3.20")
    assert calc.values.total_savings == Decimal("-3.20")


def test_set_public_value_unchanged_returns_false() -> None:
    """Setting the current value reports no change so no save is scheduled."""
    calc = SolarSavingsCalculator()

    assert calc.set_public_value("self_consumption_savings", Decimal("0")) is False


def test_set_public_value_rejects_derived_total() -> None:
    """The derived total cannot be overwritten directly."""
    calc = SolarSavingsCalculator()

    with pytest.raises(ValueError, match="settable"):
        calc.set_public_value("total_savings", Decimal("10"))


def test_solar_deltas_can_be_accumulated_before_accounting() -> None:
    """Frequent solar readings should be grouped before monetary settlement."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("100"),
        import_energy=Decimal("50"),
        export_energy=Decimal("10"),
    )

    calc.handle_grid_update(
        import_energy=Decimal("51"),
        export_energy=Decimal("13"),
    )

    assert calc.observe_solar_update(solar_energy=Decimal("102")) is True
    assert calc.observe_solar_update(solar_energy=Decimal("105")) is True

    assert calc.values.self_consumption_savings == Decimal("0")
    assert calc.values.export_revenue == Decimal("0")

    assert (
        calc.settle_pending_accounting(
            import_price=Decimal("0.30"),
            export_price=Decimal("0.08"),
        )
        is True
    )

    # Pending solar = 5 kWh; pending net export = 2 kWh.
    assert calc.values.self_consumption_savings == Decimal("0.90")
    assert calc.values.export_revenue == Decimal("0.16")
    assert calc.values.total_savings == Decimal("1.06")


def test_energy_is_not_valued_between_settlements() -> None:
    """Meter readings only accumulate energy; no tariff is applied per reading."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("0"),
        import_energy=Decimal("0"),
        export_energy=Decimal("0"),
    )

    calc.handle_grid_update(import_energy=Decimal("0"), export_energy=Decimal("1"))
    calc.observe_solar_update(solar_energy=Decimal("1"))
    calc.handle_grid_update(import_energy=Decimal("0"), export_energy=Decimal("2"))
    calc.observe_solar_update(solar_energy=Decimal("2"))

    assert calc.values.total_savings == Decimal("0")

    snapshot = calc.as_dict()
    assert snapshot["pending_net_export_energy"] == "2"
    assert snapshot["pending_solar_energy"] == "2"


def test_single_settlement_matches_per_reading_valuation() -> None:
    """One settlement per interval gives the same money as valuing every reading."""
    per_reading = SolarSavingsCalculator()
    once = SolarSavingsCalculator()
    for calc in (per_reading, once):
        calc.seed(
            solar_energy=Decimal("0"),
            import_energy=Decimal("0"),
            export_energy=Decimal("0"),
        )

    import_price = Decimal("0.30")
    export_price = Decimal("0.08")
    readings = [
        (Decimal("1"), Decimal("3"), Decimal("6")),
        (Decimal("2"), Decimal("5"), Decimal("11")),
        (Decimal("2"), Decimal("9"), Decimal("18")),
    ]

    for import_energy, export_energy, solar_energy in readings:
        per_reading.handle_grid_update(
            import_energy=import_energy,
            export_energy=export_energy,
        )
        per_reading.handle_solar_update(
            solar_energy=solar_energy,
            import_price=import_price,
            export_price=export_price,
        )

        once.handle_grid_update(
            import_energy=import_energy,
            export_energy=export_energy,
        )
        once.observe_solar_update(solar_energy=solar_energy)

    once.settle_pending_accounting(
        import_price=import_price,
        export_price=export_price,
    )

    assert once.values.export_revenue == per_reading.values.export_revenue
    assert (
        once.values.self_consumption_savings
        == per_reading.values.self_consumption_savings
    )
    assert once.values.total_savings == Decimal("3.86")


def test_tariff_change_values_earlier_energy_at_the_previous_tariff() -> None:
    """Energy accumulated before a tariff change keeps the outgoing tariff."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("0"),
        import_energy=Decimal("0"),
        export_energy=Decimal("0"),
    )

    # Cheap hour: 2 kWh exported, 5 kWh generated.
    calc.handle_grid_update(import_energy=Decimal("0"), export_energy=Decimal("2"))
    calc.observe_solar_update(solar_energy=Decimal("5"))

    # The tariff is about to change, so settle at the tariffs still in force.
    calc.settle_pending_accounting(
        import_price=Decimal("0.20"),
        export_price=Decimal("0.05"),
    )

    assert calc.values.self_consumption_savings == Decimal("0.60")
    assert calc.values.export_revenue == Decimal("0.10")

    # Expensive hour: another 1 kWh exported and 4 kWh generated.
    calc.handle_grid_update(import_energy=Decimal("0"), export_energy=Decimal("3"))
    calc.observe_solar_update(solar_energy=Decimal("9"))
    calc.settle_pending_accounting(
        import_price=Decimal("0.40"),
        export_price=Decimal("0.10"),
    )

    assert calc.values.self_consumption_savings == Decimal("1.80")
    assert calc.values.export_revenue == Decimal("0.20")


def test_export_settled_before_the_solar_reading_is_not_counted_twice() -> None:
    """A settlement between two solar readings must not inflate self-consumption."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("0"),
        import_energy=Decimal("0"),
        export_energy=Decimal("0"),
    )

    # The smart meter reports 2 kWh net export before the slower solar counter
    # reports the generation that produced it.
    calc.handle_grid_update(import_energy=Decimal("0"), export_energy=Decimal("2"))
    calc.settle_pending_accounting(
        import_price=Decimal("0.30"),
        export_price=Decimal("0.08"),
    )

    assert calc.values.export_revenue == Decimal("0.16")
    assert calc.values.self_consumption_savings == Decimal("0")
    assert calc.as_dict()["unallocated_export_energy"] == "2"

    # The 5 kWh solar reading now covers that export, so only 3 kWh avoided
    # imports, exactly as if a single settlement had covered both readings.
    calc.observe_solar_update(solar_energy=Decimal("5"))
    calc.settle_pending_accounting(
        import_price=Decimal("0.30"),
        export_price=Decimal("0.08"),
    )

    assert calc.values.self_consumption_savings == Decimal("0.90")
    assert calc.values.export_revenue == Decimal("0.16")
    assert calc.as_dict()["unallocated_export_energy"] == "0"


def test_settlement_without_pending_energy_reports_no_change() -> None:
    """An interval with no energy must not schedule a save or a state write."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("10"),
        import_energy=Decimal("5"),
        export_energy=Decimal("1"),
    )

    assert (
        calc.settle_pending_accounting(
            import_price=Decimal("0.30"),
            export_price=Decimal("0.08"),
        )
        is False
    )


def test_legacy_pending_export_revenue_is_kept_on_upgrade() -> None:
    """Revenue already valued by an older version must survive the upgrade."""
    calc = SolarSavingsCalculator.from_dict(
        {
            "last_solar_energy": "10",
            "pending_net_export_energy": "1",
            "pending_export_revenue": "0.25",
            "export_revenue": "1.00",
            "self_consumption_savings": "2.00",
        }
    )

    assert calc.values.export_revenue == Decimal("1.25")
    assert calc.values.total_savings == Decimal("3.25")
    assert "pending_export_revenue" not in calc.as_dict()


def test_settlement_is_deferred_while_a_needed_tariff_is_unknown() -> None:
    """Energy is held, not dropped, until a tariff is available to value it."""
    calc = SolarSavingsCalculator()
    calc.seed(
        solar_energy=Decimal("0"),
        import_energy=Decimal("0"),
        export_energy=Decimal("0"),
    )

    calc.handle_grid_update(import_energy=Decimal("0"), export_energy=Decimal("2"))
    calc.observe_solar_update(solar_energy=Decimal("5"))

    assert calc.settle_pending_accounting(import_price=None, export_price=None) is False
    assert calc.as_dict()["pending_solar_energy"] == "5"
    assert calc.as_dict()["pending_net_export_energy"] == "2"

    assert (
        calc.settle_pending_accounting(
            import_price=Decimal("0.30"),
            export_price=Decimal("0.08"),
        )
        is True
    )
    assert calc.values.self_consumption_savings == Decimal("0.90")
    assert calc.values.export_revenue == Decimal("0.16")
