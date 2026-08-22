"""The Solar Savings integration."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from .calculator import SolarSavingsCalculator, to_decimal
from .const import (
    BATTERY_CONF_KEYS,
    BATTERY_MIN_ACCOUNTING_INTERVAL,
    BATTERY_STALE_TIMEOUT,
    CONF_ACCOUNTING_INTERVAL,
    CONF_BATTERY_CHARGE_ENERGY_SENSOR,
    CONF_BATTERY_DISCHARGE_ENERGY_SENSOR,
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_GRID_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_MIN_ACCOUNTING_INTERVAL,
    CONFIG_ENTRY_VERSION,
    CONF_SOLAR_ENERGY_SENSOR,
    DEFAULT_ACCOUNTING_INTERVAL,
    SECTION_BATTERY,
    SIGNAL_UPDATED,
    STORAGE_KEY,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.storage import Store


PLATFORMS = ["sensor"]
_LOGGER = logging.getLogger(__name__)
_ENERGY_TO_KWH = {
    "Wh": Decimal("0.001"),
    "kWh": Decimal("1"),
    "MWh": Decimal("1000"),
}
_WARNED_UNITS_BY_ENTITY: dict[str, Any] = {}

# Maps the calculator's register names onto the configuration keys that select
# the sensor feeding them. The battery registers are only present when battery
# tracking is configured.
_ENERGY_REGISTERS: dict[str, str] = {
    "solar_energy": CONF_SOLAR_ENERGY_SENSOR,
    "export_energy": CONF_EXPORT_ENERGY_SENSOR,
    "grid_import_energy": CONF_GRID_IMPORT_ENERGY_SENSOR,
    "battery_charge_energy": CONF_BATTERY_CHARGE_ENERGY_SENSOR,
    "battery_discharge_energy": CONF_BATTERY_DISCHARGE_ENERGY_SENSOR,
}
_BATTERY_REGISTERS = ("battery_charge_energy", "battery_discharge_energy")


@dataclass(slots=True)
class SolarSavingsRuntimeData:
    """Runtime data for a Solar Savings config entry."""

    calculator: SolarSavingsCalculator
    store: Store[dict[str, Any]]
    accounting_interval: float
    remove_listeners: list[Callable[[], None]] = field(default_factory=list)
    # When the open attribution window started holding energy. A window that
    # has only just opened cannot contain a reading from every meter yet.
    window_opened_at: float | None = None
    # When a battery register stopped reporting, the moment the wait began, and
    # whether the wait has already been given up on.
    battery_hold_since: float | None = None
    battery_is_stale: bool = False
    # Last known good tariffs. Settlements are always valued with these, which
    # is what lets a tariff change be settled with the price that was in force
    # before it.
    import_price: Decimal | None = None
    export_price: Decimal | None = None
    cancel_scheduled_accounting: Callable[[], None] | None = None
    settle: Callable[[], None] | None = None


def resolve_accounting_interval(config: Mapping[str, Any]) -> float:
    """Return the configured accounting interval in seconds.

    Config entries created before the option was renamed still carry the old
    ``minimum_accounting_interval`` key, and entries created before the option
    existed carry neither. Both fall back to the default.
    """
    raw = config.get(
        CONF_ACCOUNTING_INTERVAL,
        config.get(CONF_MIN_ACCOUNTING_INTERVAL, DEFAULT_ACCOUNTING_INTERVAL),
    )
    try:
        interval = float(raw)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Ignoring invalid accounting interval %r; using %s seconds",
            raw,
            DEFAULT_ACCOUNTING_INTERVAL,
        )
        return float(DEFAULT_ACCOUNTING_INTERVAL)
    return max(interval, 0.0)


def settlement_interval_for(config: Mapping[str, Any]) -> float:
    """Return the window over which energy is attributed and then valued.

    The battery scenarios are built by cancelling the battery's contribution
    against the grid flow of the same window, so the window has to be long
    enough to hold a reading from every register. A battery entry is therefore
    floored at :data:`BATTERY_MIN_ACCOUNTING_INTERVAL`, including the ``0``
    that otherwise means "value every reading".
    """
    interval = resolve_accounting_interval(config)
    if not battery_is_tracked(config):
        return interval
    return max(interval, BATTERY_MIN_ACCOUNTING_INTERVAL)


def flatten_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the configuration with the battery section merged into the top.

    The config flow groups the optional battery sensors in a collapsible
    section, which Home Assistant stores as a nested mapping. Everything else
    reads a flat configuration, so the nesting is undone once, here. Blank
    selections are dropped so ``in`` is enough to test whether a sensor was
    chosen.
    """
    flat = {key: value for key, value in config.items() if key != SECTION_BATTERY}
    section: Mapping[str, Any] = config.get(SECTION_BATTERY) or {}
    for key in BATTERY_CONF_KEYS:
        value = section.get(key, flat.get(key))
        if value:
            flat[key] = value
        else:
            flat.pop(key, None)
    return flat


