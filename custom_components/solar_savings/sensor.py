"""Sensor platform for Solar Savings."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    RestoreSensor,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolarSavingsRuntimeData
from .calculator import SETTABLE_VALUE_KEYS, to_finite_decimal
from .const import (
    ATTR_VALUE,
    DOMAIN,
    SERVICE_SET_VALUE,
    SIGNAL_UPDATED,
    STORAGE_SAVE_DELAY,
)


@dataclass(frozen=True, kw_only=True)
class SolarSavingsSensorEntityDescription(SensorEntityDescription):
    """Description for a Solar Savings sensor."""

    value_key: str


SENSOR_DESCRIPTIONS: tuple[SolarSavingsSensorEntityDescription, ...] = (
    SolarSavingsSensorEntityDescription(
        key="self_consumption_savings",
        translation_key="self_consumption_savings",
        value_key="self_consumption_savings",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    SolarSavingsSensorEntityDescription(
        key="export_revenue",
        translation_key="export_revenue",
        value_key="export_revenue",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    SolarSavingsSensorEntityDescription(
        key="total_savings",
        translation_key="total_savings",
        value_key="total_savings",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solar Savings sensors."""
    async_add_entities(
        SolarSavingsSensor(hass, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_VALUE,
        # Accept the raw number/string and build the Decimal in the handler so
        # exact precision is preserved and the payload stays JSON-serializable.
        {vol.Required(ATTR_VALUE): vol.Any(int, float, str)},
        "async_set_value",
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
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Solar Savings",
        }
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
        correct accumulated drift. The derived total savings sensor is rejected
        because it is recomputed from the two source totals.
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

        data: SolarSavingsRuntimeData = self.entry.runtime_data
        if data.calculator.set_public_value(value_key, decimal_value):
            data.store.async_delay_save(data.calculator.as_dict, STORAGE_SAVE_DELAY)
            async_dispatcher_send(
                self.hass, f"{SIGNAL_UPDATED}_{self.entry.entry_id}"
            )
