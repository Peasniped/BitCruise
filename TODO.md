# TODO.md

Outstanding work for **BitCruise**, and nothing else. [PLAN.md](PLAN.md) describes phases
and acceptance criteria and carries the phase status table; [DESIGN.md](DESIGN.md)
describes behavior.

**Finished work is removed from this file rather than ticked.** Git history records what
was done; `PLAN.md` records which phases are complete. Anything learned along the way
that should change how the next change is written belongs in `CLAUDE.md` under "Traps
this project has already fallen into", where it is read every session — not in a
completed checkbox nobody scrolls back to.

Add newly discovered work here rather than leaving it implicit. Legend: `[ ]` open ·
`[~]` in progress · `[-]` dropped (say why).

---

## Open decisions

- [ ] Whether ready-by is a `time` entity or an option-only setting in V1.
- [ ] Default reserve floor percentage once Phase 9 lands. `0` in V1 keeps behavior unchanged; a shipped default of 30–40% would suit the reference household but changes behavior for everyone.
- [ ] Whether a reserve-floor breach should also raise the effective charge target, or only change *when* charging happens. Current design says only the timing (`DESIGN.md` §5).
- [ ] Multi-vehicle config entry model: one entry per vehicle vs. per-vehicle subentries (`DESIGN.md` §18).
- [ ] Whether the household supply limit belongs in BitCruise or should read an existing HA power sensor.
- [ ] Whether the final price interval may be partially allocated in V1, or always whole.

---

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

## Presentation and approval UX

Not a numbered phase; a pass over what the integration *looks like*, sitting ahead of
Phase 5 because a notification should announce a coherent state rather than one the
user has to assemble from eight entities. Raised after the first real session on the
reference installation: the integration was working correctly and still felt poor to
use.

### Too many entities, no overview

- [ ] Group the working-out under `EntityCategory.DIAGNOSTIC` — `battery_energy_deficit`, `grid_energy_required`, `required_charge_duration`. They explain a number rather than answering a question, and HA already has a place for that. Keep them enabled; this is about where they appear, not whether they exist.
- [ ] `sensor.bitcruise_summary`: one readable sentence, e.g. "Charging 02:00–06:00 tonight, 33.5 kWh for 53.83 DKK". Mind the 255-character state limit, and keep it translatable — it is a user-facing string, not a log line.
- [ ] Decide what the summary says in each state: idle, needs charge but no window, awaiting approval, approved, error. The error case should name the first problem rather than saying "error".
- [ ] Display precision across every sensor. `estimated_cost` reads `53.833372711111111884` and `required_charge_duration` reads `3.04999595959596`. Currency is `Decimal` internally on purpose, but that precision has done its job before a person sees it.
- [ ] Review whether `proposed_start` / `proposed_end` earn their place, given they read `unknown` whenever nothing is pending — which under `ask_on_change` is nearly always.

### The approval flow itself

- [ ] Make `button.accept_plan` and `button.reject_plan` unavailable when no proposal is pending. **This reverses a decision made in Phase 4**, where they were left always-available so a notification action could not fail. That reasoning was backwards: a notification is only sent while a proposal is live, and a stale tap failing visibly beats it silently doing nothing. Greyed-out buttons are also the clearest available signal that nothing wants your input.
- [ ] Re-check that Phase 5's notification actions still behave sensibly once the buttons can be unavailable — that is the constraint the original decision was protecting.
- [ ] Consider surfacing *why* approval is being asked for in the proposal itself, not only as a `plan_status` attribute.

### Later

- [ ] Custom Lovelace card. Still unordered in the backlog below; the work above should make it less necessary rather than more.

## Phase 5 — Notifications

- [ ] Optional notification target/action in config.
- [ ] Warning offset setting, default 15 minutes.
- [ ] Debounce notifications, not recomputation (`DESIGN.md` §6). Rate-limit at the point a message would be sent; a coordinator-level debouncer was tried in Phase 3 and made restart recovery worse for no gain.
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

