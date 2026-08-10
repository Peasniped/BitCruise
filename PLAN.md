# PLAN.md

Delivery plan for **BitCruise** — what gets built, in what order, and what "finished" means for each phase.

Design detail lives in [DESIGN.md](DESIGN.md). Actionable task state lives in [TODO.md](TODO.md); this file deliberately carries no checkboxes.

The project builds in layers. The first useful release solves charging well before calendar/trip-booking features are added. The architecture stays provider-agnostic: Home Assistant supplies the car, charger, price, notification, and later calendar entities. Vendor-specific adapters are conveniences, not the core domain model.

---

## Phase status

**Phase numbers are identity, not sequence.** They are referenced from `DESIGN.md`, the
ADRs and `CLAUDE.md`, so they keep their names when priorities move. The `Order` column
is the delivery sequence; [TODO.md](TODO.md) is written in that order.

| Order | Phase | Title | Status |
| --- | --- | --- | --- |
| — | 0 | Repository bootstrap | Complete |
| — | 1 | Domain model and pure charging planner | Complete |
| — | 2 | HA source binding and visible sensors | Code complete; awaiting hardware check |
| — | 3 | Energi Data Service + Carnot price adapter | Complete |
| — | 4 | Proposal/approval state machine | Complete |
| 1 | 6a | Charger execution, reporting only | Not started |
| 2 | 6b | Charger execution, acting | Not started |
| 3 | 9 | Reserve floor becomes active | Not started |
| 4 | 8a | Daily commute requirement | Not started |
| 5 | 16 | Multi-day price awareness | Not started |
| 6 | 8c | Optional deadline | Not started |
| 7 | 5a | Notification plumbing and critical cases | Not started |
| 8 | 5b | Cheap power alert | Not started |
| 9 | 5c | Remaining notification cases | Not started |
| 10 | 10 | Calendar booking input | Not started |
| 11 | 11 | Trip energy planning | Not started |
| 12 | 13 | Planned distance calendar | Not started |
| 13 | 7 | Repairs and diagnostics | Partly absorbed; see below |
| 14 | 14 | First HACS-quality release | Not started |
| 15 | 15 | Multiple vehicles and shared-resource coordination | Not started |
| — | 8b | Just-in-time finishing | Demoted to backlog |
| — | 12 | Booking conflict decisions | Unscheduled |

### What changed, and why

The original 5→9 sequence was written before Phases 3 and 4 existed. Reordering by
delivered value produced four structural changes:

**Execution comes first.** Until it charges the car, everything else is an integration
that tells you things you then act on by hand. It splits in two: 6a builds the whole
config surface and every precondition check but never fires an action, so it can be
dry-run against the real charger; 6b acts.

**Phase 7 mostly already happened.** Persistence, callback re-registration, plan expiry,
and tolerating unavailable sources all landed in Phases 3 and 4 and are covered by
tests. What remains that concerns *execution* — idempotency markers, reconciling a
restart inside an approved window — moves into 6b, because a build that can double-start
a charger should never exist even briefly. Repairs and diagnostics are what is left.

**The floor and the commute figure move ahead of everything they enable.** Both are
prerequisites for multi-day planning: deferring charging to tomorrow requires knowing
what tomorrow morning actually demands, and with a 90% target and no floor there is
nothing to defer.

**Multi-day price awareness (Phase 16) replaces just-in-time finishing.** Sitting at
80-90% for a few days does not meaningfully age a battery; sitting at 100%, hot, does.
So delaying *within* a night buys little, while shifting a whole day is worth real
money — and the two optimise opposite axes. Just-in-time is demoted rather than
dropped, in case daily targets end up near 100%.

The public release still sits at Phase 14, after the calendar and distance work,
because the configuration schema should stop moving before other people install it and
have to be migrated.

Provider-specific calendar RSVP is **out of scope permanently**, not deferred. A
separate lightweight project handles it. BitCruise may still decide whether a booking
conflicts (Phase 12); it just never speaks the invitation protocol.

Decided already: repository `Peasniped/BitCruise`, integration domain `bitcruise`,
one vehicle per installation for V1 (`DESIGN.md` ADR-008).

---

## Phase 0 — Repository bootstrap

**Goal.** A clean custom-integration repository that can be developed locally, checked in CI, and installed through HACS as a custom repository.

