"""The Solar Savings integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from .calculator import SolarSavingsCalculator, to_decimal
from .const import (
    CONF_ACCOUNTING_INTERVAL,
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_MIN_ACCOUNTING_INTERVAL,
    CONF_SOLAR_ENERGY_SENSOR,
    DEFAULT_ACCOUNTING_INTERVAL,
    SIGNAL_UPDATED,
    STORAGE_KEY,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

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


@dataclass(slots=True)
class SolarSavingsRuntimeData:
    """Runtime data for a Solar Savings config entry."""

    calculator: SolarSavingsCalculator
    store: Store[dict[str, Any]]
    accounting_interval: float
    remove_listeners: list[Callable[[], None]] = field(default_factory=list)
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
    calculator = SolarSavingsCalculator.from_dict(await store.async_load())
    config = {**entry.data, **entry.options}
    accounting_interval = resolve_accounting_interval(config)

    solar_state = hass.states.get(config[CONF_SOLAR_ENERGY_SENSOR])
    import_state = hass.states.get(config[CONF_IMPORT_ENERGY_SENSOR])
    export_state = hass.states.get(config[CONF_EXPORT_ENERGY_SENSOR])
    calculator.seed(
        solar_energy=energy_to_kwh(solar_state),
        import_energy=energy_to_kwh(import_state),
        export_energy=energy_to_kwh(export_state),
    )

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
    def async_settle_pending_accounting() -> None:
        """Value the energy accumulated since the previous settlement.

        The last known tariffs are used, so a settlement triggered by a tariff
        change values the accumulated energy at the tariffs that were in force
        before the change. This is also the only point where sensor states and
        the stored snapshot are updated: between settlements the integration
        merely accumulates energy in memory.
        """
        async_cancel_scheduled_accounting()

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

    data.settle = async_settle_pending_accounting

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
                lambda _now: async_settle_pending_accounting(),
            )

    @callback
    def handle_grid_event(_event: Event) -> None:
        import_state = hass.states.get(config[CONF_IMPORT_ENERGY_SENSOR])
        export_state = hass.states.get(config[CONF_EXPORT_ENERGY_SENSOR])
        if calculator.handle_grid_update(
            import_energy=energy_to_kwh(import_state),
            export_energy=energy_to_kwh(export_state),
        ):
            async_schedule_accounting()

    @callback
    def handle_solar_event(_event: Event) -> None:
        solar_state = hass.states.get(config[CONF_SOLAR_ENERGY_SENSOR])
        if calculator.observe_solar_update(
            solar_energy=energy_to_kwh(solar_state),
        ):
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

        async_settle_pending_accounting()
        data.import_price = new_import_price
        data.export_price = new_export_price

    data.remove_listeners.extend(
        [
            async_track_state_change_event(
                hass,
                [
                    config[CONF_IMPORT_ENERGY_SENSOR],
                    config[CONF_EXPORT_ENERGY_SENSOR],
                ],
                handle_grid_event,
            ),
            async_track_state_change_event(
                hass,
                [config[CONF_SOLAR_ENERGY_SENSOR]],
                handle_solar_event,
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
