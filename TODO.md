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

**Phase numbers are identity, not sequence.** They are referenced from `DESIGN.md`, the
ADRs and `CLAUDE.md`, so they do not get renumbered when priorities move. This file is
written in delivery order; the numbers are just names.

---

## Delivery order

| # | Work | Phase |
| --- | --- | --- |
| 1 | Charger execution, reporting only | 6a |
| 2 | Charger execution, acting | 6b |
| 3 | Reserve floor becomes active | 9 |
| 4 | Daily commute requirement | 8a |
| 5 | Multi-day price awareness | 16 |
| 6 | Optional deadline | 8c |
| 7 | Notification plumbing and the critical cases | 5a |
| 8 | Cheap power alert | 5b |
| 9 | Remaining notification cases | 5c |
| 10 | Calendar booking input | 10 |
| 11 | Trip energy planning | 11 |
| 12 | Planned distance calendar | 13 |
| 13 | Repairs and diagnostics | 7 |
| 14 | First HACS-quality release | 14 |
| 15 | Multiple vehicles | 15 |

Why this order:

- **Nothing else matters until it charges the car.** Execution is the value unlock;
  everything before it is an integration that tells you things you then act on by hand.
- **The floor and the commute figure come next because multi-day planning needs both.**
  Deferring charging to tomorrow means knowing what tomorrow morning actually demands.
  With a 90% target and no floor there is nothing to defer.
- **Multi-day beats just-in-time finishing**, which is demoted to the backlog. Sitting
  at 80-90% for a few days does not meaningfully age a battery; sitting at 100%, hot,
  does. So the reason to delay *within* a night is weak, while the reason to shift a
  *day* is worth real money. The two also pull against each other: if tomorrow 04:00 is
  half tonight's price, finishing just in time for tomorrow's 07:00 deadline optimises
  the wrong axis.
- **Phase 7 mostly already happened.** Phases 3 and 4 absorbed the persistence,
  callback re-registration, expiry and unavailable-source handling. What remains that
  is *about execution* belongs inside 6b, not after it: a version that can double-start
  a charger should never exist, even briefly. Repairs and diagnostics are all that is
  left of Phase 7 as a separate piece.

---

## Open decisions

- [ ] Whether ready-by is a `time` entity or an option-only setting in V1.
- [ ] Default reserve floor percentage once Phase 9 lands. `0` today keeps behavior unchanged; a shipped default of 30–40% would suit the reference household but changes behavior for everyone.
- [ ] Whether a reserve-floor breach should also raise the effective charge target, or only change *when* charging happens. Current design says only the timing (`DESIGN.md` §5).
- [ ] Multi-vehicle config entry model: one entry per vehicle vs. per-vehicle subentries (`DESIGN.md` §18).
- [ ] Whether the household supply limit belongs in BitCruise or should read an existing HA power sensor.
- [ ] Whether the final price interval may be partially allocated in V1, or always whole.
- [ ] How far a multi-day deferral may chain before it must charge regardless (see 16).

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
- [ ] Range is live and temperature-aware (`distance_to_empty_battery`) — the trip model prefers it over any kWh/100km figure.

---

## 1. Phase 6a — Charger execution, reporting only

Everything execution needs *except* firing an action. Run it on the real Zaptec for a
few nights and check it would have done the right thing at 02:00. The one phase that
can physically do something wrong to a car is the one worth dry-running.

Landed: the charger config step, `execution.py` with the decision matrix,
`ChargerStatus` normalization, `binary_sensor.ready_to_charge`, the execution
attributes on `sensor.bitcruise_plan_status`, and the execution sentences in the
summary. Remaining:

- [ ] Confirm on the real Zaptec over several nights that `next_charger_action` matches what a human watching the charger would do. This is the whole point of 6a and cannot be done from here.
- [ ] Decide whether `charging_power_entity` earns a sensor of its own or stays config-only. It is collected but currently unused.
- [ ] Consider reading a status entity's `options` attribute during config, and warning when it declares a state BitCruise does not recognise. The reference charger publishes its full enum there, so an unsupported charger could be detected at setup rather than at 02:00.

## 2. Phase 6b — Charger execution, acting

Fires the actions. 6a already decides *what* to do and skips actions that state
information proves unnecessary — `execution.next_action` returns one action at a time
and is re-asked every evaluation. What is left is carrying it out and coping when it
does not work.

The restart and idempotency items below came from Phase 7 and are deliberately built
here rather than deferred: they are part of writing a start flow, not a hardening pass
afterwards.

Landed: `switch.bitcruise_operate_the_charger` (default **off**, so an upgrade
never starts operating a charger that was not being operated before), domain
dispatch to `button.press` / `switch.turn_on` / `switch.turn_off`, a persisted
attempt marker giving idempotency across restarts, a cooldown so a burst of
state changes presses once, giving up after three ignored attempts, and the
stall reported on the status attributes and in the summary. Remaining:

- [ ] Validate against the real Zaptec with the switch on, once 6a's dry run looks right.
- [ ] Late connection: recalculate achievable SoC and report it; do not extend past approved end. Starting late already works — the decision simply becomes START when the car appears mid-window — but the reachable state of charge is still reported as though the full window were used.
- [ ] Detect an external stop: someone stopping the charge by hand currently reads as CONNECTED and gets started again. Decide whether that is right, or whether a manual stop should be respected for the rest of the window.
- [ ] Disconnect mid-charge: currently just stops deciding. Consider whether it deserves a notification (Phase 5a).
- [ ] Document the idempotency limitation where no status entity is configured — without one, "did it work?" cannot be answered, and the attempt cap is the only protection.
- [ ] Decide whether `ATTEMPT_COOLDOWN` (60s) and `MAX_ATTEMPTS` (3) should be configurable, once there is evidence from a real charger.

## 3. Phase 9 — Reserve floor becomes active

See `DESIGN.md` §6.4 and ADR-007. Also removes the "NOT ACTIVE YET" wart from the
settings UI, which currently ships a setting that does nothing.

Charge to the floor regardless of price; *prefer* the cheapest hours available before
you need it. Cheap-when-possible, not cheap-or-nothing — only charging to the floor
when power happens to be cheap breaks the one job the floor has, which is that the car
is drivable on an expensive day too.

- [ ] Urgency-aware search window: start from "as soon as connected" rather than gating on ready-by.
- [ ] Objective switch: restore the floor at the earliest opportunity, cheapest-first only among non-delaying intervals.
- [ ] Two-segment plans: urgent portion up to the floor, then `NORMAL` optimization up to the target.
- [ ] Recompute urgency on every SoC change; a drop through the floor produces an `URGENT` plan.
- [ ] Urgent replan when the car reconnects below the floor outside any approved window.
- [ ] `auto_approve_urgent` setting, default enabled.
- [ ] Guarantee: urgent charging never cancels, shortens, or moves an approved plan; it is added before it.
- [ ] Approved plan invalidated by an unexpected SoC drop stages a replacement proposal rather than extending itself.
- [ ] Warn when the battery is low and power is expensive — it still charges, but the cost is worth surfacing rather than discovering on a bill.
- [ ] Tests: floor breach while idle; breach mid-window; reconnect below floor; urgent plan overlapping an approved plan; `auto_approve_urgent` disabled; floor restored on an expensive day.

## 4. Phase 8a — Daily commute requirement

The biggest battery-health win on the list, and the input multi-day planning needs.
Charging to what the day actually costs instead of 90% every night matters far more
than shifting a window by a few hours. See `DESIGN.md` §6.5 and §17.

- [ ] One-way commute distance setting, doubled for the return leg. Label it "one way" unmistakably — entering the round trip gives a target twice too high, and nothing about the result looks wrong.
- [ ] Optional range entity in config (`sensor.volvo_xc40_distance_to_empty_battery`, km). Prefer it over any consumption figure: it is live, temperature-aware, and `trip_km / range_km × current_soc_pct` needs neither battery capacity nor kWh/100km. See `DESIGN.md` §17.
- [ ] Read SoC and range in the same evaluation — a ratio between two moments is meaningless.
- [ ] Fall back to consumption when range or SoC is zero, unusable, or no range entity is configured. Never return a confident answer from a degenerate ratio.
- [ ] Consumption fallback chain: vehicle-reported (`17.9 kWh/100km` on the reference installation), then user-configured.
- [ ] Configurable margin, now correcting for route type rather than the season — a range learned on town driving understates a motorway run.
- [ ] Add the reserve floor on top rather than comparing against it — arriving home at exactly the floor means the commute consumed everything spare.
- [ ] `sensor.commute_energy_required` (kWh) and the state of charge that covers the day.
- [ ] `binary_sensor.commute_covered` — whether the configured target covers a return trip with reserve intact.
- [ ] Advise only: the vehicle target is read-only, so report the figure and let the user set it.
- [ ] Put it in `trip_energy.py` — a commute is a trip that repeats and needs no booking.
- [ ] Tests: one-way doubling; range-derived vs consumption-derived agree on a known case; fallback at zero SoC and zero range; no range entity configured; consumption from entity vs configured; floor added; target too low; margin applied.