**Scope.** Licence, `.gitignore`, README with early-development warning, `hacs.json`, `custom_components/bitcruise/` skeleton with a valid `manifest.json`, minimal `__init__.py` and `config_flow.py`, translations and tests structure, and CI for tests + HACS + Hassfest validation. Decide and document the minimum supported Home Assistant version and local VS Code development steps.

**Acceptance criteria.**

- Repository validates as a HACS custom integration.
- The integration copies into `custom_components` without structural errors.
- Home Assistant recognizes the config flow.
- A minimal config entry loads and unloads successfully.
- CI runs automatically on push and PR.

---

## Phase 1 — Domain model and pure charging planner

**Goal.** Charging math and cheapest-window selection with no Home Assistant side effects.

This is the most important layer. Charger control is not started before it is heavily tested.

**Scope.** The domain models and `ChargePlan` shape in `DESIGN.md` §4, the calculations in §5, and the contiguous-window optimizer in §6.

Includes `reserve_floor_pct` and `ChargeUrgency` in the domain model and the floor
deficit calculation (§5, ADR-007). The floor defaults to `0`, which reproduces pure
deadline-driven behavior; urgency-aware *planning* is Phase 9. Carrying the field
now avoids reopening `PlanningInput`, `ChargePlan`, persistence, and the approval
state machine at once later.

**Acceptance criteria.**

Given fixture prices and battery state, `planner.py` returns exactly the expected plan without importing Home Assistant, and the planner test matrix in `DESIGN.md` §14 passes.

---

## Phase 2 — Home Assistant source binding and visible sensors

**Goal.** The user configures vehicle and price inputs entirely through the HA UI and sees the requested deficit values.

**Scope.** The V1 config flow surface in `DESIGN.md` §10 (vehicle, charging, prices — charger execution is deferred to Phase 6 unless it falls out naturally from the flow design), the runtime normalization rules in §13, and the first exposed entities: charging deficit %, battery energy deficit kWh, grid energy required kWh, required charge duration, charge-needed binary sensor, and plan status.

Also adds the reserve floor as a configurable percentage, validated against the target
(`DESIGN.md` §5). It is exposed and honoured in the deficit figures; acting on it
urgently is Phase 9.

**Acceptance criteria.**

On the real XC40 installation, changing vehicle SoC or charge target updates deficit % and kWh correctly, with no YAML templates.

---

## Phase 3 — Energi Data Service + Carnot price adapter

**Goal.** Turn the selected EDS entity into normalized price intervals, with forecast data usable before official next-day prices arrive.

**Scope.** The price-source abstraction and EDS/Carnot adapter behavior in `DESIGN.md` §12, plus the replanning triggers and debounce in §6.

Attribute names must be captured from the user's real Energi Data Service sensor and frozen as sanitized fixtures. Do not code from assumptions.

**Acceptance criteria.**

Before official tomorrow prices exist, the planner builds a forecast-based plan. Once actual prices replace the forecast, it produces the actual-price optimum and can explain whether the plan changed.

---

## Phase 4 — Proposal/approval state machine

**Goal.** Make plan approval a first-class feature.

**Scope.** The persisted proposal/approval record in `DESIGN.md` §11, the control entities and core approval rules in §7, and the proposed/approved start-end, estimated cost, plan status, and approval-required sensors.

**Acceptance criteria.**

An already approved schedule can never be silently changed by a background replan.

---

## Phase 5 — Notifications

**Goal.** Provide the desired household interaction without making notifications mandatory.

**Scope.** The optional notification target, warning offset (default 15 minutes), and the notification cases and message shapes in `DESIGN.md` §8.

Delivered in three parts, because they are wanted at different times:

*5a — Plumbing and the critical cases.* The notification target, the debounce, and the
two messages worth having the day execution goes live: charger action failed, and car
not connected before start. Messages send `sensor.bitcruise_summary` rather than
composing a second set of sentences; wording a message needs and the summary lacks is
added to `summary.py`.

*5b — Cheap power alert (§8).* Independent of the car: it fires whether or not charging
is needed and is not gated on the smart charging switch. It needs the Phase 3 price
curve and 5a's plumbing, nothing else, so it can be brought forward whenever a quick
win is wanted. Its main design risk is nuisance rather than correctness — one
notification per cheap *window*, never per interval, and never repeated when the price
curve refreshes.

