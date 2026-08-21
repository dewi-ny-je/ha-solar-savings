"""Sensor platform for Solar Savings."""

from __future__ import annotations

from dataclasses import dataclass, replace

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    RestoreSensor,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolarSavingsRuntimeData, battery_is_tracked, entry_config
from .calculator import ENERGY_VALUE_KEYS, SETTABLE_VALUE_KEYS, ZERO, to_finite_decimal
from .const import (
    ATTR_VALUE,
    DOMAIN,
    SERVICE_SET_VALUE,
    SIGNAL_UPDATED,
    STORAGE_SAVE_DELAY,
    SolarSavingsEntityFeature,
)


@dataclass(frozen=True, kw_only=True)
class SolarSavingsSensorEntityDescription(SensorEntityDescription):
    """Description for a Solar Savings sensor."""

    value_key: str
    # Scenario costs and virtual meters only mean something once the grid
    # import and the battery registers are known, so they are not created at
    # all without them.
    requires_battery: bool = False
    # The solar split is the whole story for a house without a battery, and
    # only a detail of it once a battery time-shifts the energy, so with a
    # battery it is created but left switched off.
    demoted_with_battery: bool = False


def _money(
    key: str,
    *,
    requires_battery: bool = False,
    demoted_with_battery: bool = False,
    enabled: bool = True,
) -> SolarSavingsSensorEntityDescription:
    """Describe a cumulative monetary sensor."""
    return SolarSavingsSensorEntityDescription(
        key=key,
        translation_key=key,
        value_key=key,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        requires_battery=requires_battery,
        demoted_with_battery=demoted_with_battery,
        entity_registry_enabled_default=enabled,
    )


def _energy(key: str) -> SolarSavingsSensorEntityDescription:
    """Describe a cumulative virtual energy meter."""
    return SolarSavingsSensorEntityDescription(
        key=key,
        translation_key=key,
        value_key=key,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        requires_battery=True,
        entity_registry_enabled_default=False,
    )


SENSOR_DESCRIPTIONS: tuple[SolarSavingsSensorEntityDescription, ...] = (
    _money("self_consumption_savings", demoted_with_battery=True),
    _money("export_revenue", demoted_with_battery=True),
    _money("solar_savings"),
    _money("battery_savings"),
    _money("total_savings"),
    _money("actual_cost", requires_battery=True, enabled=False),
    _money("cost_without_battery", requires_battery=True, enabled=False),
    _money("cost_without_battery_and_solar", requires_battery=True, enabled=False),
    _energy("virtual_import_without_battery"),
    _energy("virtual_export_without_battery"),
    _energy("virtual_import_without_solar"),
)


def descriptions_for(
    *,
    track_battery: bool,
) -> list[SolarSavingsSensorEntityDescription]:
    """Return the sensors an entry publishes, and how they start out."""
    descriptions = []
    for description in SENSOR_DESCRIPTIONS:
        if description.requires_battery and not track_battery:
            continue
        if description.demoted_with_battery and track_battery:
            descriptions.append(
                replace(description, entity_registry_enabled_default=False)
            )
        else:
            descriptions.append(description)
    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solar Savings sensors."""
    track_battery = battery_is_tracked(entry_config(entry))
    async_add_entities(
        SolarSavingsSensor(hass, entry, description)
        for description in descriptions_for(track_battery=track_battery)
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_VALUE,
        # Accept the raw number/string and build the Decimal in the handler so
        # exact precision is preserved and the payload stays JSON-serializable.
        {vol.Required(ATTR_VALUE): vol.Any(int, float, str)},
        "async_set_value",
        # Restrict the action to the settable source sensors. Home Assistant
        # applies this filter before invoking any handler, so targeting a
        # device or area skips the derived total sensor and an explicit target
        # of it is rejected up front, never mutating siblings on a failed call.
        required_features=[SolarSavingsEntityFeature.SET_VALUE],
    )


class SolarSavingsSensor(RestoreSensor, SensorEntity):
    """Sensor exposing cumulative solar savings."""

    entity_description: SolarSavingsSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: SolarSavingsSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self.entry = entry
        self.entity_description = description
        if description.value_key in SETTABLE_VALUE_KEYS:
            self._attr_supported_features = SolarSavingsEntityFeature.SET_VALUE
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Solar Savings",
        }
        if description.native_unit_of_measurement is None:
            self._attr_native_unit_of_measurement = hass.config.currency

    @property
    def native_value(self) -> float:
        """Return the current cumulative monetary value."""
        data: SolarSavingsRuntimeData = self.entry.runtime_data
        value = getattr(data.calculator.values, self.entity_description.value_key)
        return float(value)

    async def async_added_to_hass(self) -> None:
        """Restore the last state and subscribe to integration updates."""
        await super().async_added_to_hass()

        if (last_sensor_data := await self.async_get_last_sensor_data()) is not None:
            data: SolarSavingsRuntimeData = self.entry.runtime_data
            if data.calculator.restore_public_value(
                self.entity_description.value_key,
                last_sensor_data.native_value,
            ):
                data.store.async_delay_save(data.calculator.as_dict, STORAGE_SAVE_DELAY)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATED}_{self.entry.entry_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Write updated value to Home Assistant."""
        self.async_write_ha_state()

    async def async_set_value(self, value: float | str) -> None:
        """Overwrite this sensor's stored cumulative total.

        Exposed as the ``solar_savings.set_value`` action so users can
        initialise the integration with totals from a previous system or
        correct accumulated drift. The ``required_features`` filter on the
        service registration keeps the derived savings sensors out of the
        target before any handler runs; the guard below is defence-in-depth for
        direct calls and never mutates state when it rejects.

        Monetary totals accept negative values, because negative tariffs make
        them legitimately negative. The virtual energy meters do not: they
        count kWh.
        """
        value_key = self.entity_description.value_key
        if value_key not in SETTABLE_VALUE_KEYS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="cannot_set_derived_value",
                translation_placeholders={"entity_id": self.entity_id},
            )

        decimal_value = to_finite_decimal(value)
        if decimal_value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={"value": str(value)},
            )

        if value_key in ENERGY_VALUE_KEYS and decimal_value < ZERO:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="negative_energy_value",
                translation_placeholders={"entity_id": self.entity_id},
            )

        data: SolarSavingsRuntimeData = self.entry.runtime_data
        if data.calculator.set_public_value(value_key, decimal_value):
            data.store.async_delay_save(data.calculator.as_dict, STORAGE_SAVE_DELAY)
            async_dispatcher_send(self.hass, f"{SIGNAL_UPDATED}_{self.entry.entry_id}")
