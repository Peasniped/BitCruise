# TODO.md

Actionable implementation backlog for **BitCruise**. This is the single source of truth for work state — [PLAN.md](PLAN.md) describes phases and acceptance criteria, [DESIGN.md](DESIGN.md) describes behavior, and neither carries checkboxes.

Tick items here as they land. Add newly discovered work here rather than leaving it implicit.

Legend: `[ ]` open · `[x]` done · `[~]` in progress · `[-]` dropped (say why)

---

## Decisions already made

- [x] Repository name: `BitPusher/BitCruise`
- [x] Integration domain: `bitcruise`
- [x] Documentation split: `CLAUDE.md` (agent instructions) / `DESIGN.md` (spec) / `PLAN.md` (phases) / `TODO.md` (backlog)
- [x] Licence: MIT, copyright BitPusher.
- [x] Minimum Home Assistant version: `2026.8.0`, matching the target instance.
- [x] Python 3.14 — required by HA 2026.3 and newer. A 3.13 environment silently resolves HA back to 2026.2.x.
- [x] `integration_type: helper`, `iot_class: calculated` — BitCruise derives state from other entities rather than talking to hardware.
- [x] `single_config_entry: true` for V1, since multi-car is an explicit non-goal. Loosening this later is backwards compatible.
- [x] Test suite split into pure `tests/` and `tests/ha/` so the planner can be developed on Windows (see `DESIGN.md` §15).

## Open decisions

- [ ] Whether ready-by is a `time` entity or an option-only setting in V1.
- [ ] Default reserve floor percentage once Phase 13 lands. `0` in V1 keeps behavior unchanged; a shipped default of 30–40% would suit the reference household but changes behavior for everyone.
- [ ] Whether a reserve-floor breach should also raise the effective charge target, or only change *when* charging happens. Current design says only the timing (`DESIGN.md` §5).
- [ ] Multi-vehicle config entry model: one entry per vehicle vs. per-vehicle subentries (`DESIGN.md` §18).
- [ ] Whether the household supply limit belongs in BitCruise or should read an existing HA power sensor.
- [ ] Whether the final price interval may be partially allocated in V1, or always whole.
- [ ] Default approval policy for V1: `always_ask` vs `ask_on_change`.
- [ ] Currency handling: `Decimal` vs `float` for price and cost summation.

---

## Phase 0 — Repository bootstrap

- [x] Add `LICENSE`.
- [x] Add `.gitignore` covering Python, VS Code, HA local config, secrets, caches, virtual environments.
- [x] Add `README.md` with an early-development warning.
- [x] Add root `hacs.json`.
- [x] Create `custom_components/bitcruise/`.
- [x] Add `manifest.json` with a valid custom-integration version.
- [x] Add minimal `__init__.py` that sets up and unloads a config entry.
- [x] Add `config_flow.py` skeleton.
- [x] Add `const.py` with the domain and storage keys.
- [x] Add `strings.json` and `translations/en.json`.
- [x] Add `pyproject.toml` (lint/format/test configuration).
- [x] Add `tests/` with `__init__.py` and `conftest.py`.
- [x] Add `.github/workflows/tests.yml`.
- [x] Add `.github/workflows/hacs.yml`.
- [x] Add `.github/workflows/hassfest.yml`.
- [x] Decide and document the minimum supported Home Assistant version.
- [x] Document local VS Code development setup in the README.
- [ ] Push to GitHub so the three CI workflows actually execute. **Not yet verified — they have never run.**
- [ ] Run `tests/ha/test_config_flow.py` on Linux/macOS or CI. **Not yet verified — cannot execute on Windows.**
- [ ] Verify: config entry loads and unloads in a real HA instance.
- [ ] Submit `icon.png` to `home-assistant/brands` before the first public release, then drop `ignore: brands` from `.github/workflows/hacs.yml`.
- [ ] Typed `ConfigEntry.runtime_data` — deferred to Phase 2, when there is runtime state worth storing.

## Phase 1 — Domain model and pure charging planner

### Models (`models.py`)

- [ ] `PriceQuality` enum (`ACTUAL`, `FORECAST`).
- [ ] `PriceInterval` frozen dataclass.
- [ ] `PlanStatus` enum covering the full state model in `DESIGN.md` §4.
- [ ] `PlanSource` / proposal-reason enum.
- [ ] `PlanningInput`, including `reserve_floor_pct`.
- [ ] `ChargeRequirement`.
- [ ] `ChargeUrgency` enum (`NORMAL`, `URGENT`).
- [ ] `ChargePlan` with the fields listed in `DESIGN.md` §4, including `urgency` and `below_reserve_floor`.

### Calculations (`planner.py`)

- [ ] Deficit percentage points.
- [ ] Battery deficit kWh.
- [ ] Reserve floor deficit (`floor_deficit_pct`, `floor_deficit_kwh`) per `DESIGN.md` §5.
- [ ] Validate `reserve_floor_pct <= target_soc_pct`; surface a violation rather than reordering.
- [ ] Grid energy requirement with charging efficiency.
- [ ] Required duration at configured charging power.
- [ ] Estimated SoC after planned charge.
- [ ] Estimated charging cost.
- [ ] Report expected over-allocation when the final interval is whole.

### Optimizer

- [ ] Normalize and clip price slots to `[earliest_start, ready_by)`.
- [ ] Enumerate contiguous sequences that deliver enough energy.
- [ ] Cost each candidate (duration × power × price).
- [ ] Select the lowest cost, tie-breaking on earliest start.
- [ ] Best-effort/shortfall result when the target is unreachable.

### Planner tests

- [ ] zero deficit;
- [ ] 10% / 50% / 100% deficits;
- [ ] target below current SoC;
- [ ] fractional required interval / partial final interval;
- [ ] cheapest window at start, middle, and end of horizon;
- [ ] equal-price deterministic tie;
- [ ] window crosses midnight;
- [ ] mixed actual + forecast horizon;
- [ ] actual replaces forecast;
- [ ] price gap / missing interval;
- [ ] insufficient price horizon;
- [ ] impossible deadline;
- [ ] 15-minute prices;
- [ ] 60-minute prices;
- [ ] DST spring-forward day (23 hours);
- [ ] DST fall-back day (25 hours);
- [ ] negative prices;
- [ ] very high prices;
- [ ] charging efficiency < 1;
- [ ] numeric precision / currency summation;
- [ ] reserve floor of 0 reproduces deadline-driven behavior exactly;
- [ ] floor deficit computed correctly when SoC is above, at, and below the floor;
- [ ] floor above target is rejected.

## Phase 2 — HA source binding and visible sensors

### Config flow

- [ ] Vehicle: battery SoC entity.
- [ ] Vehicle: target SoC entity **or** fixed target percentage.
- [ ] Vehicle: usable capacity entity **or** fixed kWh.
- [ ] Vehicle: connected binary sensor/entity.
- [ ] Vehicle: optional charging-state entity.
- [ ] Vehicle: reserve floor percentage, validated against the target. Default `0` (disabled).
- [ ] Charging: fixed charging power, default 10 kW.
- [ ] Charging: efficiency, default ~90%.
- [ ] Charging: ready-by time.
- [ ] Charging: optional not-before time.
- [ ] Prices: electricity price entity.
- [ ] Prices: adapter selection (`auto` / Energi Data Service).
- [ ] Options/reconfigure flow for the adjustable settings.
- [ ] Store bindings in `ConfigEntry.data`, behavior in `ConfigEntry.options`.
- [ ] Typed `ConfigEntry.runtime_data`.

### Normalization (`source_normalization.py`)

- [ ] Track selected entity state changes (event-driven, no vendor polling).
- [ ] Normalize SoC to 0..100.
- [ ] Normalize target SoC.
- [ ] Normalize capacity (Wh → kWh).
- [ ] Normalize power (W → kW).
- [ ] Normalize connection status via configured mapping.
- [ ] Handle `unknown` / `unavailable` explicitly.

### Entities

- [ ] `sensor.charging_deficit` (%).
- [ ] `sensor.battery_energy_deficit` (kWh).
- [ ] `sensor.grid_energy_required` (kWh).
- [ ] `sensor.required_charge_duration`.
- [ ] `binary_sensor.charge_needed`.
- [ ] `sensor.plan_status`.
- [ ] One logical device per planner instance.

### Tests

- [ ] Config flow: successful setup.
- [ ] Config flow: invalid selections.
- [ ] Config flow: duplicate setup policy.
- [ ] Config flow: options change and reconfigure.
- [ ] Config flow: unload.
- [ ] Normalization unit conversions and unavailable states.

## Phase 3 — Energi Data Service + Carnot price adapter

- [ ] Capture real attributes from the user's EDS sensor.
- [ ] Freeze them as sanitized test fixtures.
- [ ] Parse today's actual prices.
- [ ] Parse tomorrow's actual prices.
- [ ] Parse Carnot forecast intervals.
- [ ] Determine when tomorrow's actual prices are valid.
- [ ] Merge sources by interval timestamp.
- [ ] Prefer actual over forecast for identical intervals.
- [ ] Mark each interval `ACTUAL` or `FORECAST`.
- [ ] Expose plan price quality: actual / forecast / mixed.
- [ ] Detect malformed or insufficient data.
- [ ] Sort intervals, reject unresolved overlaps, detect gaps, preserve timezone.
- [ ] Replanning triggers for SoC, target, capacity/power, ready-by, connection, prices, forecast, tomorrow-valid.
- [ ] Debounce rapid changes.
- [ ] Tests against the frozen fixtures.

## Phase 4 — Proposal/approval state machine

- [ ] Persisted state record: current proposal, approved plan, plan IDs/revisions, proposal reason, approval status, execution markers.
- [ ] `storage.py` using HA storage helpers.
- [ ] `button.accept_plan`.
- [ ] `button.reject_plan`.
- [ ] `button.recalculate_plan`.
- [ ] `switch.smart_charging`.
- [ ] Sensors: proposed start/end, approved start/end, estimated cost, plan status.
- [ ] `binary_sensor.plan_requires_approval`.
- [ ] Rule: new plan with no approved plan → proposal.
- [ ] Rule: accept → approved atomically.
- [ ] Rule: reject → proposal cleared.
- [ ] Rule: equivalent replan → keep approved plan silently.
- [ ] Rule: materially changed replan → stage replacement, keep approved plan active.
- [ ] Rule: accept move → atomic replacement.
- [ ] Rule: keep old plan → discard replacement.
- [ ] Configurable "materially changed" threshold, default one price interval.
- [ ] State machine tests (`DESIGN.md` §14).

## Phase 5 — Notifications

- [ ] Optional notification target/action in config.
- [ ] Warning offset setting, default 15 minutes.
- [ ] Initial proposal notification.
- [ ] Actual-prices-published / move-plan notification.
- [ ] Car-not-connected notification before start.
- [ ] Impossible-target notification.
- [ ] Charger-action-failed notification.
- [ ] Optional completion summary.
- [ ] Actionable buttons call the same integration actions as dashboard controls.
- [ ] Verify the integration is fully operable with no notification target configured.

## Phase 6 — Charger execution

- [ ] Config: charger connected/status entity.
- [ ] Config: authorization-state entity.
- [ ] Config: authorize action/button.
- [ ] Config: start/resume action/button/switch.
- [ ] Config: stop/pause action/button/switch.
- [ ] Config: optional charging power and charging state sensors.
- [ ] Start flow with all preconditions in `DESIGN.md` §9.
- [ ] Skip actions that state information proves unnecessary.
- [ ] Verify resulting charger state when a status sensor exists.
- [ ] End flow: stop/pause if configured, mark completed.
- [ ] Late connection: start immediately if enough window remains.
- [ ] Late connection: recalculate achievable SoC; do not extend past approved end.
- [ ] Failure handling: authorization failure, start failure, charger unavailable, disconnect mid-charge, external stop, restart mid-session.
- [ ] Document idempotency limitations where state is unavailable.
- [ ] `binary_sensor.ready_to_charge`.
- [ ] Execution tests (no live HA charger calls).

## Phase 7 — Restart recovery and robustness

- [ ] Persist approved/proposed plan state across restart.
- [ ] Re-register future callbacks on startup.
- [ ] Reconcile when startup falls inside an approved window.
- [ ] Reconcile when startup falls after approved end.
- [ ] Idempotency markers to avoid double authorize/start/stop.
- [ ] Tolerate selected entities unavailable during HA startup.
- [ ] Re-evaluate when source entities recover.
- [ ] Repairs issues for broken entity selections.
- [ ] Diagnostics output with private data redacted.
- [ ] Restart-recovery tests for each timing case in `PLAN.md` Phase 7.

## Phase 8 — First HACS-quality release

- [ ] README installation instructions.
- [ ] Document required/supported source entities.
- [ ] Screenshots of config flow and entities.
- [ ] Troubleshooting section.
- [ ] Diagnostics instructions.
- [ ] Changelog / release notes.
- [ ] `manifest.json` version matches the release tag.
- [ ] HACS validation passes.
- [ ] Hassfest passes.
- [ ] Test suite passes.
- [ ] Clean install tested through a HACS custom repository.
- [ ] Upgrade from a previous version tested.
- [ ] Document backup/recovery implications.

---

## Future phases (not scheduled)

### Phase 9 — Calendar booking input

- [ ] Optional car-booking calendar entity in config.
- [ ] Look-ahead horizon, parsing mode, consumption kWh/100 km, reserve SoC, normal target, max target, trip-prep approval policy.
- [ ] Structured event-description parsing (`distance_km`, `return`, `reserve_soc`, optional fields).
- [ ] Load events over the horizon and normalize to `CarBooking`.
- [ ] Detect overlaps.
- [ ] Determine next required departure time.
- [ ] Derive required departure SoC and feed target/deadline into the planner.

### Phase 10 — Trip energy planning

- [ ] `trip_energy.py` with the base model.
- [ ] Outputs: trip energy, minimum/recommended departure SoC, fits-in-one-charge, expected arrival SoC, `intermediate_charge_required`.
- [ ] Long-trip target policy including cap, approval, and restore.
- [ ] Only set the vehicle target when a writable actuator is configured; otherwise notify.