*5c — The remaining cases.* Proposal, plan moved, impossible target, completion summary.

Buttons in notifications invoke the same integration actions as dashboard buttons. Approval logic is never duplicated inside notification handling. Since Phase 4's presentation pass, `button.accept_plan` and `button.reject_plan` are unavailable when nothing is pending, and Home Assistant silently skips unavailable entities in a service call — so a notification action tapped after the question is answered does nothing rather than failing. Whether that silence is acceptable is a 5c decision.

**Acceptance criteria.**

The system remains fully operable from HA entities when no notification target is configured.

---

## Phase 6a — Charger execution, reporting only

**Goal.** Everything execution needs except firing an action, so the whole flow can be
dry-run against the real charger before it is allowed to touch it.

**Scope.** The optional charger capability selections added to the config flow
(`DESIGN.md` §10), every start precondition in §9 evaluated and reported,
`binary_sensor.ready_to_charge`, and the execution states added to the summary
sentence. No service call is made.

Charger control entities are `unavailable` while unplugged on the reference
installation. That is "cannot act yet", not a fault, and must not read as one.

**Acceptance criteria.**

Over several nights on the real Zaptec Go 2, the integration reports that it would have
authorized and started at the planned time, and the report matches what a human
watching the charger would have done. No charger action is ever sent.

---

## Phase 6b — Charger execution, acting

**Goal.** Execute an approved plan through the user-selected Home Assistant controls.
The initial real target is a Zaptec Go 2, via generic entity/action selection rather
than Zaptec-specific code.

**Scope.** The start/end execution flows, late-connection behavior, and failure
handling in `DESIGN.md` §9, plus the execution half of §11: idempotency markers and
reconciliation when startup falls inside an approved window.

Those last two came from Phase 7 and are built here deliberately. Knowing whether an
action has already been sent is part of writing the action, not a later hardening pass;
a build that can double-start a charger should never exist, even briefly.

**Acceptance criteria.**

On the real Zaptec Go 2, an approved plan authorizes/starts at the planned time and
stops at the planned end, with no YAML automation glue. Restarting Home Assistant at
each of these points produces sensible behavior: before scheduled start; one minute
before start; during charging; one minute before end; after end; while price or car
entities are unavailable.

---

## Phase 7 — Repairs and diagnostics

**Goal.** Make a broken configuration explain itself.

**Scope.** Repairs issues for broken entity selections, and diagnostics output with
private data redacted.

This is what remains of the original "restart recovery and robustness" phase.
Persistence, callback re-registration, plan expiry and tolerating unavailable sources
landed in Phases 3 and 4 and are covered by tests; the execution-recovery items moved
into Phase 6b.

**Acceptance criteria.**

Deleting a selected source entity raises a Repairs issue naming it, and a diagnostics
download contains no entity IDs, addresses, or notification targets belonging to the
household.

---

## Phase 8 — Daily charging for battery health

**Depends on:** Phases 3 and 4.

**Goal.** Stop treating "full by a fixed morning deadline" as the only way to charge.
A lithium battery ages faster the longer it sits at a high state of charge, so the
default behaviour should be to hold a modest daily level and reach a high one only
when something actually requires it.

**Scope.** Two changes, specified in `DESIGN.md` §6.5 and §17. A third,
*just-in-time finishing*, was split out and demoted — see below.

*8a — Daily commute requirement.* The user enters their commute distance **one way**;
BitCruise doubles it for the return leg, converts it to energy — preferring the
vehicle's own live range estimate over any kWh/100km figure (`DESIGN.md` §17) — adds
the reserve floor, and reports the state of charge that actually covers the day. That
figure is what a daily target should be set to, instead of 90%.

This is the largest battery-health win available, and it is also a prerequisite for
Phase 16: you cannot defer charging to tomorrow without knowing what tomorrow morning
demands.

*8c — Optional deadline.* Ready-by becomes optional. A household without a fixed
departure should be able to say "keep it above the floor, charge only when cheap" and
have that be a complete configuration. It depends on Phase 9 rather than the reverse:
with no deadline, the only thing stopping the planner waiting forever for a cheaper
hour is the reserve floor.

