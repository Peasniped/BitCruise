# BitCruise

> [!WARNING]
> **Early development. Not ready for use.**
>
> BitCruise does not yet plan or control charging. The repository currently contains
> a config-entry skeleton only. Do not rely on it to charge a vehicle. Behavior,
> configuration schema, and entity names will change without migration paths until
> a `0.1.0` release is tagged.

A Home Assistant custom integration that plans and executes residential EV charging
using data that already exists in Home Assistant.

BitCruise reads your vehicle's state of charge and your electricity price sensor,
works out the cheapest window that will reach your charge target before you need the
car, proposes that schedule for your approval, and then drives your charger through
the Home Assistant entities and actions you selected.

It does **not** talk to any vehicle, charger, or price API directly. Everything goes
through Home Assistant, so BitCruise is not tied to any particular brand of car,
charger, or electricity provider.

## Status

| Phase | Status |
| --- | --- |
| 0 — Repository bootstrap | Loads and unloads on HA 2026.8.1 |
| 1 — Pure charging planner | Not started |
| 2 — HA source binding and sensors | Not started |
| 3 — Price adapter (Energi Data Service / Carnot) | Not started |
| 4 — Proposal / approval state machine | Not started |
| 5 — Notifications | Not started |
| 6 — Charger execution | Not started |
| 7 — Restart recovery | Not started |
| 8 — First HACS release | Not started |
| 9–15 — Calendar, trips, urgency, multi-vehicle | Future |

See [PLAN.md](PLAN.md) for the full delivery plan and [TODO.md](TODO.md) for the
current backlog.

## Requirements

- Home Assistant **2026.8.0** or newer.
- **One vehicle per installation.** BitCruise is deliberately limited to a single
  config entry. Two cars sharing a charger, or two chargers sharing a house supply,
  need coordination that does not exist yet — running two independent planners would
  double-book the charger or exceed the supply limit. If you have two EVs, run
  BitCruise for the one that benefits most from price optimization and charge the
  other manually. See [DESIGN.md](DESIGN.md) section 18.
- A vehicle integration exposing battery state of charge.
- An electricity price integration exposing hourly or 15-minute prices.
- A charger exposed through Home Assistant, if you want BitCruise to start and stop
  charging rather than only plan it.

The reference installation is a Volvo XC40 Recharge, a Zaptec Go 2, and Energi Data
Service with Carnot forecasts, at roughly 10 kW. None of those are required — they
are simply what the first release is tested against.

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

## Development

Requires Python 3.13 or newer. Home Assistant 2026.x runs on Python 3.14.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

pip install pytest pytest-asyncio pytest-homeassistant-custom-component ruff

ruff check .                  # lint
ruff format .                 # format
```

### Running tests on Windows

Home Assistant cannot be imported on Windows — `homeassistant.runner` imports the
Unix-only `fcntl` module. The test suite is therefore split:

```bash
pytest -p no:homeassistant --ignore=tests/ha   # pure tests - works on Windows
pytest                                         # everything - Linux, macOS, or CI only
```

`tests/` holds pure tests with no Home Assistant dependency; `tests/ha/` holds tests
that need a `hass` instance. The pure charging planner lives entirely on the first
side of that line by design, so most development can happen on Windows. The
Home Assistant tests run in CI on every push.

### VS Code

The Python and Ruff extensions are the only ones needed. Point the interpreter at
`.venv` (**Ctrl+Shift+P → Python: Select Interpreter**) so tests and linting resolve
Home Assistant imports.

To test against a real Home Assistant instance, symlink or copy
`custom_components/bitcruise/` into that instance's config directory and restart it.

## Documentation

| File | Contents |
| --- | --- |
| [DESIGN.md](DESIGN.md) | Product scope, domain model, architecture, ADRs |
| [PLAN.md](PLAN.md) | Delivery phases and acceptance criteria |
| [TODO.md](TODO.md) | Actionable backlog |
| [CLAUDE.md](CLAUDE.md) | Instructions for AI-assisted development |

## Licence

[MIT](LICENSE)
