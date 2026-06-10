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

- smart-meter import/export counter changes, and
- solar-generation counter changes.

For every smart-meter update it accumulates **positive net export**:

```text
net_export = max(export_delta - import_delta, 0)
```

For every solar-generation update it updates the solar baseline immediately, so daily resets are handled as soon as they are observed. Only positive solar deltas are accumulated:

```text
pending_solar_energy += max(solar_energy - previous_solar_energy, 0)
```

Monetary accounting is then settled using the accumulated solar and smart-meter deltas:

```text
self_consumed_energy = max(pending_solar_energy - pending_net_export, 0)
self_consumption_savings += self_consumed_energy * import_price
export_revenue += pending_export_revenue
```

The settlement step is throttled by **Minimum accounting interval**, which defaults (and it is recommended to be set) to **60 seconds** for both new and existing configurations that do not already store an explicit value. This avoids creating an accounting boundary on every solar reading when solar and smart-meter energy sensors both update very frequently, for example every second. Solar readings are still observed continuously; only the conversion of accumulated energy deltas into monetary totals is deferred.

- `0 s` preserves immediate settlement on every solar-generation update.
- A positive value, such as `60 s`, accumulates deltas and settles them no more frequently than that interval.

## Required input sensors

During setup, select five sensors and one accounting option:

| Input | Expected unit | Notes |
| --- | --- | --- |
| Solar generation energy | kWh | Total-increasing is ideal. Daily-reset production counters are supported by ignoring negative deltas. |
| Imported energy | kWh | Smart meter import counter. |
| Import price | currency/kWh | Current dynamic price paid for imported electricity. |
| Exported energy | kWh | Smart meter export counter. |
| Export price | currency/kWh | Current dynamic price received for exported electricity. |
| Minimum accounting interval | seconds | Minimum spacing between monetary settlements. Defaults to `60`. Existing entries without an explicitly stored value also use `60`. Set it to `0` to restore immediate settlement on every solar reading. |

The exposed savings sensors use Home Assistant's configured currency and are cumulative monetary totals with `device_class: monetary` and `state_class: total`, allowing Home Assistant's recorder/statistics pipeline to track them.

## Actions

### `solar_savings.set_value`

Overwrites the stored cumulative total of a Solar Savings sensor. This is useful to seed the integration with values carried over from a previous system, or to correct accumulated drift.

| Field | Description |
| --- | --- |
| `value` | New cumulative monetary total, in the configured currency. Negative values are allowed; non-finite values (`NaN`/`∞`) are rejected. Pass the value as a quoted string to preserve exact decimal precision. |

Target either the **Self-consumption savings** or the **Export revenue** sensor. The **Total savings** sensor is derived from those two totals and cannot be set directly; calling the action on it raises an error.

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
4. Search for **Solar Savings**, select the five required sensors, and choose the minimum accounting interval.

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
