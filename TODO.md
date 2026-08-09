# TODO.md

Actionable implementation backlog for **BitCruise**. This is the single source of truth for work state — [PLAN.md](PLAN.md) describes phases and acceptance criteria, [DESIGN.md](DESIGN.md) describes behavior, and neither carries checkboxes.

Tick items here as they land. Add newly discovered work here rather than leaving it implicit.

Legend: `[ ]` open · `[x]` done · `[~]` in progress · `[-]` dropped (say why)

---

## Decisions already made

- [x] Repository name: `Peasniped/BitCruise`. Early drafts said `BitPusher/BitCruise`; no such org exists.
- [x] Integration domain: `bitcruise`
- [x] Documentation split: `CLAUDE.md` (agent instructions) / `DESIGN.md` (spec) / `PLAN.md` (phases) / `TODO.md` (backlog)
- [x] Licence: MIT, copyright Peasniped. Change to a legal name if the project is ever distributed formally.
- [x] Minimum Home Assistant version: `2026.8.0`, matching the target instance.
- [x] Python 3.14 — required by HA 2026.3 and newer. A 3.13 environment silently resolves HA back to 2026.2.x.
- [x] `integration_type: service`, `iot_class: calculated`. `helper` was tried first and is wrong: it files the integration under the Helpers tab, and the Helpers UI opens an options flow on click, so an integration without one fails with "Invalid handler specified" (home-assistant/frontend#15044). `helper` is for automation aids such as input booleans and derivatives; the calculated nature is already carried by `iot_class`. Covered by a regression test in `tests/test_manifest.py`.
- [x] `single_config_entry: true` for V1, since multi-car is an explicit non-goal. Loosening this later is backwards compatible.
- [x] Test suite split into pure `tests/` and `tests/ha/` so the planner can be developed on Windows (see `DESIGN.md` §15).
- [x] Currency is `Decimal`; energy, power and SoC stay `float`. Conversion happens only at the price × energy boundary, so repeated cost addition is exact.
- [x] Whole price intervals are allocated, with the slack reported as `over_allocation_kwh`. Cost is charged on the energy actually expected to be drawn, since the car stops at target rather than running the window out.

## Open decisions

- [ ] Whether ready-by is a `time` entity or an option-only setting in V1.
- [ ] Default reserve floor percentage once Phase 9 lands. `0` in V1 keeps behavior unchanged; a shipped default of 30–40% would suit the reference household but changes behavior for everyone.
- [ ] Whether a reserve-floor breach should also raise the effective charge target, or only change *when* charging happens. Current design says only the timing (`DESIGN.md` §5).
- [ ] Multi-vehicle config entry model: one entry per vehicle vs. per-vehicle subentries (`DESIGN.md` §18).
- [ ] Whether the household supply limit belongs in BitCruise or should read an existing HA power sensor.
- [ ] Whether the final price interval may be partially allocated in V1, or always whole.
- [ ] Default approval policy for V1: `always_ask` vs `ask_on_change`.

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
- [x] Push to GitHub so the three CI workflows actually execute. Pushed to `Peasniped/BitCruise` at `a28ff7b`.
- [x] Run `tests/ha/test_config_flow.py` on Linux/macOS or CI. Passed in CI on Python 3.14 — first execution; they cannot run on Windows.
- [x] Hassfest validation passes.
- [x] HACS validation passes. It initially failed because repository topics were unset; topics were added on GitHub and the check went green. Note the `home-as` topic looks like a truncated `home-assistant` — harmless for validation, worth fixing for discoverability.
- [x] Verify: config entry loads and unloads in a real HA instance. Confirmed on HA OS 2026.8.1 — integration discoverable, config flow completes, second entry rejected, entry deletes cleanly with no restart and no log output. This test is what caught the `integration_type` bug.
- [ ] Submit `icon.png` to `home-assistant/brands` before the first public release, then drop `ignore: brands` from `.github/workflows/hacs.yml`.
- [ ] Typed `ConfigEntry.runtime_data` — deferred to Phase 2, when there is runtime state worth storing.

## Phase 1 — Domain model and pure charging planner

### Models (`models.py`)

- [x] `PriceQuality` enum (`ACTUAL`, `FORECAST`).
- [x] `PriceInterval` frozen dataclass.
- [x] `PlanStatus` enum covering the full state model in `DESIGN.md` §4.
- [x] `PlanSource` / proposal-reason enum.
- [x] `PlanningInput`, including `reserve_floor_pct`.
- [x] `ChargeRequirement`.
- [x] `ChargeUrgency` enum (`NORMAL`, `URGENT`).
- [x] `ChargePlan` with the fields listed in `DESIGN.md` §4, including `urgency` and `below_reserve_floor`.
- [x] `PlanPriceQuality` enum (`ACTUAL`, `FORECAST`, `MIXED`) — plan-level aggregate, distinct from per-interval `PriceQuality`.
- [x] `to_utc` / `elapsed_hours` helpers. **Every** datetime comparison and duration must go through these; see the DST note below.

### Calculations (`planner.py`)

- [x] Deficit percentage points.
- [x] Battery deficit kWh.
- [x] Reserve floor deficit (`floor_deficit_pct`, `floor_deficit_kwh`) per `DESIGN.md` §5.
- [x] Validate `reserve_floor_pct <= target_soc_pct`; surface a violation rather than reordering.
- [x] Grid energy requirement with charging efficiency.
- [x] Required duration at configured charging power.
- [x] Estimated SoC after planned charge.
- [x] Estimated charging cost.
- [x] Report expected over-allocation when the final interval is whole (`allocated_grid_kwh`, `over_allocation_kwh`).

### Optimizer

- [x] Normalize and clip price slots to `[earliest_start, ready_by)`.
- [x] Enumerate contiguous sequences that deliver enough energy.
- [x] Cost each candidate (duration × power × price).
- [x] Select the lowest cost, tie-breaking on earliest start.
- [x] Best-effort/shortfall result when the target is unreachable.
- [x] Reject overlapping intervals; treat gaps as breaking contiguity.
- [x] Deterministic plan `id` derived from content, so a recalculation that changes nothing is recognisable as unchanged.

### Planner tests

96 pure tests, all passing on Windows via `pytest -p no:homeassistant --ignore=tests/ha`.

- [x] zero deficit;
- [x] 10% / 50% / 100% deficits;
- [x] target below current SoC;
- [x] fractional required interval / partial final interval;
- [x] cheapest window at start, middle, and end of horizon;
- [x] equal-price deterministic tie;
- [x] window crosses midnight;
- [x] mixed actual + forecast horizon;
- [ ] actual replaces forecast — belongs to the price adapter, moved to Phase 3. The planner only sees already-merged intervals.
- [x] price gap / missing interval;
- [x] insufficient price horizon;
- [x] impossible deadline;
- [x] 15-minute prices;
- [x] 60-minute prices;
- [x] DST spring-forward day (23 hours);
- [x] DST fall-back day (25 hours);
- [x] negative prices;
- [x] very high prices;
- [x] charging efficiency < 1;
- [x] numeric precision / currency summation;
- [x] reserve floor of 0 reproduces deadline-driven behavior exactly;
- [x] floor deficit computed correctly when SoC is above, at, and below the floor;
- [x] floor above target is rejected;
- [x] unsorted input is sorted; overlapping input is rejected;
- [x] identical inputs produce an identical plan id.

### DST: the trap this phase uncovered

Python does **wall-clock** arithmetic when two aware datetimes share a `tzinfo`,
which is wrong twice a year and was wrong in the first draft of the planner:

- `end - start` for 01:00→03:00 on a spring-forward day returned 2 hours when only
  1 hour elapses, so the planner would have believed it could deliver twice the
  energy it actually can.
- `moment + timedelta(hours=1)` returns a wall-clock time, which on a
  spring-forward day is 02:00 — an instant that does not exist.
- The repeated 02:00 hour on a fall-back day makes two genuinely different
  instants compare *equal*, breaking sorting and overlap detection.

Fixed by routing every comparison, duration and ordering through `to_utc`.
Regression tests live in `tests/test_models.py::TestDaylightSavingArithmetic`.
**Any new datetime arithmetic in this project must go through `to_utc` or
`elapsed_hours`.**

## Reference installation

Real entity names, shapes and traps from the development installation are recorded in
[`docs/reference-installation.md`](docs/reference-installation.md), with the price
snapshot in `tests/fixtures/energidataservice.json`. Read it before writing anything
that reads an entity. Highlights that change the plan:

- [ ] Charge target is **readable but not writable** (`sensor.volvo_xc40_target_battery_charge_level`, no `number`). Future trip preparation must notify rather than set it.
- [ ] Plug status is an **enum `sensor`** with a third `fault` state, not a `binary_sensor`.
- [ ] `charging_limit` is **amps**, not a target percentage.
- [ ] The target sensor has **no `device_class`**, so a picker filtered on `device_class: battery` will not show it.
- [ ] `car_connection` (`no_internet`, `power_saving_mode`) is a **staleness signal** — treat as an unsafe-input gate per `DESIGN.md` §3.4.
- [ ] Capacity is discoverable (`81.608 kWh`), so manual entry is an override.
- [ ] Charger control entities are **`unavailable` while unplugged** — that is "cannot act yet", not an error.
- [ ] Real charger capability is **16 A three-phase ≈ 11 kW**, not 10 kW.
- [ ] Measured consumption exists (`17.9 kWh/100km`) — the trip model can read it.
- [x] **Resolved:** EDS applies tariffs and surcharges itself, so the exposed price is the true all-in price per kWh. Never add the `tariffs` attribute to it — that double-counts and inflates cost by ~40%.

## Phase 2 — HA source binding and visible sensors

### Config flow

- [x] Vehicle: battery SoC entity. The only picker filtered on `device_class`, because it is the only one where it is reliably set.
- [x] Vehicle: target SoC entity **or** fixed target percentage.
- [x] Vehicle: usable capacity entity **or** fixed kWh.
- [x] Vehicle: connected entity, accepting an enum `sensor` as well as a `binary_sensor`. `fault` reports `unknown` rather than `off`, so a charging fault is not hidden as "not plugged in".
- [x] Vehicle: availability entity for the staleness gate.
- [x] Vehicle: reserve floor percentage, validated against the target.
- [x] Charging: fixed charging power, default 11 kW (16 A three-phase).
- [x] Charging: efficiency, default 90%.
- [x] Charging: ready-by time.
- [x] Charging: optional not-before time.
- [x] Prices: electricity price entity, filtered on `device_class: monetary`. Not yet read — Phase 3.
- [ ] Prices: adapter selection (`auto` / Energi Data Service) — Phase 3, when there is more than one adapter to choose between.
- [x] Options flow for the adjustable settings; the entry reloads so changes apply immediately.
- [x] Store bindings in `ConfigEntry.data`, behavior in `ConfigEntry.options`.
- [x] Typed `ConfigEntry.runtime_data` via `BitCruiseConfigEntry`.
- [x] Reconfigure flow for changing the source entities without deleting the entry, pre-filled with the current selections.
- [x] Omit the fixed target and capacity fields when the matching entity is selected — they were dead inputs the entity always overrode.
- [x] Reject a target entity not measured in `%`, catching a charging *current* limit picked by mistake.
- [x] An invalid reserve floor is clamped and reported rather than blanking every sensor.

### Normalization (`source_normalization.py`)

The module is pure — no Home Assistant import — so it runs on Windows. The coordinator
reads `hass.states` and passes raw values in.

- [x] Track selected entity state changes (event-driven, no polling at all).
- [x] Normalize SoC and target to 0..100, rejecting out-of-range values.
- [x] Normalize capacity (Wh / kWh / MWh → kWh). An absent or unknown unit is refused rather than assumed.
- [x] Normalize power (W / kW / MW → kW).
- [x] Normalize connection status, including the enum-sensor form and `fault`.
- [x] Staleness gate from the availability entity (`no_internet`, `power_saving_mode`, `ota_installation_in_progress`).
- [x] Handle `unknown` / `unavailable` explicitly, naming the offending entity in the problem.

### Entities

- [x] `sensor.charging_deficit` (%).
- [x] `sensor.battery_energy_deficit` (kWh).
- [x] `sensor.grid_energy_required` (kWh).
- [x] `sensor.required_charge_duration` (hours).
- [x] `sensor.reserve_floor_deficit` (kWh, diagnostic, disabled by default).
- [x] `sensor.plan_status`, carrying the diagnosis — problems, plug status, freshness, resolved inputs, ready-by — as attributes.
- [x] `binary_sensor.charge_needed`.
- [x] `binary_sensor.vehicle_connected`.
- [x] One logical device per planner instance, `entry_type: service`, not claiming the vehicle or charger devices.

### Tests

38 tests for this phase: 23 pure normalization, 15 requiring Home Assistant.

- [x] Config flow: successful two-step setup.
- [x] Config flow: capacity required without an entity; floor above target rejected.
- [x] Config flow: a selected target entity removes the fixed-target requirement.
- [x] Config flow: duplicate setup policy.
- [x] Config flow: options change.
- [x] Config flow: unload.
- [x] Normalization unit conversions and unavailable states.
- [x] Entities: deficit figures against the real reference numbers.
- [x] Entities: recomputation on a source state change.
- [x] Entities: unavailable source reports `error` and names the entity.
- [x] Entities: stale vehicle data flagged.
- [x] Entities: plug `fault` reports `unknown`, not `off`.
- [x] Config flow: reconfigure changes sources and keeps settings.
- [x] Config flow: fixed fields hidden when the matching entity is selected, shown when it is not.
- [x] Config flow: a target measured in amps is rejected.
- [x] Entities: a bad reserve floor does not blank the deficit figures.

## Phase 3 — Energi Data Service + Carnot price adapter

- [ ] Build fixtures from the Energi Data Service integration's published attribute schema — do **not** ask the user to paste their entity state. The user picks an entity; adapters work out how to read it (`DESIGN.md` §12).
- [ ] Normalize the `unit` attribute (`MWh` / `kWh` / `Wh`). EDS can report per MWh, which would make every cost wrong by 1000×.
- [ ] Carry `currency` through to the cost sensor rather than assuming DKK.
- [ ] Auto-detect across known conventions; fail loudly with an actionable error when none match.
- [ ] Expose what was parsed (source, interval count, actual/forecast mix) so correctness is confirmed by reading a sensor.
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

### Cheap power alert (needs Phase 3 prices; independent of the car)

- [ ] `number.cheap_price_threshold`, in the **units of the selected price entity** — `0.50` for `DKK/kWh`, `50` for øre when `use_cent` is set. Show the entity's unit in the UI; reinterpret if the user later picks a price entity with a different unit.
- [ ] Group contiguous below-threshold intervals into a single window; notify **once per window**, not per interval.
- [ ] Identify a window by its start instant so a refreshed price curve does not re-notify for the same window.
- [ ] At most one update when a known window materially changes shape; never notify for a window already in the past.
- [ ] Configurable lead time, so the alert arrives while it is still actionable.
- [ ] Distinct message for negative prices ("you are paid to use power").
- [ ] `binary_sensor.cheap_power` — on while the current price is below the threshold.
- [ ] `sensor.next_cheap_period` — next window start, with end, duration, min and mean price as attributes.
- [ ] Both entities report `unknown`, not `off`, when no price data exists — "none coming" and "we don't know" are different claims.
- [ ] Not gated on `switch.smart_charging`, plug status, or whether charging is needed.
- [ ] Tests: window grouping; no re-notify on curve refresh; window extended; window passed; negative-price tier; threshold in øre vs DKK; no price data.

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

## Phase 8 — Daily charging for battery health

A battery ages faster sitting at a high state of charge, so holding a modest daily
level should be the default. See `DESIGN.md` §6.5 and §17.

### Daily commute requirement

- [ ] One-way commute distance setting, doubled for the return leg. Label it "one way" unmistakably — entering the round trip gives a target twice too high, and nothing about the result looks wrong.
- [ ] Read consumption from the vehicle where exposed (`17.9 kWh/100km` on the reference installation); configurable otherwise.
- [ ] Configurable margin on consumption: the measured figure is a past average and winter is materially worse.
- [ ] Add the reserve floor on top rather than comparing against it — arriving home at exactly the floor means the commute consumed everything spare.
- [ ] `sensor.commute_energy_required` (kWh) and the state of charge that covers the day.
- [ ] `binary_sensor.commute_covered` — whether the configured target covers a return trip with reserve intact.
- [ ] Advise only: the vehicle target is read-only, so report the figure and let the user set it.
- [ ] Put it in `trip_energy.py` — a commute is a trip that repeats and needs no booking.
- [ ] Tests: one-way doubling; consumption from entity vs configured; floor added; target too low; margin applied.

### Just-in-time finishing

- [ ] Cost tolerance defining "about the same", so a genuinely cheaper early window is never traded away for battery health.
- [ ] Among windows within tolerance, prefer the latest finish.
- [ ] Safety buffer before the deadline, configurable, default about 45 minutes.
- [ ] Drop the buffer rather than the charge when nothing fits, and report that it happened.
- [ ] Reverse the tie-break to latest start; determinism is preserved, only the direction changes.
- [ ] Tests: equal prices pick the latest; a cheaper early window still wins outside tolerance; buffer respected; buffer dropped when infeasible; determinism holds.

### Optional deadline

- [ ] Make ready-by optional in the config flow.
- [ ] With no deadline, plan on price and the reserve floor alone across the known horizon.
- [ ] Never invent a deadline to fill the gap.
- [ ] Tests: no deadline still charges; no deadline and no floor does nothing rather than charging arbitrarily.

## Phase 9 — Urgency-aware planning for unplanned trips

Depends on Phases 1, 2, 4. See `DESIGN.md` §6.4 and ADR-007.

- [ ] Urgency-aware search window: start from "as soon as connected" rather than gating on ready-by.
- [ ] Objective switch: restore the floor at the earliest opportunity, cheapest-first only among non-delaying intervals.
- [ ] Two-segment plans: urgent portion up to the floor, then `NORMAL` optimization up to the target.
- [ ] Recompute urgency on every SoC change; a drop through the floor produces an `URGENT` plan.
- [ ] Urgent replan when the car reconnects below the floor outside any approved window.
- [ ] `auto_approve_urgent` setting, default enabled.
- [ ] Guarantee: urgent charging never cancels, shortens, or moves an approved plan; it is added before it.
- [ ] Approved plan invalidated by an unexpected SoC drop stages a replacement proposal rather than extending itself.
- [ ] Notification case: charging urgently because the car is below the reserve floor.
- [ ] Tests: floor breach while idle; breach mid-window; reconnect below floor; urgent plan overlapping an approved plan; `auto_approve_urgent` disabled.

---

## Future phases (not scheduled)

### Phase 10 — Calendar booking input

- [ ] Optional car-booking calendar entity in config.
- [ ] Look-ahead horizon, parsing mode, consumption kWh/100 km, reserve SoC, normal target, max target, trip-prep approval policy.
- [ ] Structured event-description parsing (`distance_km`, `return`, `reserve_soc`, optional fields).
- [ ] Load events over the horizon and normalize to `CarBooking`.
- [ ] Detect overlaps.
- [ ] Determine next required departure time.
- [ ] Derive required departure SoC and feed target/deadline into the planner.

### Phase 11 — Trip energy planning

- [ ] `trip_energy.py` with the base model.
- [ ] Outputs: trip energy, minimum/recommended departure SoC, fits-in-one-charge, expected arrival SoC, `intermediate_charge_required`.
- [ ] Capture and persist `normal_target_soc` **before** the first raise is proposed. Without it there is nothing to tell the user to restore to, and a raised target would be mistaken for normal.
- [ ] Prompt to raise the target to **100%** when a trip needs more than the current target and no writable actuator exists. 100% rather than the exact requirement: consumption estimates carry real error, and partial values are fiddly to set in a vehicle app.
- [ ] Skip the prompt when the target already meets the requirement.
- [ ] Set the target directly only when a writable actuator is configured, subject to the approval policy.
- [ ] Watch the target sensor after prompting: satisfied / not raised in time / raised partially. Never imply the trip is covered merely because a prompt was sent — keep `can_meet_target` and `estimated_soc_at_departure` honest.
- [ ] `binary_sensor.charge_target_raised`, on while the target exceeds `normal_target_soc`. This carries the state; a notification alone would be missed and a repeated one would nag.
- [ ] One notification when the trip ends, naming the value to restore ("still 100%, set it back to 90%").
- [ ] Clear the raised state when the target returns to `normal_target_soc` or below. A different lower value becomes the new normal rather than an error (ADR-003).
- [ ] Automatic restore only where a writable actuator exists, and only behind a policy setting — silently lowering a deliberately raised target is its own surprise.
- [ ] Tests: prompt issued / skipped when already high enough; user raises in time; user never raises; user raises partially; restore reminder fires once; state clears on manual restore; user sets a new lower normal.

### Phase 12 — Booking conflict decisions

Producing the decision is in scope; replying to the invitation is not. A separate
project owns that (ADR-005).

- [ ] Pure `booking_policy.py` returning `BookingDecision`.
- [ ] Deterministic initial policy (accept / decline / needs-review).
- [ ] Expose the decision so another project can act on it.
- [ ] Fixture-based tests with no network access and no provider-specific code.

### Phase 13 — Planned distance calendar

Depends on Phases 10 and 11. See `DESIGN.md` §17.

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

### Phase 14 — First HACS-quality release

Deliberately after the calendar and distance work: once other people install it, every
configuration change needs a migration path, so the schema should stop moving first.

- [ ] README installation instructions.
- [ ] Document required/supported source entities.
- [ ] Screenshots of config flow and entities.
- [ ] Troubleshooting section.
- [ ] Diagnostics instructions.
- [ ] Changelog / release notes.
- [ ] `manifest.json` version matches the release tag.
- [ ] Submit `icon.png` to `home-assistant/brands`, then drop `ignore: brands` from the HACS workflow.
- [ ] HACS validation, Hassfest and the test suite all pass.
- [ ] Clean install tested through a HACS custom repository.
- [ ] Upgrade from a previous version tested.
- [ ] Document backup/recovery implications.

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