### Phase 11 — Booking conflict decisions

- [ ] Pure `booking_policy.py` returning `BookingDecision`.
- [ ] Deterministic initial policy (accept / decline / needs-review).
- [ ] Fixture-based tests with no network access.

### Phase 12 — Fastmail invitation RSVP adapter

- [ ] Investigate whether the selected HA calendar integration exposes RSVP.
- [ ] Choose Option A (in-repo adapter) or Option B (companion integration).
- [ ] Implement the chosen transport behind an adapter interface.
- [ ] Privacy review: minimal permissions, no invitation bodies logged, credentials via HA config entries.

### Phase 13 — Urgency-aware planning for unplanned trips

Depends on Phases 1, 2, 4. See `DESIGN.md` §6.4 and ADR-007.

- [ ] Urgency-aware search window: start from "as soon as connected" rather than gating on ready-by.
- [ ] Objective switch: restore the floor at the earliest opportunity, cheapest-first only among non-delaying intervals.
- [ ] Two-segment plans: urgent portion up to the floor, then `NORMAL` optimization up to the target.
- [ ] Recompute urgency on every SoC change; a drop through the floor produces an `URGENT` plan.
- [ ] Urgent replan when the car reconnects below the floor outside any approved window.
- [ ] `auto_approve_urgent` setting, default enabled.
- [ ] Guarantee: urgent charging never cancels, shortens, or moves an approved plan; it is added before it.
- [ ] Approved plan invalidated by an unexpected SoC drop stages a replacement proposal rather than extending itself.
- [ ] `sensor.reserve_floor_deficit` and urgency exposed on plan status.
- [ ] Notification case: charging urgently because the car is below the reserve floor.
- [ ] Tests: floor breach while idle; breach mid-window; reconnect below floor; urgent plan overlapping an approved plan; `auto_approve_urgent` disabled.

### Phase 14 — Planned distance calendar

Depends on Phases 9 and 10. See `DESIGN.md` §17.

- [ ] Aggregate `CarBooking` distances into local-day totals, DST-correct.
- [ ] Apply return-trip doubling.
- [ ] Attribute multi-day bookings to the start day; make even distribution a configurable alternative.
- [ ] Represent unknown distance as `None` and mark the day incomplete — never `0`.
- [ ] Sum overlapping bookings without resolving conflicts.
- [ ] Expose `sensor.planned_distance_today` with a per-day breakdown attribute.
- [ ] Flag days whose planned distance exceeds usable range.
- [ ] Label the output as planned distance only, so it is not read as expected SoC drain.
- [ ] Evaluate a BitCruise-provided `calendar` entity only after the sensor proves useful.
- [ ] Tests: DST days, multi-day bookings, missing distance, overlapping bookings, over-range days.

### Phase 15 — Multiple vehicles and shared-resource coordination

Depends on Phases 1–7, and should follow Phase 13. See `DESIGN.md` §18 and ADR-008.

- [ ] Verify the current Home Assistant config subentry APIs against official docs.
- [ ] Choose the entry model: one entry per vehicle, or one entry with per-vehicle subentries.
- [ ] Allocation layer consuming per-vehicle requirements, not finished windows.
- [ ] Constraint: no charger double-booked.
- [ ] Constraint: concurrent draw within a configured household supply limit.
- [ ] Deterministic, configurable priority rule; expose which rule resolved a collision.
- [ ] Reserve-floor breaches outrank `NORMAL` demand across vehicles.
- [ ] Relax `single_config_entry` in `manifest.json`; migrate existing single-vehicle entries.
- [ ] Add a vehicle identifier to `CarBooking` and the booking convention.
- [ ] Make the planned distance calendar per-vehicle with an optional household total.
- [ ] Tests: two cars one charger; two cars two chargers over the supply limit; priority collisions; migration from a single-vehicle entry.

---

## Later backlog (unordered)

- [ ] Split charging across non-contiguous cheapest intervals.
- [ ] Price ceiling / "never charge above X unless required".
- [ ] Negative-price preference.
- [ ] Solar forecast / surplus charging.
- [ ] Dynamic charger current based on house load.
- [ ] Multiple EVs sharing one connection limit.
- [ ] Multiple chargers.
- [ ] Historical actual charging power learning.
- [ ] Learned wall-to-battery efficiency.
- [ ] Charger session energy reconciliation.
- [ ] Completion detection from SoC target rather than schedule end.
- [ ] Configurable tariff/tax components.
- [ ] Custom Lovelace card.
- [ ] Calendar UI helpers.
- [ ] Route provider adapter.
- [ ] DC fast-charge stop planning.
- [ ] Household priority rules.
- [ ] Vacation / away mode.
- [ ] `select.approval_policy` entity (`always_ask`, `ask_on_change`, `automatic`).
