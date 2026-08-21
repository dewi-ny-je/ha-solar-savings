"""Pure calculation model for Solar Savings.

The model is intentionally independent from Home Assistant so it can be tested
quickly and deterministically. Home Assistant event handling lives in
``__init__.py``; this module owns the accounting rules.

Accounting is a three stage pipeline:

``observe``
    Reads the energy registers and accumulates their positive deltas. Nothing
    is interpreted here, so a register that lags behind its siblings only
    delays accounting instead of distorting it.

``split_scenarios``
    Turns the observed deltas into the grid position of three scenarios: what
    actually happened, what would have happened without the battery, and what
    would have happened without the battery and without the panels. Only
    energy is involved, so this can run as often as the registers update.

``settle_pending_accounting``
    Values the accumulated energy of each scenario at the tariffs in force.
    Costs and savings are differences between the scenarios.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")

# Public cumulative totals that may be overwritten directly. The derived
# ``solar_savings`` and ``total_savings`` values are intentionally excluded
# because they are always recomputed from the totals below.
SETTABLE_VALUE_KEYS = (
    "self_consumption_savings",
    "export_revenue",
    "battery_savings",
    "actual_cost",
    "cost_without_battery",
    "cost_without_battery_and_solar",
    "virtual_import_without_battery",
    "virtual_export_without_battery",
    "virtual_import_without_solar",
)

# Settable totals that count energy instead of money, and therefore may never
# become negative.
ENERGY_VALUE_KEYS = (
    "virtual_import_without_battery",
    "virtual_export_without_battery",
    "virtual_import_without_solar",
)


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

    # Meter baselines. The battery and grid-import registers stay ``None`` when
    # the corresponding sensors are not configured.
    last_solar_energy: str | None = None
    last_export_energy: str | None = None
    last_grid_import_energy: str | None = None
    last_battery_charge_energy: str | None = None
    last_battery_discharge_energy: str | None = None

    # Energy observed but not yet attributed to the three scenarios. Deltas
    # wait here while a battery register is unreadable, so the scenario split
    # can still be exact once it reports again.
    unsplit_solar_energy: str = "0"
    unsplit_grid_import_energy: str = "0"
    unsplit_grid_export_energy: str = "0"
    unsplit_battery_charge_energy: str = "0"
    unsplit_battery_discharge_energy: str = "0"

    # Scenario energy awaiting valuation at the next settlement.
    pending_solar_energy: str = "0"
    pending_import_energy: str = "0"
    pending_export_energy: str = "0"
    pending_virtual_import_energy: str = "0"
    pending_virtual_export_energy: str = "0"

    # Virtual export that no solar reading covers yet.
    unallocated_export_energy: str = "0"

    # Cumulative public totals.
    self_consumption_savings: str = "0"
    export_revenue: str = "0"
    battery_savings: str = "0"
    actual_cost: str = "0"
    cost_without_battery: str = "0"
    cost_without_battery_and_solar: str = "0"
    virtual_import_without_battery: str = "0"
    virtual_export_without_battery: str = "0"
    virtual_import_without_solar: str = "0"


@dataclass(slots=True)
class SolarSavingsValues:
    """Public sensor values."""

    self_consumption_savings: Decimal
    export_revenue: Decimal
    solar_savings: Decimal
    battery_savings: Decimal
    total_savings: Decimal
    actual_cost: Decimal
    cost_without_battery: Decimal
    cost_without_battery_and_solar: Decimal
    virtual_import_without_battery: Decimal
    virtual_export_without_battery: Decimal
    virtual_import_without_solar: Decimal


@dataclass(slots=True, frozen=True)
class _PendingSplit:
    """Scenario energy accumulated since the previous settlement."""

    solar: Decimal
    actual_import: Decimal
    actual_export: Decimal
    virtual_import: Decimal
    virtual_export: Decimal
    self_consumed: Decimal
    carried_export: Decimal

    @property
    def has_energy(self) -> bool:
        """Report whether this settlement has anything to value."""
        return (
            self.solar > ZERO
            or self.actual_import > ZERO
            or self.actual_export > ZERO
            or self.virtual_import > ZERO
            or self.virtual_export > ZERO
        )


class SolarSavingsCalculator:
    """Account for solar and battery savings against counterfactual scenarios."""

    def __init__(
        self,
        snapshot: SolarSavingsSnapshot | None = None,
        *,
        track_battery: bool = False,
    ) -> None:
        """Initialize from an optional stored snapshot.

        ``track_battery`` reports whether the grid-import and battery registers
        are configured. Without them the absolute cost of a scenario is
        unknowable - the import register is what turns a grid position into
        money - so only the savings, which are differences in which the import
        register cancels out, are accumulated.
        """
        self._snapshot = snapshot or SolarSavingsSnapshot()
        self._track_battery = track_battery

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        track_battery: bool = False,
    ) -> SolarSavingsCalculator:
        """Create a calculator from storage data.

        Two fields from older snapshots no longer exist:

        ``pending_export_revenue`` was already valued at the tariff in force
        when it was earned, so it is folded into the cumulative export revenue
        instead of being dropped. ``pending_net_export_energy`` held export
        energy awaiting allocation, which is now tracked gross, so it carries
        over into ``pending_export_energy``.

        ``last_import_energy`` is simply ignored: it belonged to the net-export
        model and is not the grid-import register this model reads. Dropping it
        makes the register be seeded from its current reading instead of from a
        stale baseline of a possibly different entity.

        Snapshots that predate battery tracking carry no battery totals, so
        they start at zero. The solar savings they already accumulated are
        preserved because that total is derived from the self-consumption and
        export totals, which are unchanged.
        """
        if not data:
            return cls(track_battery=track_battery)
        allowed = SolarSavingsSnapshot.__dataclass_fields__.keys()
        snapshot_data = {key: data[key] for key in allowed if key in data}
        if "pending_export_energy" not in snapshot_data:
            legacy_pending = data.get("pending_net_export_energy")
            if legacy_pending is not None:
                snapshot_data["pending_export_energy"] = legacy_pending
                snapshot_data.setdefault(
                    "pending_virtual_export_energy", legacy_pending
                )
        calculator = cls(
            SolarSavingsSnapshot(**snapshot_data),
            track_battery=track_battery,
        )
        legacy_revenue = to_decimal(data.get("pending_export_revenue"))
        if legacy_revenue is not None and legacy_revenue != ZERO:
            calculator._add("export_revenue", legacy_revenue)
        return calculator

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot."""
        return asdict(self._snapshot)

    def _add(self, value_key: str, amount: Decimal) -> None:
        """Add an increment to a cumulative total."""
        setattr(
            self._snapshot,
            value_key,
            str(Decimal(getattr(self._snapshot, value_key)) + amount),
        )

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

        # The savings totals are derived from the totals below and should not
        # be restored directly, otherwise they could disagree with them.
        if value_key not in SETTABLE_VALUE_KEYS:
            return False

        if value_key in ENERGY_VALUE_KEYS and restored < ZERO:
            return False

        return self.set_public_value(value_key, restored)

    @property
    def values(self) -> SolarSavingsValues:
        """Return current public sensor values.

        ``solar_savings`` is the money the panels made, split for reporting
        into the imports they avoided and the export they earned.
        ``total_savings`` adds what the battery made on top, which is exactly
        the difference between the no-battery-no-solar scenario and what was
        actually paid.
        """
        snapshot = self._snapshot
        self_savings = Decimal(snapshot.self_consumption_savings)
        export_revenue = Decimal(snapshot.export_revenue)
        battery_savings = Decimal(snapshot.battery_savings)
        solar_savings = self_savings + export_revenue
        return SolarSavingsValues(
            self_consumption_savings=self_savings,
            export_revenue=export_revenue,
            solar_savings=solar_savings,
            battery_savings=battery_savings,
            total_savings=solar_savings + battery_savings,
            actual_cost=Decimal(snapshot.actual_cost),
            cost_without_battery=Decimal(snapshot.cost_without_battery),
            cost_without_battery_and_solar=Decimal(
                snapshot.cost_without_battery_and_solar
            ),
            virtual_import_without_battery=Decimal(
                snapshot.virtual_import_without_battery
            ),
            virtual_export_without_battery=Decimal(
                snapshot.virtual_export_without_battery
            ),
            virtual_import_without_solar=Decimal(snapshot.virtual_import_without_solar),
        )

    def seed(
        self,
        *,
        solar_energy: Decimal | None = None,
        export_energy: Decimal | None = None,
        grid_import_energy: Decimal | None = None,
        battery_charge_energy: Decimal | None = None,
        battery_discharge_energy: Decimal | None = None,
    ) -> None:
        """Set initial baselines without creating revenue.

        This prevents a restart or first setup from treating the meter's full
        historical value as newly generated energy. A register that gains a
        sensor later - a battery added to an existing entry - is seeded the
        first time it reports, for the same reason.
        """
        baselines = {
            "last_solar_energy": solar_energy,
            "last_export_energy": export_energy,
            "last_grid_import_energy": grid_import_energy,
            "last_battery_charge_energy": battery_charge_energy,
            "last_battery_discharge_energy": battery_discharge_energy,
        }
        for field_name, reading in baselines.items():
            if reading is not None and getattr(self._snapshot, field_name) is None:
                setattr(self._snapshot, field_name, str(reading))

    def observe(
        self,
        *,
        solar_energy: Decimal | None = None,
        export_energy: Decimal | None = None,
        grid_import_energy: Decimal | None = None,
        battery_charge_energy: Decimal | None = None,
        battery_discharge_energy: Decimal | None = None,
    ) -> bool:
        """Accumulate the positive deltas of every register that reported.

        Every valid reading updates its baseline so counter resets are handled
        promptly, and only positive deltas are accumulated. No tariff and no
        scenario is applied here: a register that reports later than its
        siblings must not shift energy between scenarios, so interpretation is
        deferred to :meth:`split_scenarios`.
        """
        registers = (
            ("last_solar_energy", "unsplit_solar_energy", solar_energy),
            ("last_export_energy", "unsplit_grid_export_energy", export_energy),
            (
                "last_grid_import_energy",
                "unsplit_grid_import_energy",
                grid_import_energy,
            ),
            (
                "last_battery_charge_energy",
                "unsplit_battery_charge_energy",
                battery_charge_energy,
            ),
            (
                "last_battery_discharge_energy",
                "unsplit_battery_discharge_energy",
                battery_discharge_energy,
            ),
        )

        changed = False
        for baseline_key, unsplit_key, reading in registers:
            if reading is None:
                continue
            previous = to_decimal(getattr(self._snapshot, baseline_key))
            delta = positive_delta(previous, reading)
            setattr(self._snapshot, baseline_key, str(reading))
            changed = True
            if delta > ZERO:
                self._add(unsplit_key, delta)
        return changed

    def split_scenarios(self) -> bool:
        """Attribute the observed energy to the three scenarios.

        The house load over the window is fixed by conservation of energy::

            load = solar + grid_import + battery_discharge
                   - grid_export - battery_charge

        Removing the battery leaves the load and the generation untouched, so
        the grid has to cover the battery's net contribution
        ``discharge - charge``. That correction is applied against the opposite
        direction of the actual flow first: energy the battery supplied would
        otherwise have reduced the export before it forced an import, and
        energy the battery absorbed would otherwise have reduced the import
        before it became export. Applying it that way keeps the actual gross
        split intact whenever the battery was idle, which is what makes the
        no-battery scenario collapse onto the actual one for a system without a
        battery.

        Removing the panels as well is the same correction with the generation,
        and is applied at settlement time because the solar register is usually
        the slowest one.
        """
        snapshot = self._snapshot
        solar = Decimal(snapshot.unsplit_solar_energy)
        grid_import = Decimal(snapshot.unsplit_grid_import_energy)
        grid_export = Decimal(snapshot.unsplit_grid_export_energy)
        charge = Decimal(snapshot.unsplit_battery_charge_energy)
        discharge = Decimal(snapshot.unsplit_battery_discharge_energy)

        observed = (solar, grid_import, grid_export, charge, discharge)
        if not any(value > ZERO for value in observed):
            return False

        battery_contribution = discharge - charge
        if battery_contribution >= ZERO:
            # Without the battery this energy had to come from the grid: it
            # first cancels export, then adds import.
            cancelled = min(battery_contribution, grid_export)
            virtual_export = grid_export - cancelled
            virtual_import = grid_import + (battery_contribution - cancelled)
        else:
            # Without the battery this energy was never taken in: it first
            # cancels import, then adds export.
            absorbed = -battery_contribution
            cancelled = min(absorbed, grid_import)
            virtual_import = grid_import - cancelled
            virtual_export = grid_export + (absorbed - cancelled)

        self._add("pending_solar_energy", solar)
        self._add("pending_import_energy", grid_import)
        self._add("pending_export_energy", grid_export)
        self._add("pending_virtual_import_energy", virtual_import)
        self._add("pending_virtual_export_energy", virtual_export)

        snapshot.unsplit_solar_energy = "0"
        snapshot.unsplit_grid_import_energy = "0"
        snapshot.unsplit_grid_export_energy = "0"
        snapshot.unsplit_battery_charge_energy = "0"
        snapshot.unsplit_battery_discharge_energy = "0"
        return True

    def _read_pending_split(self) -> _PendingSplit:
        """Return the scenario energy awaiting valuation.

        Assuming every exported kWh came from the panels, the export of the
        no-battery scenario is the part of the generation that did not serve
        the house. Export the solar register does not cover yet is carried over
        instead of being credited at the import tariff, so a settlement that
        falls between two solar readings gives the same answer as one that
        covers both.
        """
        snapshot = self._snapshot
        solar = Decimal(snapshot.pending_solar_energy)
        virtual_export = Decimal(snapshot.pending_virtual_export_energy)
        carried_export = Decimal(snapshot.unallocated_export_energy)

        available_export = virtual_export + carried_export
        exported_solar = min(solar, available_export)
        return _PendingSplit(
            solar=solar,
            actual_import=Decimal(snapshot.pending_import_energy),
            actual_export=Decimal(snapshot.pending_export_energy),
            virtual_import=Decimal(snapshot.pending_virtual_import_energy),
            virtual_export=virtual_export,
            self_consumed=solar - exported_solar,
            carried_export=available_export - exported_solar,
        )

    def settle_pending_accounting(
        self,
        *,
        import_price: Decimal | None,
        export_price: Decimal | None,
    ) -> bool:
        """Convert the pending scenario energy into costs and savings.

        This is the only place where energy is valued. The caller decides when
        a settlement happens: once per configured accounting interval, or
        immediately before a tariff change is applied, in which case it passes
        the tariffs that were in force *before* the change.

        Each scenario's grid position is valued with the same two tariffs::

            cost = import_energy * import_price - export_energy * export_price

        and the published figures are differences between scenarios: the
        battery saved what the no-battery scenario would have cost minus what
        was actually paid, and the panels saved what a house with neither would
        have cost minus the no-battery scenario. Both can be negative, because
        charging from the grid costs money and exporting at a negative tariff
        does too.

        A tariff that is needed but unknown - an unavailable price sensor, or a
        restart before the first price arrives - defers the whole settlement
        instead of dropping the energy: it stays pending and is valued by the
        next settlement that has a price.
        """
        split = self._read_pending_split()
        if not split.has_energy:
            return self._store_carried_export(split.carried_export)

        needs_import_price = (
            split.self_consumed > ZERO
            or split.actual_import > ZERO
            or split.virtual_import > ZERO
        )
        needs_export_price = split.actual_export > ZERO or split.virtual_export > ZERO
        if (needs_import_price and import_price is None) or (
            needs_export_price and export_price is None
        ):
            return False

        self._apply_settlement(
            split,
            ZERO if import_price is None else import_price,
            ZERO if export_price is None else export_price,
        )
        return True

    def _apply_settlement(
        self,
        split: _PendingSplit,
        import_price: Decimal,
        export_price: Decimal,
    ) -> None:
        """Value one settlement window and clear the pending energy."""
        # Solar savings, reported split into the imports the panels avoided and
        # the export they earned.
        self._add("self_consumption_savings", split.self_consumed * import_price)
        self._add("export_revenue", split.virtual_export * export_price)

        # Battery savings: the no-battery scenario minus what was actually
        # paid. The import register cancels out, so this stays exact even when
        # only the savings are tracked.
        self._add(
            "battery_savings",
            (split.virtual_import - split.actual_import) * import_price
            - (split.virtual_export - split.actual_export) * export_price,
        )

        if self._track_battery:
            import_without_solar = split.virtual_import + split.self_consumed
            actual = split.actual_import * import_price
            self._add("actual_cost", actual - split.actual_export * export_price)
            self._add(
                "cost_without_battery",
                split.virtual_import * import_price
                - split.virtual_export * export_price,
            )
            # Without the panels the house cannot export, because the export of
            # the no-battery scenario is exactly the generation it did not use.
            self._add(
                "cost_without_battery_and_solar",
                import_without_solar * import_price,
            )
            self._add("virtual_import_without_battery", split.virtual_import)
            self._add("virtual_export_without_battery", split.virtual_export)
            self._add("virtual_import_without_solar", import_without_solar)

        snapshot = self._snapshot
        snapshot.pending_solar_energy = "0"
        snapshot.pending_import_energy = "0"
        snapshot.pending_export_energy = "0"
        snapshot.pending_virtual_import_energy = "0"
        snapshot.pending_virtual_export_energy = "0"
        self._store_carried_export(split.carried_export)

    def _store_carried_export(self, carried_export: Decimal) -> bool:
        """Persist the export still waiting for a solar reading."""
        if carried_export == Decimal(self._snapshot.unallocated_export_energy):
            return False
        self._snapshot.unallocated_export_energy = str(carried_export)
        return True

    def observe_export_update(self, *, export_energy: Decimal | None) -> bool:
        """Track the smart meter's export register.

        Kept for callers that only follow the grid export, such as a system
        without a battery, where every reading can be attributed to a scenario
        as soon as it arrives.
        """
        changed = self.observe(export_energy=export_energy)
        return self.split_scenarios() or changed

    def observe_solar_update(self, *, solar_energy: Decimal | None) -> bool:
        """Track a solar generation reading without performing accounting."""
        changed = self.observe(solar_energy=solar_energy)
        return self.split_scenarios() or changed

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
        instead observes every reading and defers
        :meth:`settle_pending_accounting` to the accounting interval or to a
        tariff change.
        """
        observed = self.observe_solar_update(solar_energy=solar_energy)
        settled = self.settle_pending_accounting(
            import_price=import_price,
            export_price=export_price,
        )
        return observed or settled
