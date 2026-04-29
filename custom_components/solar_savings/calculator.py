"""Pure calculation model for Solar Savings.

The model is intentionally independent from Home Assistant so it can be tested
quickly and deterministically. Home Assistant event handling lives in
``__init__.py``; this module owns the accounting rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")


def to_decimal(value: Any) -> Decimal | None:
    """Convert a Home Assistant state value into a Decimal, if possible."""
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def positive_delta(previous: Decimal | None, current: Decimal | None) -> Decimal:
    """Return a positive meter delta, ignoring resets and invalid values.

    Energy sensors can be total-increasing or daily-reset counters. Negative
    deltas are therefore treated as reset/no-new-energy instead of subtracting
    from the accumulated savings.
    """
    if previous is None or current is None:
        return ZERO
    delta = current - previous
    if delta <= ZERO:
        return ZERO
    return delta


@dataclass(slots=True)
class SolarSavingsSnapshot:
    """Serializable state for Solar Savings accounting."""

    last_solar_energy: str | None = None
    last_import_energy: str | None = None
    last_export_energy: str | None = None
    pending_net_export_energy: str = "0"
    pending_export_revenue: str = "0"
    self_consumption_savings: str = "0"
    export_revenue: str = "0"


@dataclass(slots=True)
class SolarSavingsValues:
    """Public sensor values."""

    self_consumption_savings: Decimal
    export_revenue: Decimal
    total_savings: Decimal


class SolarSavingsCalculator:
    """Account for solar self-consumption savings and export revenue."""

    def __init__(self, snapshot: SolarSavingsSnapshot | None = None) -> None:
        """Initialize from an optional stored snapshot."""
        self._snapshot = snapshot or SolarSavingsSnapshot()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SolarSavingsCalculator:
        """Create a calculator from storage data."""
        if not data:
            return cls()
        allowed = SolarSavingsSnapshot.__dataclass_fields__.keys()
        snapshot_data = {key: data[key] for key in allowed if key in data}
        return cls(SolarSavingsSnapshot(**snapshot_data))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot."""
        return asdict(self._snapshot)

    def restore_public_value(self, value_key: str, value: Any) -> bool:
        """Restore a public cumulative value from Home Assistant state storage.

        Home Assistant stores the native value for sensors that inherit from
        RestoreSensor. The integration also persists its full accounting
        snapshot with Store, but restoring the public cumulative values gives us
        a second line of defence after restarts and reloads.

        Negative energy prices can make cumulative monetary totals decrease or
        become negative, so a restored value is valid even when it is lower than
        the current stored value.
        """
        restored = to_decimal(value)
        if restored is None:
            return False

        if value_key == "self_consumption_savings":
            current = Decimal(self._snapshot.self_consumption_savings)
            if restored != current:
                self._snapshot.self_consumption_savings = str(restored)
                return True
            return False

        if value_key == "export_revenue":
            current = Decimal(self._snapshot.export_revenue)
            if restored != current:
                self._snapshot.export_revenue = str(restored)
                return True
            return False

        # total_savings is derived from the two source totals and should not be
        # restored directly, otherwise it could disagree with them.
        return False
    
    @property
    def values(self) -> SolarSavingsValues:
        """Return current public sensor values."""
        self_savings = Decimal(self._snapshot.self_consumption_savings)
        export_revenue = Decimal(self._snapshot.export_revenue)
        return SolarSavingsValues(
            self_consumption_savings=self_savings,
            export_revenue=export_revenue,
            total_savings=self_savings + export_revenue,
        )

    def seed(
        self,
        *,
        solar_energy: Decimal | None,
        import_energy: Decimal | None,
        export_energy: Decimal | None,
    ) -> None:
        """Set initial baselines without creating revenue.

        This prevents a restart or first setup from treating the meter's full
        historical value as newly generated energy.
        """
        if solar_energy is not None and self._snapshot.last_solar_energy is None:
            self._snapshot.last_solar_energy = str(solar_energy)
        if import_energy is not None and self._snapshot.last_import_energy is None:
            self._snapshot.last_import_energy = str(import_energy)
        if export_energy is not None and self._snapshot.last_export_energy is None:
            self._snapshot.last_export_energy = str(export_energy)

    def handle_grid_update(
        self,
        *,
        import_energy: Decimal | None,
        export_energy: Decimal | None,
        export_price: Decimal | None,
    ) -> bool:
        """Process a smart meter import/export update.

        Import/export meters often update much more frequently than solar
        production meters. For each interval we calculate positive net export
        as ``export_delta - import_delta`` and accumulate it until the next
        solar-production update can allocate generation between self-consumed
        energy and exported energy.
        """
        previous_import = to_decimal(self._snapshot.last_import_energy)
        previous_export = to_decimal(self._snapshot.last_export_energy)

        import_delta = positive_delta(previous_import, import_energy)
        export_delta = positive_delta(previous_export, export_energy)
        net_export = export_delta - import_delta
        if net_export < ZERO:
            net_export = ZERO

        changed = False
        if net_export > ZERO:
            pending_energy = Decimal(self._snapshot.pending_net_export_energy)
            self._snapshot.pending_net_export_energy = str(pending_energy + net_export)
            if export_price is not None:
                pending_revenue = Decimal(self._snapshot.pending_export_revenue)
                self._snapshot.pending_export_revenue = str(
                    pending_revenue + (net_export * export_price)
                )
            changed = True

        if import_energy is not None:
            self._snapshot.last_import_energy = str(import_energy)
            changed = True
        if export_energy is not None:
            self._snapshot.last_export_energy = str(export_energy)
            changed = True
        return changed

    def handle_solar_update(
        self,
        *,
        solar_energy: Decimal | None,
        import_price: Decimal | None,
    ) -> bool:
        """Process a solar generation update.

        Generated energy first offsets grid imports. Any net export that was
        observed since the previous solar update is subtracted from the positive
        solar delta. The remainder is valued using the current import price.
        """
        previous_solar = to_decimal(self._snapshot.last_solar_energy)
        solar_delta = positive_delta(previous_solar, solar_energy)
        if solar_energy is not None:
            self._snapshot.last_solar_energy = str(solar_energy)

        pending_export = Decimal(self._snapshot.pending_net_export_energy)
        pending_export_revenue = Decimal(self._snapshot.pending_export_revenue)
        self_consumed_energy = solar_delta - pending_export
        if self_consumed_energy < ZERO:
            self_consumed_energy = ZERO

        changed = solar_energy is not None
        if self_consumed_energy > ZERO and import_price is not None:
            self_savings = Decimal(self._snapshot.self_consumption_savings)
            self._snapshot.self_consumption_savings = str(
                self_savings + (self_consumed_energy * import_price)
            )
            changed = True

        if pending_export_revenue != ZERO:
            export_revenue = Decimal(self._snapshot.export_revenue)
            self._snapshot.export_revenue = str(export_revenue + pending_export_revenue)
            changed = True

        if pending_export > ZERO or pending_export_revenue != ZERO:
            self._snapshot.pending_net_export_energy = "0"
            self._snapshot.pending_export_revenue = "0"
            changed = True

        return changed