*8b — Just-in-time finishing (demoted).* When several windows cost about the same,
prefer the one that ends closest to the deadline. Moved to the backlog: sitting at
80-90% for a few days does not meaningfully age a battery, so the gain is small, and it
optimises the opposite axis to Phase 16's whole-day deferral. Revisit only if daily
targets end up near 100%.

**Acceptance criteria.**

Entering a one-way commute produces a defensible minimum state of charge, and clearly
reports whether the configured target covers a return trip plus reserve. With no
deadline configured, charging still happens on price alone without the planner
inventing a deadline.

---

## Phase 9 — Urgency-aware planning for unplanned trips

**Depends on:** Phases 1, 2, 4. Independent of the calendar phases.

**Goal.** Keep the car drivable at short notice without a calendar booking, by acting
on the reserve floor rather than only on the ready-by deadline.

Most household driving is never booked. Cost optimization works against spontaneity:
the cheapest plan deliberately leaves the battery low until the early hours.

**Scope.** The planning rules, reconnection behavior, and approval interaction in
`DESIGN.md` §6.4 — the urgency-aware search window, two-segment plans that restore the
floor urgently then optimize the remainder normally, `auto_approve_urgent`, and urgent
replanning when a car returns below the floor outside any window.

The relaxation of the approval rule is strictly one-directional: urgency may add
charging that was not explicitly approved, and may never cancel, shorten, or move an
approved plan (ADR-003, ADR-007).

**Acceptance criteria.**

Driving the car to below the reserve floor at an arbitrary time produces charging that
begins at the next opportunity rather than at the next cheapest window, while any
already approved plan survives unchanged.

---

## Phase 16 — Multi-day price awareness

**Depends on:** Phase 9 (the floor) and Phase 8a (the commute requirement). Both are
hard prerequisites: deferring charging means knowing what tomorrow morning demands, and
with a 90% target and no floor there is nothing that may safely be left uncharged.

**Goal.** Stop optimising one night at a time. Tomorrow's actual prices publish around
13:00 CET; if tomorrow is materially cheaper, charge only what tonight requires and do
the bulk tomorrow.

**Scope.** A comparison between the cheapest window before the next deadline and the
cheapest window in the following cycle, and a decision about *how much* to charge now.

The load-bearing design constraint: this changes **how much to charge tonight**, not
the shape of the window. It must not emit one plan spanning two days. The window stays
contiguous, `planner.py` is unchanged, and only this cycle's effective target moves —
which keeps "split charging across non-contiguous intervals" out of the critical path.

Three things make it safe rather than clever:

- **The floor is not negotiable.** A deferral may never take the car below the reserve
  floor plus tomorrow's commute. Phase 9 wins every disagreement.
- **Forecast prices need a wider margin than actual ones.** The cost is asymmetric: a
  wrong forecast means a car that is not ready, not a slightly worse price. Reuse the
  `tomorrow_valid` and `PlanPriceQuality` distinction from Phase 3 rather than adding a
  clock trigger for 13:00.
- **Deferral must not chain forever.** "Tomorrow looks cheaper" every day is a car that
  never charges. There is a cap, after which it charges and reports why.

**Acceptance criteria.**

On an evening where tomorrow is materially cheaper, the car charges only what it needs
for the morning and the rest is scheduled for the cheaper day, with the reason visible
in the summary. On an evening where tomorrow is cheaper only by noise, the plan is
unchanged. In neither case does the car drop below the reserve floor.

---

## Phase 10 — Calendar booking input

**Goal.** Let a shared household calendar define when the car is needed, via `Fastmail -> CalDAV -> HA calendar entity -> BitCruise`.

**Scope.** The calendar abstraction, booking schema convention, `CarBooking` model, and booking logic in `DESIGN.md` §17. No Fastmail credentials in BitCruise for this phase.

**Acceptance criteria.**

Adding a valid car-booking event changes the next ready-by deadline and required departure SoC without changing the core charging optimizer.

---

## Phase 11 — Trip energy planning

**Goal.** Estimate the energy a booking requires, including the return journey, and derive a required departure SoC.

**Scope.** The trip energy model and long-trip target policy in `DESIGN.md` §17, kept in a separate `trip_energy.py`.

**Acceptance criteria.**

A booking with an explicit distance produces a defensible required departure SoC and feeds it into the same planner used by ordinary charging, with `intermediate_charge_required` set honestly.

---

