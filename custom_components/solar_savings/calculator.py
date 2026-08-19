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

# Public cumulative totals that may be overwritten directly. The derived
# ``total_savings`` value is intentionally excluded because it is always
# recomputed from these two source totals.
SETTABLE_VALUE_KEYS = ("self_consumption_savings", "export_revenue")


def to_decimal(value: Any) -> Decimal | None:
    """Convert a Home Assistant state value into a Decimal, if possible."""
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def to_finite_decimal(value: Any) -> Decimal | None:
    """Convert a user-provided service value into a finite Decimal.

    Unlike :func:`to_decimal`, strings and integers are passed to ``Decimal``
    directly so the caller's exact precision is preserved; only floats fall back
    to their shortest round-trip string form, which avoids binary rounding
    artefacts. Booleans, non-numeric input, and non-finite values (NaN and
    infinities) return ``None`` so the service layer can reject them before
    anything is persisted.
    """
    if isinstance(value, bool):
        return None
    source: Any = str(value) if isinstance(value, float) else value
    try:
        candidate = Decimal(source)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not candidate.is_finite():
        return None
    return candidate


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
    pending_solar_energy: str = "0"
    unallocated_export_energy: str = "0"
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
        """Create a calculator from storage data.

        Snapshots written before monetary valuation moved to settlement time
        carry a ``pending_export_revenue`` field that no longer exists. Its
        value was already valued at the tariff in force when it was earned, so
        it is folded into the cumulative export revenue instead of being
        dropped.
        """
        if not data:
            return cls()
        allowed = SolarSavingsSnapshot.__dataclass_fields__.keys()
        snapshot_data = {key: data[key] for key in allowed if key in data}
        calculator = cls(SolarSavingsSnapshot(**snapshot_data))
        legacy_revenue = to_decimal(data.get("pending_export_revenue"))
        if legacy_revenue is not None and legacy_revenue != ZERO:
            snapshot = calculator._snapshot
            snapshot.export_revenue = str(
                Decimal(snapshot.export_revenue) + legacy_revenue
            )
        return calculator

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot."""
        return asdict(self._snapshot)

    def set_public_value(self, value_key: str, value: Decimal) -> bool:
        """Overwrite a settable public cumulative total with an explicit value.

        Returns ``True`` when the stored value actually changed so the caller
        can decide whether to persist and notify. Raises ``ValueError`` for keys
        that are not directly settable, such as the derived ``total_savings``.

        Negative monetary totals are valid because negative energy prices can
        make cumulative savings or revenue decrease below zero.
        """
        if value_key not in SETTABLE_VALUE_KEYS:
            msg = f"{value_key!r} is not a settable public value"
            raise ValueError(msg)

        if Decimal(getattr(self._snapshot, value_key)) == value:
            return False
        setattr(self._snapshot, value_key, str(value))
        return True

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

        # total_savings is derived from the two source totals and should not be
        # restored directly, otherwise it could disagree with them.
        if value_key not in SETTABLE_VALUE_KEYS:
            return False

        return self.set_public_value(value_key, restored)

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
    ) -> bool:
        """Process a smart meter import/export update.

        Import/export meters often update much more frequently than solar
        production meters. For each reading we calculate positive net export as
        ``export_delta - import_delta`` and accumulate the *energy* only. The
        clamp at zero is per reading, so it cannot be reconstructed from the
        meter values at the interval boundaries and has to stay here.

        No tariff is applied at this point: revenue is linear in energy, so
        while the export tariff is constant it is equivalent - and much
        cheaper - to value the accumulated energy once, in
        :meth:`settle_pending_accounting`.
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
            changed = True

        if import_energy is not None:
            self._snapshot.last_import_energy = str(import_energy)
            changed = True
        if export_energy is not None:
            self._snapshot.last_export_energy = str(export_energy)
            changed = True
        return changed

    def observe_solar_update(
        self,
        *,
        solar_energy: Decimal | None,
    ) -> bool:
        """Track a solar generation reading without performing monetary accounting.

        Solar readings may arrive as frequently as smart-meter readings. Every
        valid reading updates the solar baseline so resets are handled promptly,
        while only positive deltas are accumulated for later accounting.
        """
        previous_solar = to_decimal(self._snapshot.last_solar_energy)
        solar_delta = positive_delta(previous_solar, solar_energy)

        changed = False
        if solar_energy is not None:
            self._snapshot.last_solar_energy = str(solar_energy)
            changed = True

        if solar_delta > ZERO:
            pending_solar = Decimal(self._snapshot.pending_solar_energy)
            self._snapshot.pending_solar_energy = str(pending_solar + solar_delta)
            changed = True

        return changed

    def settle_pending_accounting(
        self,
        *,
        import_price: Decimal | None,
        export_price: Decimal | None,
    ) -> bool:
        """Convert pending solar/grid energy into monetary savings and revenue.

        This is the only place where energy is valued. The caller decides when
        a settlement happens: once per configured accounting interval, or
        immediately before a tariff change is applied, in which case it passes
        the tariffs that were in force *before* the change.

        ``pending_solar_energy`` holds the sum of the positive solar deltas
        observed since the previous settlement and ``pending_net_export_energy``
        the positive net export over the same window.

        Net export can be observed before the slower solar counter reports the
        generation that produced it. Export energy that no solar reading covers
        yet is therefore carried over in ``unallocated_export_energy`` and
        subtracted from the next settlement's generation, so a settlement that
        falls between two solar readings does not credit exported energy at the
        higher import tariff.

        A tariff that is needed but unknown - an unavailable price sensor, or a
        restart before the first price arrives - defers the whole settlement
        instead of dropping the energy: it stays pending and is valued by the
        next settlement that has a price.
        """
        pending_solar = Decimal(self._snapshot.pending_solar_energy)
        pending_export = Decimal(self._snapshot.pending_net_export_energy)
        carried_export = Decimal(self._snapshot.unallocated_export_energy)

        self_consumed_energy = pending_solar - (pending_export + carried_export)
        if self_consumed_energy < ZERO:
            carried_export = -self_consumed_energy
            self_consumed_energy = ZERO
        else:
            carried_export = ZERO

        if (pending_export > ZERO and export_price is None) or (
            self_consumed_energy > ZERO and import_price is None
        ):
            return False

        changed = False
        if pending_export > ZERO and export_price is not None:
            export_revenue = Decimal(self._snapshot.export_revenue)
            self._snapshot.export_revenue = str(
                export_revenue + (pending_export * export_price)
            )
            changed = True

        if self_consumed_energy > ZERO and import_price is not None:
            self_savings = Decimal(self._snapshot.self_consumption_savings)
            self._snapshot.self_consumption_savings = str(
                self_savings + (self_consumed_energy * import_price)
            )
            changed = True

        if pending_solar > ZERO or pending_export > ZERO:
            self._snapshot.pending_solar_energy = "0"
            self._snapshot.pending_net_export_energy = "0"
            changed = True

        if carried_export != Decimal(self._snapshot.unallocated_export_energy):
            self._snapshot.unallocated_export_energy = str(carried_export)
            changed = True

        return changed

    def handle_solar_update(
        self,
        *,
        solar_energy: Decimal | None,
        import_price: Decimal | None,
        export_price: Decimal | None = None,
    ) -> bool:
        """Process a solar update and settle accounting immediately.

        This wrapper keeps the settle-on-every-solar-reading behaviour
        available to tests and direct callers. The Home Assistant event layer
        instead calls :meth:`observe_solar_update` on every reading and defers
        :meth:`settle_pending_accounting` to the accounting interval or to a
        tariff change.
        """
        observed = self.observe_solar_update(solar_energy=solar_energy)
        settled = self.settle_pending_accounting(
            import_price=import_price,
            export_price=export_price,
        )
        return observed or settled