## 5. Phase 16 — Multi-day price awareness

Depends on 9 and 8a: deferring means knowing what tomorrow morning demands.

Tomorrow's actual prices publish around 13:00 CET. If tomorrow is materially cheaper,
charge only what tonight requires and do the bulk tomorrow.

**Design constraint that keeps this cheap:** decide *how much to charge tonight*, not
*one plan spanning two days*. The window stays contiguous, the planner is unchanged,
and only this cycle's effective target moves. A two-day plan would drag in
non-contiguous allocation for no benefit.

- [ ] Compare the cheapest window before the next deadline against the cheapest window in the following cycle.
- [ ] Lower this cycle's effective target when deferring, never the window shape.
- [ ] Floor first: a deferral may never take the car below the reserve floor plus what tomorrow's commute needs. Phase 9 wins every disagreement.
- [ ] Configurable saving threshold, so the plan does not oscillate on price noise.
- [ ] Re-evaluate when tomorrow's actual prices land. `tomorrow_valid` and `PlanPriceQuality` already carry actual-vs-forecast from Phase 3 — reuse them rather than adding a clock trigger for 13:00.
- [ ] Defer on *forecast* prices only with a larger, separate margin. A forecast that is wrong costs a morning, and the cost is asymmetric.
- [ ] Cap how far deferral may chain. "Tomorrow looks cheaper" every day means the car never charges; after the cap it charges and says why.
- [ ] Report the decision in the summary: what was charged tonight instead of what, and how much cheaper the deferred window is.
- [ ] Tests: tomorrow much cheaper; cheaper only by noise; forecast-only tomorrow; deferral would breach the floor; deferral chain hits the cap; actual prices arriving mid-evening reverse the answer.

## 6. Phase 8c — Optional deadline

Nearly free once 9 and 8a exist, and not viable before them: with no deadline, nothing
stops the planner waiting forever for a cheaper hour except the floor.

- [ ] Make ready-by optional in the config flow.
- [ ] With no deadline, plan on price and the reserve floor alone across the known horizon.
- [ ] Never invent a deadline to fill the gap.
- [ ] Tests: no deadline still charges; no deadline and no floor does nothing rather than charging arbitrarily.

## 7. Phase 5a — Notification plumbing and the critical cases

The cases you want the day execution goes live, and the plumbing the cheap power alert
needs. Not all of Phase 5.

- [ ] Optional notification target/action in config.
- [ ] Send `sensor.bitcruise_summary` rather than composing a second set of sentences. If a message needs wording the summary does not have, add it to `summary.py`.
- [ ] Debounce notifications, not recomputation (`DESIGN.md` §6). Rate-limit at the point a message would be sent; a coordinator-level debouncer was tried in Phase 3 and made restart recovery worse for no gain.
- [ ] Charger-action-failed notification.
- [ ] Car-not-connected notification before start.
- [ ] Warning offset setting, default 15 minutes.
- [ ] Verify the integration is fully operable with no notification target configured.

## 8. Phase 5b — Cheap power alert

Independent of the car, the charger and `switch.smart_charging`. Needs the Phase 3
price curve, which exists, and the plumbing in 5a. Can jump the queue whenever a quick
win is wanted — but do not let it become a reason to build all of Phase 5 first.

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

## 9. Phase 5c — Remaining notification cases

- [ ] Initial proposal notification.
- [ ] Actual-prices-published / move-plan notification.
- [ ] Impossible-target notification.
- [ ] Optional completion summary.
- [ ] Actionable buttons call the same integration actions as dashboard controls.
- [ ] Notification actions must cope with `button.accept_plan` / `button.reject_plan` being unavailable once the question is answered. Home Assistant skips unavailable entities in a service call, so a stale tap is inert rather than an error — decide whether that silence is good enough or whether the action should report back.

## 10. Phase 10 — Calendar booking input

