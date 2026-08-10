# BitCruise

[![Tests](https://github.com/Peasniped/BitCruise/actions/workflows/tests.yml/badge.svg)](https://github.com/Peasniped/BitCruise/actions/workflows/tests.yml)
[![Hassfest](https://github.com/Peasniped/BitCruise/actions/workflows/hassfest.yml/badge.svg)](https://github.com/Peasniped/BitCruise/actions/workflows/hassfest.yml)
[![HACS](https://github.com/Peasniped/BitCruise/actions/workflows/hacs.yml/badge.svg)](https://github.com/Peasniped/BitCruise/actions/workflows/hacs.yml)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8.0%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

> [!WARNING]
> **Early development. Works, but not yet released.**
>
> BitCruise plans charging and drives a charger, and does both on the author's own
> installation. There are no tagged releases: configuration and entity names can
> still change without migration paths, so an upgrade may need reconfiguring.
>
> Treat it as convenience automation rather than a guarantee. Do not depend on it
> for a car you must have charged by morning.

A Home Assistant custom integration that plans and executes residential EV charging
using data that already exists in Home Assistant.

BitCruise reads your vehicle's state of charge and your electricity price sensor,
works out the cheapest window that will reach your charge target before you need the
car, proposes that schedule for your approval, and then drives your charger through
the Home Assistant entities and actions you selected.

It does **not** talk to any vehicle, charger, or price API directly. Everything goes
through Home Assistant, so BitCruise is not tied to any particular brand of car,
charger, or electricity provider.

## What it does today

- Works out how much energy the car needs and how long that takes.
- Finds the cheapest contiguous window that reaches your target before your
  ready-by time, using real prices where they exist and forecast prices for the
  hours the real ones have not reached yet.
- Asks before charging, as much or as little as you want: every time, only when the
  plan changes, or never. That is a control on the dashboard, not a buried setting.
- Authorizes and starts your charger at the planned time, and stops it at the end.
- Says what it is doing in one sentence, in `sensor.bitcruise_summary`, rather than
  leaving you to assemble it from a dozen entities.

**Charger control is opt-in.** The `Operate the charger` switch is **off** by
default: BitCruise decides and reports exactly as it would, and presses nothing.
Watch it make the right calls for a few nights before letting it make them.

Not yet built: notifications, a reserve floor for unplanned trips, calendar-driven
trips, and multi-day price optimisation. See [TODO.md](TODO.md).

## Requirements

- Home Assistant **2026.8.0** or newer.
- **One vehicle per installation.** BitCruise is deliberately limited to a single
  config entry. Two cars sharing a charger, or two chargers sharing a house supply,
  need coordination that does not exist yet — running two independent planners would
  double-book the charger or exceed the supply limit. If you have two EVs, run
  BitCruise for the one that benefits most from price optimization and charge the
  other manually.
- A vehicle integration exposing battery state of charge.
- An electricity price integration exposing hourly or 15-minute prices.
- A charger exposed through Home Assistant, if you want BitCruise to start and stop
  charging rather than only plan it.

The reference installation is a Volvo XC40 Recharge, a Zaptec Go 2, and Energi Data
Service with Carnot forecasts, at 16 A three-phase (about 11 kW). None of those are
required — they are simply what the first release is tested against. Their real
entity names, units and traps are recorded in
[docs/reference-installation.md](docs/reference-installation.md).

> [!NOTE]
> If your charger has its own built-in schedule, switch it to normal or default
> control. A charger following its own schedule accepts a start command and then
> quietly ignores it, and nothing it reports tells you why.

## Installation

### HACS (recommended, once releases exist)

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/Peasniped/BitCruise` with category **Integration**.
3. Install **BitCruise**, then restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and search for
   **BitCruise**.

### Manual

Copy `custom_components/bitcruise/` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

Setup asks for three pages: the entities to read, the charging settings, and — all
optional — the charger controls. Leaving the third page empty is a complete
configuration: BitCruise plans and tells you, and you start the charger yourself.

The installed version is shown on the integration page and as the device's firmware
version, which is how to check that an upgrade actually took.

## Dashboard

[docs/lovelace-card.yaml](docs/lovelace-card.yaml) is a ready-made card showing the
summary sentence, the smart-charging toggle, the approval policy, and the accept /
reject / recalculate buttons in one tile. It needs the `button-card` custom card from
HACS. A card shipped with the integration is on the backlog, not in the box.

## Development

Requires Python **3.14**. Home Assistant 2026.3 and newer will not run on anything
older, and a 3.13 environment silently resolves Home Assistant back to 2026.2.x
rather than failing, which produces confusing test results.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

pip install pytest pytest-asyncio pytest-homeassistant-custom-component ruff

ruff check .                  # lint
ruff format .                 # format
```

### Running tests on Windows

Home Assistant does not support Windows: it imports the Unix-only `fcntl` and
`resource` modules, and its test harness blocks the sockets Windows asyncio needs.
`tools/winshim/` provides stand-ins for the first two, and `tests/ha/conftest.py`
lifts the socket guard on Windows only, so the whole suite runs:

```powershell
$env:PYTHONPATH = "$PWD\tools\winshim"
pytest
```

On Linux and macOS no setup is needed — just `pytest`. The shims refuse to import
anywhere but Windows, so they can never shadow the real modules, and CI keeps the
socket guard that catches tests reaching the network.

Without `PYTHONPATH` set, the pure tests still run on their own:

```powershell
pytest -p no:homeassistant --ignore=tests/ha
```

`tests/` holds pure tests with no Home Assistant dependency; `tests/ha/` holds tests
that need a `hass` instance. The charging planner and price adapter live entirely on
the first side of that line by design.

### VS Code

The Python and Ruff extensions are the only ones needed. Point the interpreter at
`.venv` (**Ctrl+Shift+P → Python: Select Interpreter**) so tests and linting resolve
Home Assistant imports.

To test against a real Home Assistant instance, symlink or copy
`custom_components/bitcruise/` into that instance's config directory and restart it.

## Documentation

| File | Contents |
| --- | --- |
| [TODO.md](TODO.md) | Actionable backlog |
| [docs/reference-installation.md](docs/reference-installation.md) | Real entity names, units and traps from the development installation |
| [docs/lovelace-card.yaml](docs/lovelace-card.yaml) | Example dashboard card |
| [CLAUDE.md](CLAUDE.md) | Instructions for AI-assisted development |

## Licence

[MIT](LICENSE)
