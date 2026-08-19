"""Tests for the config entry setup wiring.

Home Assistant is not a test dependency, so the handful of helpers
``async_setup_entry`` imports are stubbed here. The stubs mirror the parts of
Home Assistant's behaviour the integration depends on - in particular that
``@callback`` marks a function as safe to run on the event loop, which is what
decides whether a scheduled job runs on the loop or in an executor thread.
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from custom_components.solar_savings import async_setup_entry
from custom_components.solar_savings.const import (
    CONF_ACCOUNTING_INTERVAL,
    CONF_EXPORT_ENERGY_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_SOLAR_ENERGY_SENSOR,
    SIGNAL_UPDATED,
)

if TYPE_CHECKING:
    from collections.abc import Callable

SOLAR_SENSOR = "sensor.solar_energy"
EXPORT_SENSOR = "sensor.export_energy"
IMPORT_PRICE_SENSOR = "sensor.import_price"
EXPORT_PRICE_SENSOR = "sensor.export_price"


def hass_callback(func: Any) -> Any:
    """Stand in for ``homeassistant.core.callback``."""
    func._hass_callback = True  # noqa: SLF001
    return func


def is_hass_callback(func: Any) -> bool:
    """Report whether Home Assistant would run ``func`` on the event loop.

    ``HassJob`` runs a target in an executor thread unless it is a coroutine
    function or carries this marker.
    """
    return getattr(func, "_hass_callback", False) is True


@dataclass
class FakeState:
    """Minimal stand-in for a Home Assistant state object."""

    entity_id: str
    state: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeConfigEntry:
    """Minimal stand-in for a config entry."""

    data: dict[str, Any]
    options: dict[str, Any] = field(default_factory=dict)
    entry_id: str = "entry"
    title: str = "Solar Savings"
    runtime_data: Any = None


class FakeStore:
    """Record what the integration persists."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        """Start with nothing persisted."""
        self.delayed_saves: list[Callable[[], dict[str, Any]]] = []
        self.saves: list[dict[str, Any]] = []

    async def async_load(self) -> dict[str, Any] | None:
        """Report an empty store."""
        return None

    def async_delay_save(
        self,
        data_func: Callable[[], dict[str, Any]],
        _delay: float = 0,
    ) -> None:
        """Record a debounced save."""
        self.delayed_saves.append(data_func)

    async def async_save(self, data: dict[str, Any]) -> None:
        """Record an immediate save."""
        self.saves.append(data)


class FakeBus:
    """Collect one-shot event listeners."""

    def __init__(self) -> None:
        """Start with no listeners."""
        self.listeners: dict[str, Any] = {}

    def async_listen_once(self, event: str, listener: Any) -> Callable[[], None]:
        """Register a one-shot listener."""
        self.listeners[event] = listener
        return lambda: None


class FakeConfigEntries:
    """Record platform forwarding."""

    def __init__(self) -> None:
        """Start with nothing forwarded."""
        self.forwarded: list[Any] = []

    async def async_forward_entry_setups(
        self,
        entry: Any,
        platforms: list[Any],
    ) -> None:
        """Record a platform setup."""
        self.forwarded.append((entry, platforms))


class FakeHass:
    """Minimal stand-in for the Home Assistant core object."""

    def __init__(self) -> None:
        """Build an empty core object."""
        self.states = FakeStates()
        self.bus = FakeBus()
        self.config_entries = FakeConfigEntries()


class FakeStates:
    """A tiny state machine."""

    def __init__(self) -> None:
        """Start with no states."""
        self._states: dict[str, FakeState] = {}

    def get(self, entity_id: str) -> FakeState | None:
        """Return a state, or ``None`` when the entity is unknown."""
        return self._states.get(entity_id)

    def set(self, entity_id: str, state: str, unit: str | None = None) -> None:
        """Write a state, optionally carrying a unit of measurement."""
        attributes = {} if unit is None else {"unit_of_measurement": unit}
        self._states[entity_id] = FakeState(entity_id, state, attributes)


class Recorder:
    """Capture what the integration hands to the Home Assistant helpers."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.scheduled: list[tuple[float, Any]] = []
        self.trackers: dict[str, Any] = {}
        self.signals: list[str] = []

    def async_call_later(
        self,
        _hass: Any,
        delay: float,
        target: Any,
    ) -> Callable[[], None]:
        """Record a delayed job instead of running it."""
        self.scheduled.append((delay, target))
        return lambda: None

    def async_track_state_change_event(
        self,
        _hass: Any,
        entity_ids: list[str],
        action: Any,
    ) -> Callable[[], None]:
        """Record a state change subscription."""
        for entity_id in entity_ids:
            self.trackers[entity_id] = action
        return lambda: None

    def async_dispatcher_send(self, _hass: Any, signal: str) -> None:
        """Record a dispatched signal."""
        self.signals.append(signal)

    def fire_timer(self) -> None:
        """Run the pending settlement target the way Home Assistant would."""
        _delay, target = self.scheduled.pop()
        target(None)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Install Home Assistant stubs for the duration of a test."""
    recorder = Recorder()

    modules = {
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.const": types.ModuleType("homeassistant.const"),
        "homeassistant.core": types.ModuleType("homeassistant.core"),
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.dispatcher": types.ModuleType(
            "homeassistant.helpers.dispatcher"
        ),
        "homeassistant.helpers.event": types.ModuleType("homeassistant.helpers.event"),
        "homeassistant.helpers.storage": types.ModuleType(
            "homeassistant.helpers.storage"
        ),
    }
    modules["homeassistant.const"].EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    modules["homeassistant.const"].Platform = types.SimpleNamespace(SENSOR="sensor")
    modules["homeassistant.core"].callback = hass_callback
    modules[
        "homeassistant.helpers.dispatcher"
    ].async_dispatcher_send = recorder.async_dispatcher_send
    modules["homeassistant.helpers.event"].async_call_later = recorder.async_call_later
    modules[
        "homeassistant.helpers.event"
    ].async_track_state_change_event = recorder.async_track_state_change_event
    modules["homeassistant.helpers.storage"].Store = FakeStore

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return recorder


def setup_entry(
    hass: FakeHass,
    entry: FakeConfigEntry,
) -> None:
    """Run ``async_setup_entry`` to completion."""
    assert asyncio.run(async_setup_entry(hass, entry)) is True


def build_entry(interval: float = 300) -> FakeConfigEntry:
    """Build a config entry wired to the test sensors."""
    return FakeConfigEntry(
        data={
            CONF_SOLAR_ENERGY_SENSOR: SOLAR_SENSOR,
            CONF_EXPORT_ENERGY_SENSOR: EXPORT_SENSOR,
            CONF_IMPORT_PRICE_SENSOR: IMPORT_PRICE_SENSOR,
            CONF_EXPORT_PRICE_SENSOR: EXPORT_PRICE_SENSOR,
            CONF_ACCOUNTING_INTERVAL: interval,
        },
    )


def prepare(
    recorder: Recorder,
    interval: float = 300,
) -> tuple[FakeHass, FakeConfigEntry]:
    """Set up an entry with seeded meters and tariffs."""
    hass = FakeHass()
    hass.states.set(SOLAR_SENSOR, "10", "kWh")
    hass.states.set(EXPORT_SENSOR, "4", "kWh")
    hass.states.set(IMPORT_PRICE_SENSOR, "0.30")
    hass.states.set(EXPORT_PRICE_SENSOR, "0.10")
    entry = build_entry(interval)
    setup_entry(hass, entry)
    recorder.signals.clear()
    return hass, entry


def test_scheduled_settlement_runs_on_the_event_loop(recorder: Recorder) -> None:
    """The interval settlement must be a callback, not an executor job.

    An undecorated target is treated as blocking and run in an executor
    thread, where ``async_dispatcher_send`` and the store raise a thread
    safety error.
    """
    hass, _entry = prepare(recorder)

    hass.states.set(SOLAR_SENSOR, "11", "kWh")
    recorder.trackers[SOLAR_SENSOR](None)

    assert recorder.scheduled, "an energy reading should arm the settlement timer"
    _delay, target = recorder.scheduled[-1]
    assert is_hass_callback(target)


def test_scheduled_settlement_values_the_accumulated_energy(
    recorder: Recorder,
) -> None:
    """Firing the timer settles and notifies the sensors."""
    hass, entry = prepare(recorder)

    hass.states.set(SOLAR_SENSOR, "11", "kWh")
    recorder.trackers[SOLAR_SENSOR](None)
    hass.states.set(EXPORT_SENSOR, "4.5", "kWh")
    recorder.trackers[EXPORT_SENSOR](None)

    recorder.fire_timer()

    values = entry.runtime_data.calculator.values
    # 1 kWh generated, 0.5 kWh exported: 0.5 kWh self-consumed at 0.30 and
    # 0.5 kWh exported at 0.10.
    assert values.self_consumption_savings == Decimal("0.15")
    assert values.export_revenue == Decimal("0.05")
    assert recorder.signals == [f"{SIGNAL_UPDATED}_{entry.entry_id}"]


def test_settlement_timer_is_armed_once_per_interval(recorder: Recorder) -> None:
    """Further readings reuse the pending timer instead of arming another."""
    hass, _entry = prepare(recorder)

    hass.states.set(SOLAR_SENSOR, "11", "kWh")
    recorder.trackers[SOLAR_SENSOR](None)
    hass.states.set(SOLAR_SENSOR, "12", "kWh")
    recorder.trackers[SOLAR_SENSOR](None)

    assert len(recorder.scheduled) == 1


def test_zero_interval_settles_without_a_timer(recorder: Recorder) -> None:
    """An interval of zero keeps the settle-on-every-reading behaviour."""
    hass, entry = prepare(recorder, interval=0)

    hass.states.set(SOLAR_SENSOR, "11", "kWh")
    recorder.trackers[SOLAR_SENSOR](None)

    assert recorder.scheduled == []
    assert recorder.signals == [f"{SIGNAL_UPDATED}_{entry.entry_id}"]
