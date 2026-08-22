# Solar Savings for Home Assistant

Solar Savings is a Home Assistant custom integration that estimates the cumulative financial benefit of residential solar panels - and, optionally, of a home battery - when import and export tariffs can change over time.

It answers one question: **how much less did the house pay than it would have paid without the equipment?** Everything it publishes is a difference between what actually happened and a counterfactual house that lacked the battery, or lacked both the battery and the panels.

## Sensors

Which sensors an entry publishes depends on whether the optional battery section is filled in. Sensors marked *hidden* are created but disabled by default; switch them on in the entity settings if you want them.

| Sensor | Solar only | With battery |
| --- | --- | --- |
| **Solar savings** | enabled | enabled |
| **Battery savings** | enabled (always `0`) | enabled |
| **Total savings** | enabled | enabled |
| **Self-consumption savings** | enabled | hidden |
| **Export revenue** | enabled | hidden |
| **Actual grid cost** | not created | hidden |
| **Grid cost without battery** | not created | hidden |
| **Grid cost without battery and solar** | not created | hidden |
| **Imported energy without battery** | not created | hidden |
| **Exported energy without battery** | not created | hidden |
| **Imported energy without battery and solar** | not created | hidden |

- **Solar savings** is what the panels earned: the imports they avoided plus the export they earned. It is still reported split into **Self-consumption savings** and **Export revenue**.
- **Battery savings** is what the battery earned by moving energy in time, which is negative whenever it charged from the grid or gave up export revenue, and positive when it later avoided a more expensive import.
- **Total savings** is the sum of the two, and is exactly *grid cost without battery and solar* minus *actual grid cost*.

All three can be negative: exporting at a negative tariff costs money, and so does charging a battery from the grid.

## Accounting model

### The three scenarios

Over any window, conservation of energy fixes the house load:

```text
load = solar + grid_import + battery_discharge - grid_export - battery_charge
```

The load is a property of the house, not of the equipment, so it stays the same in every scenario. Each scenario is therefore just a different grid position, and all three are valued with the same two tariffs:

```text
cost = import_energy * import_price - export_energy * export_price
```

1. **Actual.** What the meter recorded: `grid_import` and `grid_export`.
2. **Without the battery.** The grid has to cover what the battery contributed, `battery_discharge - battery_charge`. That correction is applied against the opposite direction of the actual flow first: energy the battery supplied would otherwise have reduced the export before it forced an import, and energy the battery absorbed would otherwise have reduced the import before it became export.
3. **Without the battery and without the panels.** The same correction again, with the generation. Everything the second scenario exported came from the panels, so removing them removes that export and turns the remaining generation - the part that served the house - into import.

The published figures are the differences:

```text
battery_savings = cost_without_battery - actual_cost
solar_savings   = cost_without_battery_and_solar - cost_without_battery
total_savings   = cost_without_battery_and_solar - actual_cost
```

### One model, with or without a battery

A house with no battery is the same model with `battery_charge = battery_discharge = 0`. The correction is then zero, the second scenario collapses onto the first, battery savings are exactly zero, and solar savings reduce to

```text
self_consumed_energy = solar_generated - exported_energy
solar_savings = self_consumed_energy * import_price + exported_energy * export_price
```

which is the energy-balance rule this integration used before batteries were supported, to the cent. That is why an existing entry keeps its **Self-consumption savings**, **Export revenue** and **Total savings** figures unchanged after the upgrade, and why there is a single code path for both cases.

The one thing a solar-only entry cannot do is put a price on a scenario, because the absolute cost of a grid position needs the imported-energy register, which it does not ask for. The *differences* do not: the import register cancels out of every one of them. So the savings are exact either way, and the scenario cost sensors are only published when the battery section is filled in.

### Why the battery matters for solar savings too

