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

Smart meters often update import/export counters more frequently than inverter or solar-production sensors. This integration therefore listens to import/export changes continuously and accumulates **positive net export** between solar-generation updates:

```text
net_export = max(export_delta - import_delta, 0)
```

When the solar-generation sensor updates, the integration calculates the positive solar delta, ignores negative deltas caused by daily resets, subtracts pending net export, and values the remainder at the current import tariff:

```text
self_consumed_energy = max(solar_delta - pending_net_export, 0)
self_consumption_savings += self_consumed_energy * import_price
export_revenue += pending_net_export * export_price
```

## Required input sensors

During setup, select five sensors:

| Input | Expected unit | Notes |
| --- | --- | --- |
| Solar generation energy | kWh | Total-increasing is ideal. Daily-reset production counters are supported by ignoring negative deltas. |
| Imported energy | kWh | Smart meter import counter. |
| Import price | currency/kWh | Current dynamic price paid for imported electricity. |
| Exported energy | kWh | Smart meter export counter. |
| Export price | currency/kWh | Current dynamic price received for exported electricity. |

The exposed savings sensors use Home Assistant's configured currency and are cumulative monetary totals with `device_class: monetary` and `state_class: total`, allowing Home Assistant's recorder/statistics pipeline to track them.

## Installation

### Manual installation

1. Copy `custom_components/solar_savings` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**.
4. Search for **Solar Savings** and select the five required sensors.

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
