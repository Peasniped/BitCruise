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
- DO write git commits at regular intervals when it makes sense.
- You must DO show a preview of the commit message in text and get my confirmation **before** calling any git tool.

## What this project is

**BitCruise** is a Home Assistant custom integration that plans and executes residential EV charging from entities that already exist in Home Assistant.

- GitHub repository: `BitPusher/BitCruise`
- Integration domain: `bitcruise` — **final**. It appears in entity IDs, storage, config entries, and user installations. Do not rename it.

Everything else about the product lives in the documents below. Do not restate spec content here.

## Document map

| File | Contents | Rule |
| --- | --- | --- |
| `CLAUDE.md` | How Claude works in this repo | This file. Keep it about process, not product. |
| `DESIGN.md` | Product scope, domain model, architecture, ADRs, test matrix | Source of truth for *what* to build. |
| `PLAN.md` | Delivery phases, goals, acceptance criteria | Source of truth for *order*. |
| `TODO.md` | Actionable implementation backlog | Source of truth for *state of work*. Tick items here, not in PLAN.md. |

## Working rules

1. Read `CLAUDE.md`, then `DESIGN.md` and `PLAN.md`, before changing anything.
2. Work the earliest incomplete phase in `PLAN.md` unless the user says otherwise.
3. Keep changes scoped to the requested phase. Do not build ahead into later phases.
4. Inspect existing files and tests before coding. Do not overwrite working architecture casually.
5. Add or update tests with every behavior change. `DESIGN.md` §14 lists the required coverage.
6. Run the relevant test/lint/validation commands after changes and report the real result.
7. Update `TODO.md` as work completes. Add newly discovered work there rather than leaving it implicit.
8. If a Home Assistant API is uncertain or may have changed, check the current official developer documentation before implementing. Do not copy patterns from old third-party custom components.
9. Do not invent service/action schemas for Volvo, Zaptec, Energi Data Service, Fastmail, HACS, or Home Assistant. Verify against real entities/docs, or make the capability user-configurable.
10. Do not hard-code the user's personal entity IDs, device names, or notification targets.
11. Do not commit secrets: app passwords, API tokens, HA URLs, addresses, or personal calendar data. Sanitize all fixtures captured from the live installation.
12. Never silently alter an approved charging plan (`DESIGN.md` ADR-003).
13. Never execute charger actions from tests against a live Home Assistant instance.
14. Prefer backwards-compatible config-entry migrations once a release may be installed.
15. Treat charging as convenience automation, not a safety-critical guarantee. Surface uncertainty and failure clearly.
16. When proposing a large architectural change, update `DESIGN.md` (add an ADR) or `PLAN.md` first, before implementing it.

## Local environment

- Development workstation is **Windows**. Target Home Assistant instance is **HA OS, Core 2026.8.1**.
- HA 2026.3+ requires **Python 3.14**. The repo venv is `.venv` on 3.14; a 3.13 venv silently resolves Home Assistant back to 2026.2.x, so do not use it.
- **Home Assistant cannot be imported on Windows** (`homeassistant.runner` imports the Unix-only `fcntl`). Consequences:
  - Pure tests: `pytest -p no:homeassistant --ignore=tests/ha` — these run locally. `no:homeassistant` is the `pytest11` entry-point name, not the package name.
  - `tests/ha/` needs Linux/macOS or CI. Do not claim these passed unless they were actually executed somewhere.
  - `tests/conftest.py` must never import Home Assistant.

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