The calendar half: when the car is needed, and how far it is going.

- [ ] Optional car-booking calendar entity in config.
- [ ] Look-ahead horizon, parsing mode, consumption kWh/100 km, reserve SoC, normal target, max target, trip-prep approval policy.
- [ ] Structured event-description parsing (`distance_km`, `return`, `reserve_soc`, optional fields).
- [ ] Load events over the horizon and normalize to `CarBooking`.
- [ ] Detect overlaps.
- [ ] Determine next required departure time.
- [ ] Derive required departure SoC and feed target/deadline into the planner.

## 11. Phase 11 — Trip energy planning

Cheaper after 8a: they share `trip_energy.py`, and the range-first model is already
built by then.

- [ ] Extend `trip_energy.py` from the commute model to booked trips, range-first with the consumption formula as fallback.
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

## 12. Phase 13 — Planned distance calendar

Depends on 10 and 11. See `DESIGN.md` §17. A derived read-model over `CarBooking` — it
makes no charging decisions and must not become a second source of truth.

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

## 13. Phase 7 — Repairs and diagnostics

All that is left of Phase 7 once 6b absorbs the execution recovery. Persistence,
callback re-registration, expiry and unavailable-source handling already landed in
Phases 3 and 4 and have been deleted from this list.

- [ ] Repairs issues for broken entity selections.
- [ ] Diagnostics output with private data redacted.

## 14. Phase 14 — First HACS-quality release

After the calendar and distance work: once other people install it, every configuration
change needs a migration path, so the schema should stop moving first.

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

## 15. Phase 15 — Multiple vehicles and shared-resource coordination

Depends on 6b and should follow 9, since a floor breach on one car competes with a
normal plan on another. See `DESIGN.md` §18 and ADR-008.

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

## Phase 12 — Booking conflict decisions (unscheduled)

Producing the decision is in scope; replying to the invitation is not. A separate
project owns that (ADR-005). Unscheduled because nothing else depends on it.

- [ ] Pure `booking_policy.py` returning `BookingDecision`.
- [ ] Deterministic initial policy (accept / decline / needs-review).
- [ ] Expose the decision so another project can act on it.
- [ ] Fixture-based tests with no network access and no provider-specific code.

---

## Later backlog (unordered)

### Demoted, with reasons

- [ ] **Just-in-time finishing** (was Phase 8b). Demoted below multi-day planning: sitting at 80-90% for a few days does not meaningfully age a battery, so the gain is small, and it optimises the opposite axis to deferring a whole day. Revisit only if daily targets end up near 100%.
- [ ] **Split charging across non-contiguous cheapest intervals.** Avoided by framing multi-day as "how much tonight" rather than one plan across two days. Only needed if a single cycle's cheapest energy is genuinely split.

### Cheap and independent, slot in anywhere

- [ ] Negative-price preference.
- [ ] Vacation / away mode.
- [ ] Price ceiling / "never charge above X unless required".

### Needs data only execution produces

- [ ] Learned wall-to-battery efficiency — needs charger session energy against SoC gain, so it cannot start before 6b.
- [ ] Historical actual charging power learning.
- [ ] Charger session energy reconciliation.
- [ ] Completion detection from SoC target rather than schedule end.
- [ ] Dynamic charger current based on house load.

### Presentation

- [ ] Custom Lovelace card. Last, once the entity shape has stopped moving. The summary sentence should make this less necessary, not more.
- [ ] Translate `sensor.bitcruise_summary`. Home Assistant translates enumerated entity states, not composed ones, so the sentence is English-only. `summary.py` keeps the wording in one place; the likely route is `async_get_translations` over a custom category, which needs checking against hassfest first.

### Calendar conveniences

- [ ] Calendar UI helpers: a way to create a car booking without hand-typing the `distance_km: / return:` block into an event description (`DESIGN.md` §17). An action, or a blueprint, that writes a correctly-formed event.
- [ ] Route provider adapter: derive `distance_km` from the booking's Location instead of the user entering it, via an optional geocoding/routing service (`DESIGN.md` §17).

### Bigger, later

- [ ] Solar forecast / surplus charging.
- [ ] Multiple EVs sharing one connection limit.
- [ ] Multiple chargers.
- [ ] Configurable tariff/tax components.
- [ ] DC fast-charge stop planning.
- [ ] Household priority rules.