## Phase 12 — Booking conflict decisions (unscheduled)

**Goal.** Determine whether a requested car booking conflicts with existing bookings.

This logic belongs in this project if BitCruise evolves into a household "car resource manager", because it directly determines vehicle availability and charging deadlines.

**Scope.** The pure `booking_policy.py` decision engine and deterministic policy in `DESIGN.md` §17.

Producing the decision is in scope; acting on it is not. Replying to an invitation is a
provider protocol concern and is handled by a separate project (ADR-005).

**Acceptance criteria.**

Given a fixture set of bookings, the decision engine returns deterministic conflict decisions with no network access and no provider-specific code.

---

## Phase 13 — Planned distance calendar

**Depends on:** Phases 10 and 11.

**Goal.** Show how far the car is planned to drive on each day of the look-ahead
horizon.

**Scope.** The aggregation rules and exposure format in `DESIGN.md` §17 — local-day
totals with DST correctness, return-trip doubling, start-day attribution for multi-day
bookings, and explicit `None`/incomplete handling for bookings without a distance.

This is a derived read-model over `CarBooking` data. It makes no charging decisions and
must not become a second source of truth.

**Acceptance criteria.**

A week of bookings produces a correct per-day distance breakdown; a booking with no
stated distance marks its day incomplete rather than contributing zero; and a day whose
planned distance exceeds usable range is visibly flagged.

---

## Phase 14 — First HACS-quality release

**Depends on:** everything above it.

**Goal.** Publish a version another Home Assistant user can install and understand
without reading the source.

It sits here rather than earlier on purpose. Once other people install it, every
configuration change needs a migration path, so the schema should stop moving first.

**Scope.** README installation instructions, documented entity requirements,
troubleshooting and diagnostics sections, changelog, version alignment in
`manifest.json`, brand assets submitted to `home-assistant/brands`, and verified clean
install plus upgrade through a HACS custom repository.

Call this `0.5.x`, not `1.0`, until the configuration schema and behaviour have seen
real household use.

**Acceptance criteria.**

HACS validation, Hassfest, and the test suite all pass, and a clean install and an
upgrade have both been tested.

---

## Phase 15 — Multiple vehicles and shared-resource coordination

**Depends on:** Phases 1–7. Should follow Phase 9, since a floor breach on one car
competes with a normal plan on another.

**Goal.** Support a household with more than one EV without double-booking a charger
or exceeding the household supply limit.

**Scope.** The allocation layer, priority rules, and config entry model in
`DESIGN.md` §18. The single-vehicle planner is not modified (ADR-002, ADR-008); the
allocator sits above it, consuming per-vehicle requirements rather than finished
windows. Includes relaxing `single_config_entry` in `manifest.json` and adding a
vehicle identifier to `CarBooking`.

Before starting, verify the current state of the Home Assistant config subentry APIs
against official developer documentation rather than assuming availability.

**Acceptance criteria.**

Two vehicles sharing one charger receive non-overlapping windows; two vehicles on
separate chargers stay within the configured household power limit; and the priority
rule that resolved any collision is inspectable rather than emergent.

---

## Later backlog

Unordered, tracked in [TODO.md](TODO.md):

just-in-time finishing (demoted from Phase 8, see above); split charging across non-contiguous cheapest intervals; price ceiling / "never charge above X unless required"; negative-price preference; solar forecast/surplus charging; dynamic charger current based on house load; multiple EVs sharing one connection limit; multiple chargers; historical actual charging power learning; learned wall-to-battery efficiency; charger session energy reconciliation; automatic completion detection from SoC target rather than schedule end; configurable tariff/tax components; custom Lovelace card; calendar UI helpers; route provider adapter; DC fast-charge stop planning; household priority rules; vacation/away mode.

---

## First coding session order

1. Create the HACS-compatible folder structure.
2. Add a minimal manifest and config flow that loads and unloads.
3. Set up tests, HACS, and Hassfest CI.
4. Implement `models.py`.
5. Implement `planner.py` against synthetic prices.
6. Write planner tests until the edge cases are trustworthy.
7. Only then bind real Home Assistant entities.
8. Capture sanitized Volvo/EDS/Zaptec entity examples as fixtures as each adapter or capability is implemented.

Do not start by writing Zaptec service calls. The optimizer and state model are the foundation.