Without battery tracking, every exported kWh is assumed to come from the panels. A battery breaks that assumption in both directions: a battery discharging into the grid inflates export revenue with energy the panels did not produce right then, and solar stored in a battery is credited at the import tariff of the moment it was stored rather than the moment it was used. Tracking the battery removes both distortions from the solar figure and moves them into the battery figure, where they belong.

### When energy is attributed and valued

The integration listens continuously to every configured energy register and to the two tariff sensors. Accounting is a three-stage pipeline.

**Observing.** Each counter is read on every update of any of them, so a daily-reset counter is handled as soon as the reset is observed, and only positive deltas are accumulated:

```text
observed_delta += max(reading - previous_reading, 0)
```

**Attributing.** The observed deltas are attributed to the three scenarios, once per settlement window.

This is deliberately *not* done per reading. The battery correction is cancelled against the grid flow of the same window, and the registers do not report together: a smart meter typically reports every few seconds while a battery counter reports about once a minute. Attributing each reading on arrival would leave almost every window holding grid export with no battery delta - which reads as solar export - and the occasional window holding a battery delta with no grid export, which reads as an avoided import. A battery selling to the grid overnight would show up as solar export revenue on a night with no sun. Attributing over the window the energy is then valued in keeps both sides of the cancellation together.

**Settling.** The accumulated scenario energy is converted into money. Revenue is linear in energy, so while a tariff is constant `sum(energy_i) * price` equals `sum(energy_i * price)`, and valuing every single reading would only mean a stored-state write every few seconds. A settlement happens:

- once per **accounting interval**, armed by the first meter reading after the previous settlement, and
- immediately **before an import or export tariff change is applied**, using the tariff that was in force before the change.

Settlements are the only point where the sensors are written and the accounting snapshot is persisted. Pending energy is settled and persisted on shutdown and on reload, so nothing is lost.

If a tariff needed by a settlement is unknown - an unavailable price sensor, or a restart before the first price arrives - the settlement is deferred instead of dropping the energy: it stays pending and is valued by the next settlement that has a price.

### Registers that report at different moments

The solar counter usually reports every few minutes while the smart meter reports every few seconds, so a settlement can land after export was recorded but before the solar reading that covers it. Export energy that no solar reading covers yet is carried over and subtracted from the next settlement's generation, instead of being dropped and then credited again at the higher import tariff. That makes the solar split independent of where the settlement boundaries fall.

The battery registers are handled by the settlement window itself, as described above: the window has to be long enough to hold a reading from every meter, which is why a battery entry never settles faster than **60 seconds** however short its accounting interval is.

If a battery register has no usable value at all when a window closes, the observed energy is **held unattributed** rather than being assigned to a battery that may or may not have been running: the deltas are preserved, so attribution is exact once the register reports again. The wait is bounded at **5 minutes**; past that the sensor is treated as gone, the held energy is accounted for as if the battery had been idle, and a warning is logged. A warning is logged once per outage, and an informational message when the register recovers.

### Accounting interval

The **Accounting interval** defaults to **60 seconds**, for new entries and for existing entries that do not store an explicit value. Config entries created before this option was renamed keep their `minimum_accounting_interval` value.

- A positive value, such as `60 s`, accumulates energy and values it once per interval, plus once per tariff change.
- `0 s` values the accumulated energy on every sensor update.

Without a battery, the interval mostly decides how often the savings sensors update rather than what they add up to: a tariff change always triggers its own settlement, and the solar split is boundary-independent.

**With a battery it is also the attribution window**, so it has to be long enough to hold a reading from each of the grid and battery meters. A battery entry therefore settles at no less than **60 seconds**, and `0 s` is raised to that with a note in the log. Raise it if your battery counters report less often than once a minute; a window that is very long instead starts to blur the battery attribution, because an import and an export inside one window partly cancel before the correction is applied. Anything from roughly one to five minutes is a good place to be.

## Input sensors

During setup, select four sensors and one accounting option:

| Input | Expected unit | Notes |
| --- | --- | --- |
| Solar generation energy | kWh | Total-increasing is ideal. Daily-reset production counters are supported by ignoring negative deltas. |
| Import price | currency/kWh | Current dynamic price paid for imported electricity. |
| Exported energy | kWh | Smart meter export counter. |
| Export price | currency/kWh | Current dynamic price received for exported electricity. |
| Accounting interval | seconds | How often accumulated energy is attributed and valued. Defaults to `60`. A tariff change always forces a settlement of its own, at the outgoing tariff. Set it to `0` to value energy on every sensor update; with battery tracking the minimum is `60`. |

The **Battery tracking** section at the bottom of the form is optional, and takes three more sensors. Fill in **all three or none**: the form rejects a partial selection, because a scenario cannot be built without all of them.

| Input | Expected unit | Notes |
| --- | --- | --- |
| Imported energy | kWh | Smart meter import counter. Needed to price a scenario. |
| Battery charged energy | kWh | Cumulative energy that went into the battery. |
| Battery discharged energy | kWh | Cumulative energy taken out of the battery. |

Check how often these three counters update. The accounting interval has to be at least as long as the slowest of them, or the battery's contribution cannot be cancelled against the grid flow it belongs to.

All energy sensors are read in `Wh`, `kWh`, or `MWh` and must be monotonically increasing, apart from resets such as a daily counter returning to zero, which are detected and ignored.

Battery tracking can be added to an existing entry from the integration's options: the new registers are seeded from their current readings, so no historical energy is counted, and the entry starts publishing the battery sensors after the reload. The savings accumulated so far are kept.

The monetary sensors use Home Assistant's configured currency and are cumulative totals with `device_class: monetary` and `state_class: total`. The virtual energy meters are `device_class: energy` with `state_class: total_increasing`, so they can be used in the energy dashboard as counterfactual comparisons.

## Actions

### `solar_savings.set_value`

Overwrites the stored cumulative total of a Solar Savings sensor. This is useful to seed the integration with values carried over from a previous system, or to correct accumulated drift.

| Field | Description |
| --- | --- |
| `value` | New cumulative total: an amount in the configured currency for the monetary sensors, or kWh for the virtual energy meters. Monetary totals may be negative; the energy meters may not. Non-finite values (`NaN`/`∞`) are rejected. Pass the value as a quoted string to preserve exact decimal precision. |

Every stored total can be set: **Self-consumption savings**, **Export revenue**, **Battery savings**, the three scenario costs, and the three virtual energy meters. **Solar savings** and **Total savings** are derived from those totals and do not support this action: they are skipped when you target the whole device or area, and selecting one explicitly is rejected before anything changes. To seed solar savings, set the self-consumption and export totals it is calculated from.

```yaml
action: solar_savings.set_value
target:
  entity_id: sensor.solar_savings_self_consumption_savings
data:
  value: 123.45
```

The new value is persisted immediately and the derived sensors are recalculated.

## Installation

### Manual installation

1. Copy `custom_components/solar_savings` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**.
4. Search for **Solar Savings**, select the four required sensors, choose the accounting interval, and optionally fill in the battery section.

### HACS custom repository

Once you publish this repository on GitHub, add it to HACS as a custom integration repository.

## Development

Create a virtual environment and install development dependencies:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run linting and type checks:

```bash
ruff check .
mypy custom_components/solar_savings
```

## Quality notes

This project includes:

- UI-based config flow, with a collapsible section for the optional battery sensors.
- Translations and entity translation keys.
- Stable unique IDs for entities.
- Persistent accounting state through Home Assistant storage, with config entry and stored-snapshot migrations.
- Pure calculation logic with regression tests, including the identities that tie the three scenarios together.
- Ruff, mypy, pytest, and coverage configuration.
- A GitHub Actions workflow for continuous integration.

Before requesting inclusion in Home Assistant Core, replace placeholder code owners, add official documentation and branding assets, and run Home Assistant's `hassfest` validation in a Home Assistant Core checkout.