def battery_is_tracked(config: Mapping[str, Any]) -> bool:
    """Report whether the entry selects all three battery-tracking sensors."""
    return all(config.get(key) for key in BATTERY_CONF_KEYS)


def entry_config(entry: ConfigEntry) -> dict[str, Any]:
    """Return a config entry's effective, flattened configuration."""
    return flatten_config({**entry.data, **entry.options})


def unique_id_for(config: Mapping[str, Any]) -> str:
    """Build the config entry unique ID from the selected energy sensors."""
    return "|".join(
        [config[CONF_SOLAR_ENERGY_SENSOR], config[CONF_EXPORT_ENERGY_SENSOR]]
    )


def energy_to_kwh(state: Any | None) -> Decimal | None:
    """Convert an energy sensor state to kWh."""
    if state is None:
        return None

    value = to_decimal(state.state)
    if value is None:
        return None

    unit = state.attributes.get("unit_of_measurement")
    factor = _ENERGY_TO_KWH.get(unit)
    if factor is None:
        previous_unit = _WARNED_UNITS_BY_ENTITY.get(state.entity_id)
        if previous_unit != unit:
            _LOGGER.warning(
                "Ignoring energy sensor %s because its unit %r is not supported; "
                "expected Wh, kWh, or MWh",
                state.entity_id,
                unit,
            )
            _WARNED_UNITS_BY_ENTITY[state.entity_id] = unit
        return None

    _WARNED_UNITS_BY_ENTITY.pop(state.entity_id, None)
    return value * factor


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a config entry to the current version.

    Version 1 entries select an imported-energy sensor. Self-consumption is now
    ``solar - export``, which never reads the import register, so the selection
    is dropped and the unique ID is rebuilt from the two sensors that remain.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        # Downgrades are not supported; leave the entry alone.
        return False

    if entry.version == 1:
        data = {
            key: value
            for key, value in entry.data.items()
            if key != CONF_IMPORT_ENERGY_SENSOR
        }
        options = {
            key: value
            for key, value in entry.options.items()
            if key != CONF_IMPORT_ENERGY_SENSOR
        }
        config = {**data, **options}
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            unique_id=unique_id_for(config),
            version=CONFIG_ENTRY_VERSION,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar Savings from a config entry."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.helpers.event import (
        async_call_later,
        async_track_state_change_event,
    )
    from homeassistant.helpers.storage import Store

    platforms = [Platform.SENSOR]
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
    )
    config = entry_config(entry)
    track_battery = battery_is_tracked(config)
    calculator = SolarSavingsCalculator.from_dict(
        await store.async_load(),
        track_battery=track_battery,
    )
    accounting_interval = settlement_interval_for(config)
    if accounting_interval != resolve_accounting_interval(config):
        _LOGGER.info(
            "Settling Solar Savings for %s every %s seconds instead of the "
            "configured interval: battery energy can only be attributed over a "
            "window that holds a reading from every meter",
            entry.title,
            accounting_interval,
        )

    energy_sensors = {
        register: config[conf_key]
        for register, conf_key in _ENERGY_REGISTERS.items()
        if config.get(conf_key)
    }

    def read_energy_registers() -> dict[str, Decimal | None]:
        """Read every configured energy register, in kWh."""
        return {
            register: energy_to_kwh(hass.states.get(entity_id))
            for register, entity_id in energy_sensors.items()
        }

    calculator.seed(**read_energy_registers())

    def current_price(entity_id: str) -> Decimal | None:
        """Read a tariff sensor, or None when it has no usable value."""
        price_state = hass.states.get(entity_id)
        return to_decimal(price_state.state if price_state else None)

    data = SolarSavingsRuntimeData(
        calculator,
        store,
        accounting_interval,
        import_price=current_price(config[CONF_IMPORT_PRICE_SENSOR]),
        export_price=current_price(config[CONF_EXPORT_PRICE_SENSOR]),
    )
    entry.runtime_data = data

    @callback
    def async_schedule_save_and_update() -> None:
        store.async_delay_save(calculator.as_dict, STORAGE_SAVE_DELAY)
        async_dispatcher_send(hass, f"{SIGNAL_UPDATED}_{entry.entry_id}")

    @callback
    def async_cancel_scheduled_accounting() -> None:
        """Drop a pending settlement timer, if any."""
        cancel_scheduled_accounting = data.cancel_scheduled_accounting
        data.cancel_scheduled_accounting = None
        if cancel_scheduled_accounting is not None:
            cancel_scheduled_accounting()

    @callback
    def async_settle_pending_accounting(*, close_window: bool = True) -> None:
        """Attribute and value the energy observed since the previous settlement.

        The last known tariffs are used, so a settlement triggered by a tariff
        change values the accumulated energy at the tariffs that were in force
        before the change. This is also the only point where sensor states and
        the stored snapshot are updated: between settlements the integration
        merely accumulates energy in memory.

        A tariff change settles in the middle of an attribution window, so it
        passes ``close_window=False``: a window too young to hold a reading
        from every meter keeps its energy instead, and carries it into the new
        tariff period. Valuing at most one window of energy at the incoming
        tariff costs far less than attributing a grid delta whose battery delta
        has not arrived yet, which is the error this window exists to prevent.
        """
        async_cancel_scheduled_accounting()
        async_split_observed_energy(require_aged_window=not close_window)

        changed = calculator.settle_pending_accounting(
            import_price=data.import_price,
            export_price=data.export_price,
        )
        if changed:
            async_schedule_save_and_update()
        elif data.import_price is None or data.export_price is None:
            _LOGGER.debug(
                "Deferring Solar Savings accounting for %s: "
                "import price is %s and export price is %s",
                entry.title,
                data.import_price,
                data.export_price,
            )

        # Energy left waiting for its window to close has to be retried without
        # depending on another meter reading, otherwise a quiet meter would
        # strand it and the bounded waits below would never be re-checked.
        if calculator.has_unattributed_energy and data.accounting_interval > 0:
            async_schedule_accounting()

    data.settle = async_settle_pending_accounting

    @callback
    def async_settle_scheduled_accounting(_now: datetime) -> None:
        """Run the interval settlement from the event loop.

        ``async_call_later`` inspects the callable it is given: anything that
        is neither a coroutine function nor decorated with ``@callback`` is
        treated as blocking and run in an executor thread, where the dispatcher
        and the store are not safe to touch. Keeping the decorator on this
        target is what pins the settlement to the event loop.
        """
        async_settle_pending_accounting()

    @callback
    def async_schedule_accounting() -> None:
        """Make sure accumulated energy is settled within the interval.

        With a positive interval the first energy reading after a settlement
        arms a single timer, so accounting runs at most once per interval and
        idles completely while no energy is flowing. An interval of ``0``
        settles on every reading, which restores the previous behaviour.
        """
        if data.accounting_interval <= 0:
            async_settle_pending_accounting()
            return

        if data.cancel_scheduled_accounting is None:
            data.cancel_scheduled_accounting = async_call_later(
                hass,
                data.accounting_interval,
                async_settle_scheduled_accounting,
            )

    @callback
    def async_battery_registers_are_readable() -> bool:
        """Decide whether the observed energy can be attributed yet.

        A battery register that is momentarily unavailable is worth waiting
        for: its delta is preserved, so a split that runs once it reports again
        is exact, while splitting without it would credit the battery's energy
        to the grid. The wait is bounded, because a sensor that was removed or
        renamed would otherwise stall accounting for good.
        """
        readings = read_energy_registers()
        if all(readings.get(register) is not None for register in _BATTERY_REGISTERS):
            if data.battery_is_stale:
                _LOGGER.info(
                    "Battery energy sensors for %s are reporting again; "
                    "resuming exact battery accounting",
                    entry.title,
                )
            data.battery_hold_since = None
            data.battery_is_stale = False
            return True

        if data.battery_is_stale:
            return True

        now = time.monotonic()
        if data.battery_hold_since is None:
            data.battery_hold_since = now
            return False
        if now - data.battery_hold_since < BATTERY_STALE_TIMEOUT:
            return False

        data.battery_is_stale = True
        _LOGGER.warning(
            "Battery energy sensors for %s have not reported a usable value for "
            "%s seconds; accounting for the energy meanwhile as if the battery "
            "were idle",
            entry.title,
            BATTERY_STALE_TIMEOUT,
        )
        return True

    @callback
    def async_window_holds_every_register() -> bool:
        """Report whether the open window is old enough to be attributed.

        The meters report at their own pace, so a window only holds a reading
        from each of them once it has been open for at least as long as the
        slowest of them. The minimum settlement interval is that bound, which
        is why a battery entry is floored at it.
        """
        opened_at = data.window_opened_at
        if opened_at is None:
            return True
        return time.monotonic() - opened_at >= BATTERY_MIN_ACCOUNTING_INTERVAL

    @callback
    def async_split_observed_energy(*, require_aged_window: bool = False) -> bool:
        """Attribute the energy of one settlement window to the scenarios.

        Attribution happens once per window rather than once per reading. The
        scenarios cancel the battery's contribution against the grid flow of
        the same window, and the registers do not report together: a smart
        meter updating every few seconds against a battery updating every
        minute would leave almost every window holding one side of the
        cancellation and a zero for the other, crediting grid export that the
        battery produced to the panels and the battery's discharge to an import
        it never avoided. Valuing the energy over the same window it was
        attributed in keeps the two in step.

        ``require_aged_window`` refuses to close a window that is still too
        young to hold a reading from every meter, which is what a settlement
        forced by a tariff change would otherwise do.
        """
        if not calculator.has_unattributed_energy:
            return False
        if track_battery:
            if require_aged_window and not async_window_holds_every_register():
                return False
            if not async_battery_registers_are_readable():
                return False
        if not calculator.split_scenarios():
            return False
        data.window_opened_at = None
        return True

    @callback
    def handle_energy_event(_event: Event) -> None:
        """Record every energy register without attributing it yet.

        All registers are re-read on any of their updates, so a reading is
        never attributed to a scenario before its siblings had the chance to
        report the same window.
        """
        if calculator.observe(**read_energy_registers()):
            if data.window_opened_at is None and calculator.has_unattributed_energy:
                data.window_opened_at = time.monotonic()
            async_schedule_accounting()

    @callback
    def handle_price_event(_event: Event) -> None:
        """Settle at the outgoing tariff before adopting a new one.

        A tariff that becomes unknown or unavailable is not a tariff change:
        the last known value is kept so energy accumulated meanwhile is still
        valued, and settlement waits for the next real price.
        """
        import_price = current_price(config[CONF_IMPORT_PRICE_SENSOR])
        export_price = current_price(config[CONF_EXPORT_PRICE_SENSOR])

        new_import_price = data.import_price if import_price is None else import_price
        new_export_price = data.export_price if export_price is None else export_price
        if (new_import_price, new_export_price) == (
            data.import_price,
            data.export_price,
        ):
            return

        async_settle_pending_accounting(close_window=False)
        data.import_price = new_import_price
        data.export_price = new_export_price

    data.remove_listeners.extend(
        [
            async_track_state_change_event(
                hass,
                list(energy_sensors.values()),
                handle_energy_event,
            ),
            async_track_state_change_event(
                hass,
                [
                    config[CONF_IMPORT_PRICE_SENSOR],
                    config[CONF_EXPORT_PRICE_SENSOR],
                ],
                handle_price_event,
            ),
        ]
    )

    async def async_handle_stop(_event: Event) -> None:
        """Settle and persist before Home Assistant shuts down."""
        async_settle_pending_accounting()
        await store.async_save(calculator.as_dict())

    data.remove_listeners.append(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_handle_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    store.async_delay_save(calculator.as_dict, STORAGE_SAVE_DELAY)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from homeassistant.const import Platform

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        [Platform.SENSOR],
    )
    if unload_ok:
        data: SolarSavingsRuntimeData = entry.runtime_data
        for remove_listener in data.remove_listeners:
            remove_listener()
        data.remove_listeners.clear()
        if data.cancel_scheduled_accounting is not None:
            data.cancel_scheduled_accounting()
            data.cancel_scheduled_accounting = None
        # Energy accumulated since the last settlement only lives in memory, so
        # settle and persist it before the runtime data goes away.
        if data.settle is not None:
            data.settle()
        await data.store.async_save(data.calculator.as_dict())
    return unload_ok
