"""The Solar Savings integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from .calculator import SolarSavingsCalculator, to_decimal
from .const import (
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_SOLAR_ENERGY_SENSOR,
    SIGNAL_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
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


@dataclass(slots=True)
class SolarSavingsRuntimeData:
    """Runtime data for a Solar Savings config entry."""

    calculator: SolarSavingsCalculator
    store: Store[dict[str, Any]]
    remove_listeners: list[Callable[[], None]]


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
        _LOGGER.warning(
            "Ignoring energy sensor %s because its unit %r is not supported; "
            "expected Wh, kWh, or MWh",
            state.entity_id,
            unit,
        )
        return None

    return value * factor


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar Savings from a config entry."""
    from homeassistant.const import Platform
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.helpers.event import async_track_state_change_event
    from homeassistant.helpers.storage import Store

    platforms = [Platform.SENSOR]
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
    )
    calculator = SolarSavingsCalculator.from_dict(await store.async_load())
    config = {**entry.data, **entry.options}

    solar_state = hass.states.get(config[CONF_SOLAR_ENERGY_SENSOR])
    import_state = hass.states.get(config[CONF_IMPORT_ENERGY_SENSOR])
    export_state = hass.states.get(config[CONF_EXPORT_ENERGY_SENSOR])
    calculator.seed(
        solar_energy=energy_to_kwh(solar_state),
        import_energy=energy_to_kwh(import_state),
        export_energy=energy_to_kwh(export_state),
    )

    data = SolarSavingsRuntimeData(calculator, store, [])
    entry.runtime_data = data

    async def async_save_and_update() -> None:
        await store.async_save(calculator.as_dict())
        async_dispatcher_send(hass, f"{SIGNAL_UPDATED}_{entry.entry_id}")

    def handle_grid_event(event: Event) -> None:
        import_state = hass.states.get(config[CONF_IMPORT_ENERGY_SENSOR])
        export_state = hass.states.get(config[CONF_EXPORT_ENERGY_SENSOR])
        price_state = hass.states.get(config[CONF_EXPORT_PRICE_SENSOR])
        changed = calculator.handle_grid_update(
            import_energy=energy_to_kwh(import_state),
            export_energy=energy_to_kwh(export_state),
            export_price=to_decimal(price_state.state if price_state else None),
        )
        if changed:
            hass.create_task(async_save_and_update())

    def handle_solar_event(event: Event) -> None:
        solar_state = hass.states.get(config[CONF_SOLAR_ENERGY_SENSOR])
        price_state = hass.states.get(config[CONF_IMPORT_PRICE_SENSOR])
        changed = calculator.handle_solar_update(
            solar_energy=energy_to_kwh(solar_state),
            import_price=to_decimal(price_state.state if price_state else None),
        )
        if changed:
            hass.create_task(async_save_and_update())

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
        ]
    )

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await store.async_save(calculator.as_dict())
    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload Solar Savings when options are changed."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from homeassistant.const import Platform

    unload_ok = await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
    if unload_ok:
        data: SolarSavingsRuntimeData = entry.runtime_data
        for remove_listener in data.remove_listeners:
            remove_listener()
    return unload_ok
