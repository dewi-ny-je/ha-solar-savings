# Solar Savings for Home Assistant

Solar Savings is a Home Assistant custom integration that estimates the cumulative financial benefit of residential solar panels when import and export tariffs can change over time.

It tracks three cumulative monetary sensors:

- **Self-consumption savings**: generated solar energy that avoided buying electricity from the grid, valued at the current import tariff.
- **Export revenue**: positive net exported energy, valued at the current export tariff.
- **Total savings**: self-consumption savings plus export revenue.

## Accounting model

Solar energy is assumed to be consumed in this order:

1. Generated solar energy first reduces grid imports.
2. Only excess generation becomes exported energy.
3. Exported energy is normally worth less and is valued separately.

The integration listens continuously to:

- smart-meter import/export counter changes,
- solar-generation counter changes, and
- import/export tariff changes.

Meter readings only accumulate **energy**. For every smart-meter update it accumulates **positive net export**:

```text
net_export = max(export_delta - import_delta, 0)
```

For every solar-generation update it updates the solar baseline immediately, so daily resets are handled as soon as they are observed. Only positive solar deltas are accumulated:

```text
pending_solar_energy += max(solar_energy - previous_solar_energy, 0)
```

### When energy is valued

Revenue is linear in energy, so while a tariff is constant `sum(energy_i) * price` equals `sum(energy_i * price)`. Valuing every single reading is therefore redundant work - and a stored-state write every few seconds. Accumulated energy is instead converted into money at a **settlement**, which happens:

- once per **accounting interval**, armed by the first meter reading after the previous settlement, and
- immediately **before an import or export tariff change is applied**, using the tariff that was in force before the change.

A settlement values the accumulated energy:

```text
self_consumed_energy = max(pending_solar_energy - pending_net_export_energy, 0)
self_consumption_savings += self_consumed_energy * import_price
export_revenue += pending_net_export_energy * export_price
```

Settlements are also the only point where the savings sensors are written and the accounting snapshot is persisted, so a busy smart meter no longer causes a stored-state write every few seconds. Pending energy is settled and persisted on shutdown and on reload, so nothing is lost.

Two details keep a settlement that does not coincide with a meter reading accurate:

- The zero clamp on net export is applied **per reading**, because a value clamped over a whole interval is not the same number. Only the valuation is deferred, never the energy accounting.
- Net export is often observed before the slower solar counter reports the generation that produced it. Export energy that no solar reading covers yet is carried over and subtracted from the next settlement's generation, so a settlement landing between two solar readings cannot credit exported energy at the higher import tariff.

If a tariff needed by a settlement is unknown - an unavailable price sensor, or a restart before the first price arrives - the settlement is deferred instead of dropping the energy: it stays pending and is valued by the next settlement that has a price.

### Accounting interval

The **Accounting interval** defaults to **60 seconds**, for new entries and for existing entries that do not store an explicit value. Config entries created before this option was renamed keep their `minimum_accounting_interval` value.

- A positive value, such as `60 s`, accumulates energy and values it once per interval, plus once per tariff change.
- `0 s` values the accumulated energy on every sensor update. This restores the previous behaviour and is only useful for debugging.

Because a tariff change always triggers its own settlement, an interval longer than the tariff period never mixes two tariffs; it only makes the savings sensors update less often.

## Required input sensors

During setup, select five sensors and one accounting option:

| Input | Expected unit | Notes |
| --- | --- | --- |
| Solar generation energy | kWh | Total-increasing is ideal. Daily-reset production counters are supported by ignoring negative deltas. |
| Imported energy | kWh | Smart meter import counter. |
| Import price | currency/kWh | Current dynamic price paid for imported electricity. |
| Exported energy | kWh | Smart meter export counter. |
| Export price | currency/kWh | Current dynamic price received for exported electricity. |
| Accounting interval | seconds | How often accumulated energy is valued. Defaults to `60`. A tariff change always forces a settlement of its own, at the outgoing tariff. Set it to `0` to value energy on every sensor update. |

The exposed savings sensors use Home Assistant's configured currency and are cumulative monetary totals with `device_class: monetary` and `state_class: total`, allowing Home Assistant's recorder/statistics pipeline to track them.

## Actions

### `solar_savings.set_value`

Overwrites the stored cumulative total of a Solar Savings sensor. This is useful to seed the integration with values carried over from a previous system, or to correct accumulated drift.

| Field | Description |
| --- | --- |
| `value` | New cumulative monetary total, in the configured currency. Negative values are allowed; non-finite values (`NaN`/`∞`) are rejected. Pass the value as a quoted string to preserve exact decimal precision. |

Target either the **Self-consumption savings** or the **Export revenue** sensor. The **Total savings** sensor is derived from those two totals and does not support this action: it is skipped when you target the whole device or area, and selecting it explicitly is rejected before anything changes.

```yaml
action: solar_savings.set_value
target:
  entity_id: sensor.solar_savings_self_consumption_savings
data:
  value: 123.45
```

The new value is persisted immediately and the **Total savings** sensor is recalculated.

## Installation

### Manual installation

1. Copy `custom_components/solar_savings` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**.
4. Search for **Solar Savings**, select the five required sensors, and choose the accounting interval.

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

- UI-based config flow.
- Translations and entity translation keys.
- Stable unique IDs for entities.
- Persistent accounting state through Home Assistant storage.
- Pure calculation logic with regression tests.
- Ruff, mypy, pytest, and coverage configuration.
- A GitHub Actions workflow for continuous integration.

Before requesting inclusion in Home Assistant Core, replace placeholder code owners, add official documentation and branding assets, and run Home Assistant's `hassfest` validation in a Home Assistant Core checkout.
