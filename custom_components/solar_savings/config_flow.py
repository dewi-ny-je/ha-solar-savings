"""Config flow for Solar Savings."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_SOLAR_ENERGY_SENSOR,
    DOMAIN,
)

DEFAULT_NAME = "Solar savings"


def _sensor_selector(device_class: str | None = None) -> selector.EntitySelector:
    """Build an entity selector restricted to sensors."""
    config: dict[str, Any] = {"domain": "sensor"}
    if device_class is not None:
        config["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**config))


def _energy_sensor_selector() -> selector.EntitySelector:
    """Select energy counter sensors."""
    return _sensor_selector("energy")


def _price_sensor_selector() -> selector.EntitySelector:
    """Select numeric price-per-kWh sensors.

    Do not filter by device_class: monetary because many price sensors expose
    currency/kWh units and no monetary device class.
    """
    return _sensor_selector()


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_SOLAR_ENERGY_SENSOR): _energy_sensor_selector(),
        vol.Required(CONF_IMPORT_ENERGY_SENSOR): _energy_sensor_selector(),
        vol.Required(CONF_IMPORT_PRICE_SENSOR): _price_sensor_selector(),
        vol.Required(CONF_EXPORT_ENERGY_SENSOR): _energy_sensor_selector(),
        vol.Required(CONF_EXPORT_PRICE_SENSOR): _price_sensor_selector(),
    }
)


def _entity_exists(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether an entity exists or is registered.

    Some integrations restore/register entities before their current state is
    available, especially after Home Assistant startup.
    """
    if hass.states.get(entity_id) is not None:
        return True

    registry = er.async_get(hass)
    if (entry := registry.async_get(entity_id)) is None:
        return False

    if entry.disabled_by is not None:
        return False

    if getattr(entry, "removed", False):
        return False

    return True


async def validate_input(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows setup.

    The selected entities may be template/utility-meter helpers, so validation is
    intentionally limited to presence and duplicate checks. Runtime unavailable
    states are handled gracefully by the calculator.
    """
    entities = [
        user_input[CONF_SOLAR_ENERGY_SENSOR],
        user_input[CONF_IMPORT_ENERGY_SENSOR],
        user_input[CONF_IMPORT_PRICE_SENSOR],
        user_input[CONF_EXPORT_ENERGY_SENSOR],
        user_input[CONF_EXPORT_PRICE_SENSOR],
    ]
    if len(entities) != len(set(entities)):
        return {"base": "duplicate_entity"}
    missing = [entity_id for entity_id in entities if not _entity_exists(hass, entity_id)]
    if missing:
        return {"base": "entity_not_found"}
    return {}


class SolarSavingsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solar Savings."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SolarSavingsOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await validate_input(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id(
                    "|".join(
                        [
                            user_input[CONF_SOLAR_ENERGY_SENSOR],
                            user_input[CONF_IMPORT_ENERGY_SENSOR],
                            user_input[CONF_EXPORT_ENERGY_SENSOR],
                        ]
                    )
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class SolarSavingsOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Solar Savings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage Solar Savings options."""
        errors: dict[str, str] = {}
        current_config = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            errors = await validate_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NAME,
                    default=current_config.get(CONF_NAME, DEFAULT_NAME),
                ): str,
                vol.Required(
                    CONF_SOLAR_ENERGY_SENSOR,
                    default=current_config[CONF_SOLAR_ENERGY_SENSOR],
                ): _energy_sensor_selector(),
                vol.Required(
                    CONF_IMPORT_ENERGY_SENSOR,
                    default=current_config[CONF_IMPORT_ENERGY_SENSOR],
                ): _energy_sensor_selector(),
                vol.Required(
                    CONF_IMPORT_PRICE_SENSOR,
                    default=current_config[CONF_IMPORT_PRICE_SENSOR],
                ): _price_sensor_selector(),
                vol.Required(
                    CONF_EXPORT_ENERGY_SENSOR,
                    default=current_config[CONF_EXPORT_ENERGY_SENSOR],
                ): _energy_sensor_selector(),
                vol.Required(
                    CONF_EXPORT_PRICE_SENSOR,
                    default=current_config[CONF_EXPORT_PRICE_SENSOR],
                ): _price_sensor_selector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )
