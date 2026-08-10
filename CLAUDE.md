# CLAUDE.md

Instructions for Claude working in this repository.

## Don'ts

- Don't add new docstrings/comments/type hints to code you didn't change.
- Don't run destructive git commands without confirmation.
- Don't introduce new dependencies without need.
- No need to tell me about the CRLF→LF warnings every commit.

## Do's

- Whenever I send a prompt, ask yourself if what I am asking makes sense. If not, ask. I am not the smartest person in the world, and may make mistakes or ask for things that don't make sense (:
- Always DO ask me if you are ever uncertain about something.
- DO keep `CLAUDE.md` up to date when making changes that contradict or add to something written in here.
- When you are done implementing a feature, before committing, DO tell me what to test to assess the implementation, as user steps.
- DO write git commits at regular intervals when it makes sense. Keep the messages short — a subject line and a sentence or two of context at most. Don't restate the diff.
- You must DO show a preview of the commit message in text and get my confirmation **before** calling any git tool.

## What this project is

**BitCruise** is a Home Assistant custom integration that plans and executes residential EV charging from entities that already exist in Home Assistant.

- GitHub repository: `Peasniped/BitCruise`
- Integration domain: `bitcruise` — **final**. It appears in entity IDs, storage, config entries, and user installations. Do not rename it.

Everything else about the product lives in the documents below. Do not restate spec content here.

## Document map

| File | Contents | Rule |
| --- | --- | --- |
| `CLAUDE.md` | How Claude works in this repo | This file. Keep it about process, not product. |
| `DESIGN.md` | Product scope, domain model, architecture, ADRs, test matrix | Source of truth for *what* to build. Working document — see below. |
| `PLAN.md` | Delivery phases, goals, acceptance criteria | Source of truth for *order*. Working document — see below. |
| `TODO.md` | Outstanding work, and nothing else | Source of truth for *what is left*. Delete items as they land; do not accumulate ticked ones. Phase status lives in PLAN.md. |
| `docs/reference-installation.md` | Real entity names, units, enums and traps from the development installation | Read before writing code that reads an entity. Evidence, not configuration — never hard-code anything from it. |

`DESIGN.md` and `PLAN.md` are **transitional working documents**. The intent is to
retire them once the behaviour they describe is implemented and covered by tests, so
they are deliberately not referenced from `README.md`. Keep using them while they
exist, but do not expand them beyond what is needed to build the current phase, and
prefer encoding decisions as tests and docstrings — those survive the documents.

## Working rules

1. Read `CLAUDE.md`, then `DESIGN.md` and `PLAN.md`, before changing anything.
2. Work the earliest incomplete phase in `PLAN.md` unless the user says otherwise.
3. Keep changes scoped to the requested phase. Do not build ahead into later phases.
4. Inspect existing files and tests before coding. Do not overwrite working architecture casually.
5. Add or update tests with every behavior change. `DESIGN.md` §14 lists the required coverage.
6. Run the relevant test/lint/validation commands after changes and report the real result.
7. Update `TODO.md` as work completes: **delete** finished items rather than ticking them, so the file only ever lists work that remains. Add newly discovered work there rather than leaving it implicit. If finishing something taught you a rule worth keeping, add it to "Traps this project has already fallen into" above.
8. If a Home Assistant API is uncertain or may have changed, check the current official developer documentation before implementing. Do not copy patterns from old third-party custom components.
9. Do not invent service/action schemas for Volvo, Zaptec, Energi Data Service, Fastmail, HACS, or Home Assistant. Verify against real entities/docs, or make the capability user-configurable.
10. Do not hard-code the user's personal entity IDs, device names, or notification targets.
11. Do not commit secrets: app passwords, API tokens, HA URLs, addresses, or personal calendar data. Sanitize all fixtures captured from the live installation.
12. Never alter an approved charging plan without the user's say-so (`DESIGN.md` ADR-003). Setting `select.approval_policy` to `automatic` *is* that say-so, given once instead of nightly; it is the only path that may replace an approved plan without a prompt.
13. Never execute charger actions from tests against a live Home Assistant instance.
14. Prefer backwards-compatible config-entry migrations once a release may be installed.
15. Treat charging as convenience automation, not a safety-critical guarantee. Surface uncertainty and failure clearly.
16. When proposing a large architectural change, update `DESIGN.md` (add an ADR) or `PLAN.md` first, before implementing it.

## Local environment

- Development workstation is **Windows**. Target Home Assistant instance is **HA OS, Core 2026.8.1**.
- HA 2026.3+ requires **Python 3.14**. The repo venv is `.venv` on 3.14; a 3.13 venv silently resolves Home Assistant back to 2026.2.x, so do not use it.
- **Running the tests on Windows.** Home Assistant imports the Unix-only `fcntl` and `resource`, and its test harness blocks the sockets Windows asyncio needs. `tools/winshim/` plus a Windows-only escape in `tests/ha/conftest.py` work around all three:

  ```powershell
  $env:PYTHONPATH = "D:\Github\BitCruise\tools\winshim"
  .\.venv\Scripts\python.exe -m pytest -q
  ```

  Without `PYTHONPATH` set, only the pure tests can run:
  `pytest -p no:homeassistant --ignore=tests/ha` (`no:homeassistant` is the `pytest11` entry-point name, not the package name).
- **Run the full suite before pushing.** CI logs are not readable from here without a token, so a red build costs a blind round trip. The shims exist precisely so that is avoidable.
- `tests/conftest.py` must never import Home Assistant, so the pure tests keep working without the shims.
- HA test instances do **not** use your timezone. Any test involving ready-by must call `await hass.config.async_set_time_zone(...)`, or a wall-clock time resolves to the wrong instant and the planner correctly picks a different window.
- Entity IDs derive from the **display name**, not the translation key. Keep them consistent or `sensor.bitcruise_<key>` will not exist.

## Coding standards

- Python only for backend integration logic.
- Fully type new code.
- Prefer dataclasses, enums, and protocols for domain models.
- Keep functions small and deterministic.
- Avoid global mutable state.
- Avoid blocking I/O in the event loop; no background threads for normal HA work.
- Use Home Assistant helpers for scheduling, entity tracking, storage, and time.
- Never use naive datetimes internally.
- Keep the planner pure: no `hass`, entities, services, or config entries in the optimization layer.
- Log actionable information only; do not spam logs each minute. Never log credentials or calendar contents unnecessarily.
- Keep user-facing strings translatable; use translation keys where HA expects them.

### Dependency policy

Prefer no third-party Python dependencies for V1. If one becomes necessary: justify it, pin it appropriately in `manifest.json`, and keep vendor/protocol communication in a separate library rather than inside the integration.

## Traps this project has already fallen into

Standing rules, each one paid for. `TODO.md` tracks work, not history, so these live
here where they are read every session.

- **Route every datetime comparison, duration and ordering through `to_utc` or `elapsed_hours`.** Python does *wall-clock* arithmetic when two aware datetimes share a `tzinfo`, which is wrong twice a year: a spring-forward 01:00→03:00 measures 2 hours when 1 elapses, and the repeated hour on a fall-back day makes two different instants compare equal, breaking sorting and overlap detection. Regression tests: `tests/test_models.py::TestDaylightSavingArithmetic`. The one deliberate exception is `next_occurrence`, where "ready by 07:00" genuinely means the wall clock.
- **Nothing that is not JSON-serializable may reach an entity state attribute.** Home Assistant serializes states with orjson, which refuses `Decimal`. The entity then never reaches the frontend and shows as `unavailable`, with nothing on it to say why. Currency is `Decimal` throughout, so this is a standing hazard; convert at the presentation boundary. Guard: `tests/ha/test_serialization.py`. Reading attributes in-process, as most tests do, cannot catch it.
- **Plan ids must be derived from plan content only.** Mixing the calculation time in makes every recomputation look like a new plan, and the approval machine re-asks about a window the user already answered.
- **`integration_type: service`, not `helper`.** `helper` files the integration under the Helpers tab, whose UI opens an options flow on click, so an integration without one fails with "Invalid handler specified" (home-assistant/frontend#15044). The calculated nature is carried by `iot_class`. Regression test in `tests/test_manifest.py`.
- **Do not debounce recomputation.** A coordinator-level debouncer was tried and reverted: on a restart every source entity appears in one burst, so it evaluated the first and deferred the rest, leaving the integration reporting "entity not found" for entities that plainly existed. Recomputation is a pure function over `hass.states` and costs nothing. Debouncing belongs at the point a *notification* would be sent.
- **`suggested_display_precision` never reaches the state.** Home Assistant writes it into the entity registry for the *frontend* to round with; the state itself keeps whatever the calculation produced, so a template, an automation, or Developer Tools reads `53.833372711111111884`. Rounding happens in `native_value` (`sensor.py`, `_rounded`), using that same precision so state and display cannot disagree.
- **Energi Data Service already includes tariffs and surcharges.** Never add the `tariffs` attribute to the exposed price — it double-counts and inflates cost by roughly 40%.

## Decisions that are settled

Do not reopen these without a reason; they are load-bearing.

- Licence MIT, copyright Peasniped. Change to a legal name only if distributed formally.
- Minimum Home Assistant `2026.8.0`; Python 3.14 (HA 2026.3+ requires it).
- `single_config_entry: true` for V1. Loosening it later is backwards compatible.
- Currency is `Decimal`; energy, power and SoC stay `float`. Conversion happens only at the price × energy boundary, so repeated cost addition stays exact.
- Whole price intervals are allocated, with the slack reported as `over_allocation_kwh`. Cost is charged on the energy expected to be drawn, since the car stops at target rather than running the window out.
- Approval defaults to `ask_on_change`: the first plan of a cycle is approved automatically, every material move still asks. `always_ask` would need a press every evening, and a missed press means no charging. `automatic` never asks and is a legitimate choice, not a debug mode.
- The approval policy is `select.approval_policy`, not an options-flow setting, and the entity is the only source of truth. It is persisted with the approval record; a config entry that predates the entity seeds it once.

## Definition of done

A feature is done when:

- behavior is implemented and unhappy paths are handled;
- entity/state semantics are stable;
- tests cover the core behavior and the relevant cases from `DESIGN.md` §14;
- translations and user-facing descriptions are updated;
- README/config docs are updated if the change is user-visible;
- HACS, Hassfest, and test workflows pass;
- restart behavior has been considered;
- `TODO.md` reflects reality;
- no secrets or installation-specific entity IDs are committed.
