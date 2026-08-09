# DESIGN.md

Product, domain, and architecture specification for **BitCruise**.

This is the *what* and *why*. See [PLAN.md](PLAN.md) for delivery phases, [TODO.md](TODO.md) for the actionable backlog, and [CLAUDE.md](CLAUDE.md) for how Claude should work in this repository.

---

## 1. Overview

Project name: **BitCruise**

GitHub repository: `Peasniped/BitCruise`

Home Assistant custom integration domain: `bitcruise`

> The `bitcruise` domain is considered final. It becomes part of entity IDs, storage, config entries, and user installations; do not rename it casually after release.

BitCruise is a Home Assistant custom integration that plans and executes residential EV charging using data that already exists in Home Assistant.

The initial installation is:

- Volvo XC40 Recharge exposed through the Home Assistant Volvo integration.
- Zaptec Go 2 exposed through the Home Assistant Zaptec integration.
- Electricity prices exposed through the Energi Data Service integration, including Carnot forecast data.
- Approximately 10 kW charging power.

The integration must **not** directly talk to Volvo, Zaptec, or Energi Data Service APIs in v1. It consumes Home Assistant entities and invokes Home Assistant actions. This keeps the integration reusable for other cars, chargers, and price providers.

### Product vision

```text
Vehicle state         Electricity prices       Household intent
     |                         |                       |
     +------------+------------+-----------------------+
                  |
                  v
          BitCruise
                  |
          proposed charge plan
                  |
            human approval
                  |
                  v
              Charger
```

Later:

```text
Fastmail shared car calendar
          |
   HA CalDAV calendar
          |
          v
   Booking / Trip Planner
          |
 required departure SoC
          |
          v
   BitCruise
```

The key product rule is:

> **Optimization may propose; approved household intent wins.**

An accepted charging plan or accepted car booking must not be silently displaced by a later optimization.

---

## 2. Scope

### V1 goals

1. Read current vehicle battery percentage.
2. Read or configure the vehicle charging target percentage.
3. Read battery usable capacity in kWh, or allow the user to configure it.
4. Calculate and expose:
   - charging deficit in percentage points;
   - charging deficit in kWh;
   - required charging duration;
   - planned start and stop time;
   - estimated charging cost when possible;
   - plan state/status.
5. Read electricity prices from an existing Home Assistant price entity.
6. Support current-day prices, next-day actual prices, and forecast prices when exposed by the selected price entity.
7. Find the cheapest charging window capable of satisfying the requested energy before the configured ready-by time.
8. Stage a proposed schedule before activating it.
9. Ask the user to accept, reject, or keep/move a schedule through Home Assistant notifications/actions when configured.
10. Recalculate after next-day actual prices are published, normally around early afternoon for the initial Energi Data Service use case.
11. Never silently move an already accepted plan merely because a cheaper plan appears; stage the replacement and request approval.
12. Notify the user shortly before charging if the car is not connected.
13. At schedule start, authenticate/authorize the charger when necessary, then start/resume charging.
14. Stop or otherwise end the controlled charging session at the approved end time when configured to do so.
15. Recover safely across Home Assistant restarts.
16. Be installable from a GitHub custom repository through HACS.
17. Alert the household when electricity is unusually cheap, independently of whether
    the car needs charging.

### Explicit non-goals for V1

- Direct Volvo cloud API access.
- Direct Zaptec cloud API access.
- Direct Fastmail API/CalDAV access.
- A custom Lovelace frontend/card.
- Route planning.
- Mapping/geocoding.
- Automatic calendar invitation acceptance/decline.
- Multi-car or multi-charger orchestration.
- Solar surplus optimization.
- Dynamic charger-current modulation.
- Optimization based on grid tariffs other than the selected price signal.

V1 is not expanded to include these unless the user explicitly changes scope.

---

## 3. Core design principles

### 3.1 Home Assistant is the integration bus

Prefer consuming existing HA entities over importing vendor SDKs.

The user selects entities/actions in the config flow. The planner operates on normalized internal values.

Examples:

- battery SoC -> selected `sensor` entity;
- target SoC -> selected `number`, `sensor`, or configured fixed value;
- connected status -> selected `binary_sensor` or normalized state mapping;
- charger authorization -> selected button/action target;
- charger start/resume -> selected button/switch/action target;
- charger stop -> selected button/switch/action target;
- prices -> selected sensor with a supported price-source adapter.

Volvo entity IDs, Zaptec entity IDs, device names, and mobile notification service names are never hard-coded.

### 3.2 Pure planning logic, HA shell around it

The optimization engine must be pure Python and have no dependency on `hass`, entities, services, or config entries.

Suggested split:

- `models.py` - immutable/internal data models.
- `planner.py` - pure charging math and scheduling.
- `price_sources.py` - normalize HA price attributes to price intervals.
- `runtime.py` or `manager.py` - orchestration/state machine.
- Home Assistant platform modules - expose entities and user controls.

### 3.3 Separate planning from execution

A calculated proposal is not an active schedule.

A recalculation may replace an unapproved proposal. It must not replace an approved plan without going through the approval policy.

### 3.4 Safety beats optimization

Never start charging based on stale or invalid critical inputs.

Examples of critical invalid states:

- SoC unknown/unavailable;
- target SoC invalid;
- battery capacity <= 0;
- ready-by deadline already impossible to satisfy and no fallback policy chosen;
- no valid future price intervals;
- selected charger action no longer exists.

When uncertain, expose a diagnostic status and do not execute unsafe/ambiguous actions.

### 3.5 Time handling must be timezone-aware

Never use naive datetimes internally.

Use Home Assistant time helpers and timezone-aware datetimes. Treat DST explicitly.

The planner must handle:

- 23-hour DST days;
- 25-hour DST days;
- hourly prices;
- 15-minute prices;
- intervals that cross midnight;
- next-day deadlines;
- Home Assistant restarts between planning and execution.

Do not assume there are always exactly 24 hourly prices in a local day.

### 3.6 Generic first, adapters second

The first supported price adapter may understand Energi Data Service attributes such as current/tomorrow raw prices and Carnot forecast, but the planner itself must only see normalized `PriceInterval` objects.

Likewise, charger operations are represented by configured HA actions/targets, not by Zaptec-specific Python calls.

Vendor conveniences can be added later as auto-detection or adapters without changing the planner API.

---

## 4. Domain model

Core model types:

```text
PriceInterval
PriceQuality
PlanningInput
ChargeRequirement
ChargePlan
PlanStatus
PlanSource
ChargeUrgency
```

### PlanningInput

The pure planner accepts inputs such as:

```python
PlanningInput(
    now=...,
    current_soc_pct=...,
    target_soc_pct=...,
    reserve_floor_pct=...,  # see section 6.4; 0 disables the floor
    usable_capacity_kwh=...,
    charging_power_kw=...,
    charging_efficiency=...,
    ready_by=...,
    price_intervals=[...],
)
```

and returns a `ChargePlan` without side effects.

### ChargePlan

Suggested fields:

```text
id
created_at
start
end
required_battery_kwh
required_grid_kwh
planned_grid_kwh
current_soc_pct
target_soc_pct
estimated_soc_at_end
estimated_cost
can_meet_target
shortfall_kwh
price_quality
intervals
urgency                 # ChargeUrgency; see section 6.4
below_reserve_floor     # bool: SoC was under the floor when planned
```

### ChargeUrgency

```text
NORMAL    - deadline-driven. Optimize cost freely within [earliest_start, ready_by).
URGENT    - SoC is below the reserve floor. Charge as soon as possible; cost is
            secondary and the ready-by deadline does not gate the start time.
```

`URGENT` is a property of the plan, not a separate planner. The same optimizer
produces it, with the search window and objective adjusted as described in section 6.4.

### Plan state model

```text
IDLE
NEEDS_CHARGE
PROPOSED
AWAITING_APPROVAL
APPROVED
WAITING_FOR_CAR
READY
CHARGING
COMPLETED
CANCELLED
ERROR
```

---

## 5. Charging calculations

### Deficit percentage

"Charging deficit %" means **percentage points needed to reach the target**:

```text
deficit_pct = max(target_soc_pct - current_soc_pct, 0)
```

Example: current 42%, target 80% -> deficit 38%.

### Deficit energy

```text
battery_deficit_kwh = usable_capacity_kwh * deficit_pct / 100
```

Exposed as the energy required **in the battery**.

Charging losses are accounted for separately:

```text
grid_energy_required_kwh = battery_deficit_kwh / charging_efficiency
```

Default efficiency is configurable, e.g. `0.90`. The distinction between battery energy and grid energy must never be hidden.

### Duration

```text
required_hours = grid_energy_required_kwh / charging_power_kw
```

Do not round to full hours internally. Allocate enough discrete price intervals to cover the required duration/energy.

The final interval may be partial if the execution model supports it. For the first implementation it is acceptable to allocate a whole final interval, but the planner must report the expected over-allocation and tests must cover it.

### Charging target

Target priority for V1:

1. configured target entity if available and valid;
2. configured fixed target percentage;
3. fail configuration if neither exists.

Never infer a target from the current SoC.

### Reserve floor deficit

The reserve floor is a second, independent SoC requirement (section 6.4). It has its
own deficit, computed the same way but never mixed with the target deficit:

```text
floor_deficit_pct = max(reserve_floor_pct - current_soc_pct, 0)
floor_deficit_kwh = usable_capacity_kwh * floor_deficit_pct / 100
```

Invariants:

- `reserve_floor_pct <= target_soc_pct`. Reject or clamp a configuration where the
  floor exceeds the target, and surface it as a configuration problem rather than
  silently reordering the two.
- A non-zero `floor_deficit_pct` always implies a non-zero `deficit_pct`, because the
  floor sits at or below the target. Satisfying the target therefore satisfies the
  floor; the floor only changes *when* charging must happen, never *how much* energy
  the target ultimately requires.
- `reserve_floor_pct = 0` disables the floor entirely and restores pure
  deadline-driven behavior.

---

## 6. Scheduling behavior

### Default mode

V1 optimizes a **contiguous charging window**. Split/non-contiguous charging is added only after contiguous planning is solid and tested.

Inputs:

- earliest start: `now` or a configured not-before time;
- deadline: next occurrence of the configured ready-by time;
- required energy;
- charge power;
- normalized price intervals.

Objective:

1. satisfy required energy before deadline;
2. minimize expected cost;
3. if costs tie, prefer the earlier valid window for deterministic behavior.

### Optimizer V1 algorithm

1. Normalize price slots.
2. Clip slots to `[earliest_start, ready_by)`.
3. Enumerate valid contiguous sequences capable of delivering enough energy.
4. Compute cost using interval duration x charging power x price.
5. Pick lowest cost.
6. Tie-break deterministically by earliest start.
7. If no sequence can meet target, return explicit best-effort/shortfall result.

Do not prematurely optimize algorithm complexity. Day-ahead price horizons are tiny. Clarity and correctness are more valuable than cleverness.

### Forecast and actual price replacement

Forecast prices may be used to create an initial plan when actual next-day prices are unavailable.

When actual next-day prices become available:

- recalculate;
- compare the new plan to the accepted plan;
- do nothing if the effective schedule is materially unchanged;
- if changed, create a new proposal;
- ask the user whether to move charging;
- keep the previously accepted plan active until replacement is explicitly accepted.

The threshold for "materially changed" is a small configurable duration, initially one price interval.

### Replanning triggers

Recalculate when any of these materially change:

- SoC;
- target SoC;
- capacity/power settings;
- ready-by time;
- connection state if relevant to policy;
- price intervals;
- forecast intervals;
- tomorrow-valid state.

Debounce rapid changes to avoid notification spam.

### 6.4 Unplanned trips and the reserve floor

Most household driving is not booked in advance. The car must be usable for an
unannounced trip at any hour, and cost optimization works directly against that: the
cheapest plan deliberately leaves the battery low until the small hours of the
morning before a deadline.

BitCruise therefore separates two independent requirements:

| | Reserve floor | Charge target |
| --- | --- | --- |
| Question it answers | "Can we drive *right now*?" | "Will the car be ready *by the deadline*?" |
| Driven by | Always, continuously | The ready-by time |
| Optimization | Cost is secondary | Cost is the objective |
| Typical value | 30–50% | 80% |

These are not two planners. The floor changes the planner's **search window and
objective**; it never changes the total energy the target requires.

#### Planning rules

1. **Floor satisfied** (`current_soc_pct >= reserve_floor_pct`) — normal behavior.
   Optimize cost freely across `[earliest_start, ready_by)`. Urgency is `NORMAL`.
2. **Floor breached** (`current_soc_pct < reserve_floor_pct`) — urgency is `URGENT`.
   The search window becomes "as soon as the car is connected", the ready-by deadline
   no longer gates the start, and the objective changes to *restore the floor at the
   earliest opportunity*, choosing the cheapest intervals only among those that do not
   delay reaching the floor.
3. **Once the floor is restored**, the remainder of the plan — from floor up to
   target — reverts to `NORMAL` cost optimization against the deadline. A single plan
   may therefore contain an urgent leading portion and a cost-optimized remainder.
   Implementations may model this as two contiguous segments.
4. **A floor breach never waits for approval** when the approval policy allows it
   (see below). Leaving the car undrivable because a notification went unanswered is
   a worse failure than charging at a mildly higher price.

#### Reacting to unplanned driving

An unplanned trip shows up as an unexpected SoC drop. The integration must handle
this everywhere, not only at planning time:

- **SoC drop while idle.** Already a replanning trigger (see "Replanning triggers").
  If the drop crosses the reserve floor, the new plan is `URGENT`.
- **SoC drop that invalidates an approved plan.** The approved window may no longer
  deliver enough energy to reach the target. This is a material change: stage a
  replacement proposal as normal (ADR-003). The approved plan is *not* silently
  extended.
- **Car driven away mid-window.** Disconnection during an approved charging window is
  an execution event, not a planning failure. Mark the session interrupted, retain the
  approved plan, and resume if the car returns while the window is still open — the
  existing late-connection behavior in section 9 covers the mechanics.
- **Car returns below the floor, outside any window.** This is the case pure
  deadline-driven planning gets wrong. It must produce an `URGENT` plan immediately on
  reconnection rather than waiting for the next scheduled evaluation.

#### Approval interaction

An urgent plan and a normal plan are approved differently, because they answer to
different failure modes:

- `NORMAL` plans follow the configured approval policy (`always_ask` / `ask_on_change`).
- `URGENT` plans should be executable without waiting for approval, controlled by a
  dedicated setting (working name `auto_approve_urgent`, default enabled). When it is
  disabled, an urgent plan is proposed and notified with clear urgency framing, and
  the car may remain below the floor.

This is the one place where the "approved household intent wins" rule is deliberately
relaxed, and only ever *upward*: urgency may cause charging that was not explicitly
approved, but it must never cancel, shorten, or move an already approved plan. If an
urgent plan overlaps an approved plan, the approved plan's window is preserved and the
urgent charging is added before it.

#### Opportunistic charging

Related but distinct, and explicitly **not V1**: when the car is plugged in and prices
are unusually low or negative, charging beyond immediate need is rational even with no
deficit and no trip booked. Tracked in the later backlog; it must not be conflated with
floor maintenance, which is about availability rather than price.

#### Scope

- **V1:** `reserve_floor_pct` exists in `PlanningInput` and `ChargePlan`, the floor
  deficit is computed and exposed, and `ChargeUrgency` is part of the domain model.
  Defaulting the floor to `0` preserves exactly the deadline-driven behavior described
  elsewhere in this document.
- **Later:** the urgency-aware search window, the two-segment plan, `auto_approve_urgent`,
  and reconnection-triggered urgent planning.

Carrying the floor through the domain model in V1 is a deliberate cost: it is a small
amount of unused plumbing now, in exchange for not re-opening `PlanningInput`,
`ChargePlan`, persistence, and the approval state machine later.

### 6.5 Battery longevity: just-in-time finishing and an optional deadline

A lithium battery ages faster the longer it sits at a high state of charge. Two
defaults work against that, and both are worth changing.

#### Finish late, not early

The planner picks the cheapest window before the deadline. That window may end hours
before departure, leaving the car sitting full overnight for no benefit: the cost is
identical whether it finishes at 03:00 or 06:15, but the time spent at a high state of
charge is not.

So among windows of **comparable** cost, prefer the one that finishes latest.

Three constraints keep this from becoming a liability:

- **An explicit tolerance.** "Comparable" means within a configured margin of the
  cheapest option — a small absolute amount per kWh, or a percentage. Without it, the
  planner would trade real money for battery health without being asked. Outside the
  tolerance, price wins.
- **A safety buffer.** Finishing targets `ready_by` minus a configurable buffer,
  defaulting to about 45 minutes. Charging rarely goes exactly to plan: the charger
  may start late, the car may taper as it approaches the target, and a whole final
  interval may be allocated. Aiming at the deadline itself converts every one of those
  into a car that is not ready.
- **Determinism is preserved.** The existing tie-break prefers the earliest window so
  that repeated planning over identical inputs produces an identical plan, which
  matters because a changed plan triggers a fresh approval request. Preferring the
  latest is equally deterministic; the property survives, only the direction changes.

Selection therefore becomes: find the cheapest feasible window; take every window
within the tolerance of it; among those, choose the one finishing latest that still
ends by `ready_by - buffer`; break any remaining tie on latest start.

If no window satisfies the buffer, the buffer is dropped rather than the charge —
being ready late beats not charging — and the plan reports that it did so.

#### The deadline itself should be optional

"Ready by 07:00" assumes a fixed daily departure. Plenty of households do not have
one, and for them the deadline is not merely unnecessary, it is actively harmful: it
forces a full battery every morning whether or not the car moves.

Ready-by therefore becomes optional. With no deadline configured, the reserve floor
(section 6.4) is the whole policy: keep the car above the floor, charge when it is
cheap, and nothing more. A household can then express "top it up cheaply, never let it
get low" as a complete configuration, which is closer to how most cars are actually
used.

When no deadline is set, the planner has no horizon end other than the price data
itself, so it optimises across whatever is known and simply reports a shorter horizon
rather than inventing a deadline.

### Impossible deadline

If the car cannot reach the target before ready-by:

- generate the best-effort earliest/lowest-cost feasible plan according to a clearly documented fallback policy;
- expose `shortfall_kwh` and/or `estimated_soc_at_departure`;
- notify the user;
- never pretend the target can be met.

For V1, prefer **maximize delivered energy before deadline**, using the cheapest available intervals if there is flexibility, while making the shortfall explicit.

---

## 7. Approval model

Approval is part of the integration's domain logic, not an external YAML automation requirement.

Control entities:

- `button.accept_plan`
- `button.reject_plan`
- `button.recalculate_plan`
- `switch.smart_charging`

Possible future entity:

- `select.approval_policy` with values such as `always_ask`, `ask_on_change`, `automatic`.

For V1, default to `always_ask` or `ask_on_change` rather than fully automatic behavior.

An approved plan must be persisted so an HA restart does not accidentally lose or replace it.

### Core rules

- New plan with no approved plan -> proposal.
- User accepts -> proposal becomes approved atomically.
- User rejects -> proposal cleared/rejected.
- New optimization while approved plan exists -> compare plans.
- Equivalent plan -> keep approved plan without prompting.
- Materially changed plan -> stage replacement, keep approved plan active.
- User accepts move -> replace approved plan atomically.
- User keeps old plan -> discard replacement proposal.

---

## 8. Notifications

Notifications are optional. Core planner behavior must not require a mobile app.

Configuration allows the user to select or enter the notification action/service target supported by their installation.

Configuration surface:

- optional notification target/action;
- warning offset, default 15 minutes;
- enable/disable individual notification categories later if needed.

### Required V1 notification cases

**Initial proposal**

```text
XC40 needs 31.4 kWh (38%).
Cheapest plan: 01:00-04:15.
Estimated cost: DKK xx.xx.
[Accept] [Reject]
```

**Actual prices published**, if the optimized window changes materially

```text
Tomorrow's actual prices are available.
Move charging from 01:00-04:15 to 03:00-06:15?
Estimated saving: DKK x.xx.
[Move] [Keep]
```

**Not connected**, at the configured offset before approved start

```text
Charging starts at 01:00, but the XC40 is not connected.
```

**Impossible target**

```text
Target 80% cannot be reached by 07:00.
Estimated departure SoC: 71%.
```

Also required: charger action failed at scheduled start; optional charging completion summary.

### Cheap power alert

Tell the household when electricity is unusually cheap, so the dishwasher, dryer or
anything else can be run then. This is **not about the car**: it fires whether or not
charging is needed, whether or not the car is plugged in, and it is not gated on the
smart charging switch. BitCruise already parses the full forward price curve, so the
information exists and is otherwise wasted.

#### Threshold

A single configurable price threshold, expressed **in the units of the selected price
entity** — `0.50` for a sensor reporting `DKK/kWh`, but `50` for one reporting øre
when `use_cent` is set. The configuration UI must show the entity's own unit rather
than assuming a currency, and the stored value must be reinterpreted if the user later
selects a price entity with a different unit.

Exposed as a `number` entity so it can be tuned from a dashboard without reopening the
config flow. A relative threshold ("cheapest 10% of the coming week") is a plausible
later addition, but the absolute value is what people can reason about.

#### Two tiers

Below the threshold and below zero are qualitatively different and read differently:

```text
Cheap power: 0.46 DKK/kWh from 10:00 to 17:00.
```

```text
Negative prices: -0.12 DKK/kWh from 02:00 to 05:00. You are paid to use power.
```

#### Notify per window, not per interval

The main risk here is nuisance. A naive implementation notifies once an hour through a
seven-hour cheap block, then again every time the price curve refreshes.

Rules:

- group contiguous below-threshold intervals into a single **window** and notify once
  per window;
- identify a window by its start instant, so a refreshed price curve that reproduces
  the same window does not re-notify;
- if a known window's shape changes materially — it starts earlier, or extends — send
  at most one update, not a fresh alert;
- notify ahead of the window rather than at its start, with a configurable lead time,
  since a cheap period is only actionable if there is time to act;
- never notify about a window that has already passed.

#### Entities

Notifications remain optional, so the same information is available as state:

- `binary_sensor.cheap_power` — on while the current price is below the threshold;
- `sensor.next_cheap_period` — start of the next cheap window, with end, duration,
  minimum and mean price as attributes;
- `number.cheap_price_threshold` — the threshold itself.

Both entities are `unknown` rather than `off` when no price data is available, since
"no cheap power coming" and "we do not know" are different claims.

### Architecture rule

Actionable notifications call the same integration actions/buttons as the dashboard. Business logic must never live only inside the notification payload.

If no notification target is configured, proposals remain visible through entities and can be accepted/rejected from HA.

---

## 9. Charger execution

Model these as independent capabilities:

1. **authorize** - authenticate/authorize the connected vehicle;
2. **start/resume** - allow energy flow;
3. **stop/pause** - prevent energy flow;
4. optional **deauthorize** later.

A charger may not need all capabilities.

### At plan start

```text
approved plan valid?
  -> smart charging enabled?
    -> current time inside approved window?
      -> car connected?
        -> authorization required?
          -> authorize
        -> start/resume if needed
        -> verify charging when possible
        -> record execution state and errors
```

Do not blindly press every configured button if state information proves the action is unnecessary.

If state information is unavailable, configured actions should be designed to be idempotent where possible and the limitation must be documented.

### At plan end

```text
active approved plan?
  -> stop/pause if configured
  -> mark completed
```

### Late connection behavior

If the car is disconnected at start but plugged in during the approved window:

- begin charging immediately if enough approved window remains;
- recalculate expected achievable SoC;
- do not extend beyond approved end without policy/approval.

### Failure handling

Handle and surface: authorization failure; start failure; charger unavailable; car disconnected during charge; charging stopped externally; Home Assistant restart mid-session.

Expose diagnostic status and notify when configured.

---

## 10. Home Assistant architecture

Follow current Home Assistant custom-integration patterns.

### Configuration

- UI setup through `config_flow.py`.
- Identity/source bindings required for setup go in `ConfigEntry.data`.
- User-adjustable behavior/settings go in `ConfigEntry.options`.
- Provide options/reconfigure flow rather than requiring YAML edits.
- Support unloading without restarting Home Assistant.
- Store runtime objects in typed `ConfigEntry.runtime_data`.

### Config flow surface (V1)

**Vehicle**

- battery SoC entity;
- target SoC entity OR fixed target;
- usable battery capacity entity OR fixed kWh;
- connected binary sensor/entity;
- optional charging-state entity.

**Charging**

- fixed charging power, default 10 kW;
- charging efficiency, sensible default around 90%;
- ready-by time;
- optional not-before time.

**Prices**

- electricity price entity;
- price adapter, initially `auto` / `Energi Data Service`.

**Charger execution capabilities** (added in the execution phase, all optional)

- charger connected/status entity;
- authorization-needed/authorization-state entity if available;
- authorize action/button;
- start/resume action/button/switch;
- stop/pause action/button/switch;
- charging power sensor if useful;
- actual charging state sensor.

Prefer generic entity/action selection. A Zaptec preset/autodetection is added later only if it materially improves setup.

### Platforms

Use only platforms that improve the HA user experience. Likely V1:

- `sensor.py`
- `binary_sensor.py`
- `button.py`
- `switch.py`
- `number.py`
- `time.py` if a native time entity fits the ready-by setting; otherwise keep ready-by in options initially.

Do not create dozens of entities for internal details. Diagnostic/noisy entities are disabled by default where appropriate.

### V1 entities

Sensors:

- `Charging deficit` - `%`
- `Battery energy deficit` - `kWh`
- `Grid energy required` - `kWh`
- `Required charge duration` - hours/minutes
- `Proposed start`
- `Proposed end`
- `Approved start`
- `Approved end`
- `Estimated charging cost`
- `Estimated SoC at ready time`
- `Plan status`
- optionally `Price source` / `Plan price quality` (`forecast`, `actual`, `mixed`)

Binary sensors:

- `Charge needed`
- `Plan requires approval`
- `Ready to charge`
- optional `Can meet target`

Controls:

- `Smart charging` switch
- `Accept plan` button
- `Reject plan` button
- `Recalculate plan` button
- `Charging power` number if not derived from another entity
- optional `Target SoC` number only when the user chooses an integration-owned target

Use device/entity classes, units, state classes, translation keys, and entity categories correctly.

### Device model

Create one logical Home Assistant device per configured planner instance, e.g. `XC40 Charge Planner`.

The planner device references selected entities but does not claim ownership of the Volvo or Zaptec physical devices.

---

## 11. Persistence and restart behavior

Do not depend on scheduled callbacks alone, because callbacks disappear on restart.

Persist at least:

- approved plan ID/version;
- approved start/end;
- approved target and required energy snapshot;
- proposal ID/version and source quality;
- proposal reason (`initial`, `price_update`, `soc_change`, etc.);
- approval status;
- execution state/markers needed for recovery.

On setup/restart:

1. read persisted state;
2. refresh all selected source entities;
3. validate that the approved plan is still meaningful;
4. if currently inside an approved window, reconcile charger state and resume execution if safe;
5. if the approved window has passed, mark it completed/expired;
6. recalculate when source data changed materially.

Avoid duplicated start/stop actions after restart. Use plan IDs and execution markers for idempotency.

Prefer Home Assistant storage helpers for runtime plan persistence rather than creating arbitrary files.

Tolerate selected entities being temporarily unavailable during HA startup and re-evaluate when they recover. Add Repairs/issues for broken entity selections where appropriate, and diagnostics output with private data redacted.

---

## 12. Price-source abstraction

Protocol:

```python
class PriceSource(Protocol):
    def parse(self, state: State, now: datetime) -> PriceData: ...
```

Normalized model:

```python
@dataclass(frozen=True)
class PriceInterval:
    start: datetime
    end: datetime
    price_per_kwh: Decimal | float
    quality: PriceQuality  # ACTUAL or FORECAST
```

Requirements:

- sort intervals by start time;
- reject overlaps unless explicitly resolved;
- detect gaps;
- preserve timezone;
- support 15-minute and 60-minute intervals;
- never assume attribute ordering is correct;
- never assume tomorrow has 24 hours;
- actual values supersede forecast values for the same interval.

Initial adapter: Energi Data Service/Carnot. It must parse today's actual prices, tomorrow's actual prices, and Carnot forecast intervals; determine when tomorrow's actual prices are valid; merge sources by interval timestamp; mark each interval `ACTUAL` or `FORECAST`; expose plan price quality as actual/forecast/mixed; and detect malformed or insufficient data.

Keep heuristics for attribute names in one module and cover them with fixtures/tests.

### The user selects an entity, never an attribute

Every other input is chosen with a standard entity picker filtered by domain and
device class, because for those the entity **state is the value**: a SoC sensor
reads `42` with unit `%`, and normalization is a unit conversion.

Prices are the one exception, and the reason is worth stating plainly: a price
sensor's state is only the *current* price — a single number. The planner needs the
whole forward curve, which exists solely in the entity's attributes, and Home
Assistant defines no standard schema for it. Energi Data Service, Nordpool, ENTSO-e
and Tibber each name and shape it differently, and there is no device class for
"hourly price curve". A picker therefore identifies the entity but cannot describe
its contents.

That is an implementation problem, not a configuration question. The user must never
be asked what their attributes are called. Instead:

- adapters encode the attribute conventions of specific integrations, derived from
  those integrations' published documentation;
- detection tries each known convention against the selected entity;
- an entity matching no convention is reported as a clear, actionable error — a
  misread price curve yields a confident, plausible, wrong schedule, which is worse
  than refusing to plan;
- the integration reports what it parsed (source, interval count, actual/forecast
  mix) so a user can confirm correctness by reading a sensor.

Energi Data Service exposes `raw_today` and `raw_tomorrow` as timestamp/price
objects, `tomorrow_valid` as a boolean, optional Carnot data under `forecast`, and
critically a `unit` of `MWh`, `kWh` or `Wh` plus a `currency`. The unit must be
normalized: assuming kWh when the sensor reports MWh makes every cost wrong by a
factor of 1000.

---

## 13. Entity-source normalization

Selected source entities may have different units/state formats. Normalize them centrally.

- SoC: require numeric percentage 0..100.
- Capacity: kWh; if source has Wh, convert.
- Power: kW; if source has W, convert.
- Connection: use a configured mapping when a binary sensor is not available.
- Handle unavailable/unknown states explicitly.

Do not scatter string comparisons such as `"connected"` throughout the code.

Prefer event-driven updates from entity state changes. Do not poll vendor integrations independently.

---

## 14. Test coverage requirements

Testing is mandatory for planning and state transitions.

### Pure planner tests

- no deficit;
- 10%, 50%, 100% deficits;
- target below current SoC;
- fractional required interval / partial final interval;
- cheapest window at beginning/middle/end;
- equal-price deterministic tie;
- window crosses midnight;
- actual + forecast mixed horizon;
- actual replaces forecast;
- missing interval/gap;
- insufficient price horizon;
- impossible deadline;
- 15-minute prices;
- hourly prices;
- DST spring-forward day;
- DST fall-back day;
- negative electricity prices;
- very high prices;
- charging efficiency < 1;
- numeric precision/currency summation.

### State machine / orchestration tests

- proposal -> accept -> approved;
- proposal -> reject -> idle/needs-charge;
- accepted plan remains active when new proposal appears;
- accept replacement atomically replaces old plan;
- keep old plan discards replacement;
- unplugged 15 minutes before start -> notification event requested;
- plugged in during approved window -> charging can begin;
- authorize required -> authorize then start;
- already authorized -> do not authorize unnecessarily;
- HA restart before start;
- HA restart during charging;
- HA restart after end;
- entities unavailable then recover;
- charger action failure;
- smart charging disabled while plan exists.

### Config flow tests

Successful setup, invalid selections, duplicate setup policy, options changes, reconfigure/unload behavior, and migrations when the schema changes.

---

## 15. Repository layout

```text
.
├── CLAUDE.md
├── DESIGN.md
├── PLAN.md
├── TODO.md
├── README.md
├── LICENSE
├── hacs.json
├── pyproject.toml
├── .gitignore
├── .github/
│   └── workflows/
│       ├── hacs.yml
│       ├── hassfest.yml
│       └── tests.yml
├── custom_components/
│   └── bitcruise/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── config_flow.py
│       ├── coordinator.py
│       ├── entity.py
│       ├── models.py
│       ├── planner.py
│       ├── price_sources.py
│       ├── source_normalization.py
│       ├── manager.py
│       ├── storage.py
│       ├── sensor.py
│       ├── binary_sensor.py
│       ├── button.py
│       ├── switch.py
│       ├── number.py
│       ├── services.yaml
│       ├── strings.json
│       └── translations/
│           └── en.json
└── tests/
    ├── __init__.py
    ├── conftest.py              # must not import Home Assistant
    ├── test_planner.py          # pure
    ├── test_price_sources.py    # pure
    ├── fixtures/
    └── ha/                      # requires Home Assistant
        ├── __init__.py
        ├── conftest.py
        ├── test_config_flow.py
        ├── test_manager.py
        └── test_restart_recovery.py
```

Delete files/platforms that are not actually needed. Do not create empty architecture for appearance alone.

### Test layout follows ADR-002

Tests are split to match the pure-core/HA-shell boundary:

- `tests/` — pure tests. No Home Assistant import at any point, including in `conftest.py`.
- `tests/ha/` — tests needing a `hass` instance, via `pytest-homeassistant-custom-component`.

This is not cosmetic. Home Assistant cannot be imported on Windows, because `homeassistant.runner` imports the Unix-only `fcntl` module, and the plugin auto-loads through a `pytest11` entry point named `homeassistant`. The split is what lets the pure planner — the layer that matters most — be developed and tested on a Windows workstation:

```bash
pytest -p no:homeassistant --ignore=tests/ha   # pure tests, runs anywhere
pytest                                         # everything, Linux/macOS or CI only
```

If a pure test starts requiring `hass`, that is a signal the planner has grown a Home Assistant dependency it should not have.

---

## 16. HACS and release requirements

Repository must follow HACS integration layout:

- one integration under `custom_components/`;
- `hacs.json` at repository root;
- all runtime integration files inside `custom_components/bitcruise/`;
- custom integration `manifest.json` contains a valid version;
- public GitHub repo is required for HACS distribution;
- use GitHub releases for clean versioned installs once releases begin.

HACS and Hassfest validation workflows are added early, not at the end.

Use semantic versioning unless there is a strong reason not to.

Version milestones:

- `0.1.0` - planning entities + manual approval, no charger execution;
- `0.2.0` - charger execution + restart recovery;
- `0.3.0` - Energi Data Service forecast/actual replacement workflow + notifications;
- `1.0.0` - stable configuration schema, migration path, documented behavior, solid tests.

The first publishable HACS release should be called `0.3.x` or `0.5.x`, not `1.0`, until config schema and behavior have seen real household use.

---

## 17. Future architecture: calendar and trip planning

This is **not V1**, but V1 is designed so it can be added without replacing the planner.

### Product idea

The household uses a shared calendar to reserve/book the car. A booking may contain a trip destination/distance or explicit required distance.

The integration should eventually:

1. read upcoming car bookings;
2. detect booking conflicts;
3. estimate trip energy requirement including return journey;
4. calculate required departure SoC;
5. combine trip requirement with household-configured reserve;
6. plan charging to satisfy that SoC by the trip start;
7. set/request a 100% target for unusually long trips when appropriate, especially when the trip is the next day;
8. warn when the trip cannot be completed without an intermediate charge;
9. eventually model intermediate charging for multi-charge trips.

### Calendar abstraction

Core trip planning must consume the Home Assistant **calendar entity abstraction**, not Fastmail directly.

Initial future configuration:

- user connects Fastmail to Home Assistant using the existing CalDAV integration;
- user selects the resulting calendar entity in BitCruise;
- BitCruise reads events for a look-ahead range and maps them to `CarBooking` objects.

This keeps trip planning compatible with Fastmail, Google Calendar, local calendars, Nextcloud, and other calendar integrations.

Do **not** add Fastmail credentials to BitCruise for the first calendar implementation.

Additional configuration: calendar look-ahead horizon; event parsing mode/convention; default consumption in kWh/100 km; reserve SoC; normal charge target; maximum target; trip-preparation approval policy.

### Booking schema

Do not rely on natural-language parsing alone. Support a simple, documented convention first:

```text
Title: Car: Aarhus
Location: Aarhus
Description:
  distance_km: 310
  return: true
  reserve_soc: 10
```

Optional fields:

```text
start_soc: auto
consumption_kwh_per_100km: auto
allow_fast_charge_stop: false
```

Later, add optional location/geocoding/routing adapters.

Internal model:

```python
@dataclass(frozen=True)
class CarBooking:
    uid: str
    start: datetime
    end: datetime
    organizer: str | None
    attendees: tuple[str, ...]
    distance_km: float | None
    return_trip: bool
    destination: str | None
```

Booking logic: load upcoming events over a defined time range; normalize to `CarBooking`; detect overlaps; determine next required departure time; derive required departure SoC; feed required target/deadline into the existing charging planner.

### Daily commute requirement

The simplest and most useful case needs no calendar at all: the same drive, most days.

The user enters their commute **one way**. BitCruise doubles it for the return leg and
derives what the day actually costs in charge:

```text
commute_km          = one_way_km * 2
commute_energy_kwh  = commute_km * consumption_kwh_per_100km / 100
required_soc_pct    = commute_energy_kwh / usable_capacity_kwh * 100
                      + reserve_floor_pct
```

Details that matter:

- **Label it "one way" unmistakably.** A user who enters the round trip gets a target
  twice too high and will never notice, because nothing about the result looks wrong.
- **Consumption is read from the vehicle** where it exposes it — the reference
  installation reports `17.9 kWh/100km` — and is configurable otherwise. A measured
  average beats a guess, but it is a *past* average: winter consumption is materially
  higher, so a configurable margin belongs here.
- **The reserve floor is added, not compared against.** Arriving home at exactly the
  floor means the commute consumed everything spare. The floor is what remains for
  anything unplanned.
- **This advises, it does not act.** The vehicle target is read-only on the reference
  installation (section 17), so BitCruise reports the state of charge that covers the
  day and whether the configured target reaches it. Changing the target stays the
  user's decision.

Exposed as the energy and percentage required, plus a binary sensor for whether the
current target covers a return commute with reserve intact. That figure is what a
daily target should be set to — usually far below the 80-90% people default to, which
is the point.

The same `trip_energy.py` module serves this and calendar-driven trips; the commute is
just a trip that repeats and needs no booking.

### Trip energy model

Start simple and configurable:

```text
trip_energy_kwh = distance_km * consumption_kwh_per_100km / 100
required_departure_energy = trip_energy_kwh + reserve_energy
required_departure_soc = required_departure_energy / usable_capacity_kwh * 100
```

Outputs: trip energy kWh; minimum departure SoC; recommended departure SoC; whether the trip fits in one charge; expected arrival SoC; whether an intermediate charge is required.

Later modifiers may include temperature, historical vehicle consumption, elevation, speed/route type, trailer/roof box, weather/wind, route service integration, and expected intermediate DC charging.

Keep this in a separate `trip_energy.py` module. Do not mix trip estimation into `planner.py`.

### Planned distance calendar

A per-day view of how far the car is expected to drive, over the calendar look-ahead
horizon. It is a derived read-model over `CarBooking` data — it makes no charging
decisions of its own and must not become a second source of truth.

Purpose:

- see at a glance which days are heavy driving days;
- spot a day whose planned distance exceeds usable range, which implies
  `intermediate_charge_required` before the trip arrives;
- give the household a sanity check on what the planner believes is coming;
- later, compare planned against actually driven distance, which is the natural input
  to the "learn vehicle consumption" backlog item.

#### Aggregation rules

Days are **local** days and must respect DST per section 3.5. A 25-hour day is still
one day.

Each `CarBooking` contributes:

```text
booking_distance_km = distance_km * (2 if return_trip else 1)
```

Decisions that must be explicit rather than incidental:

- **Multi-day bookings.** A booking spanning several days is attributed to its start
  day by default, because the distance is usually driven at departure and return
  rather than spread evenly. Even distribution across the span is a configurable
  alternative, not the default.
- **Unknown distance.** A booking with no `distance_km` contributes `None`, never `0`.
  A day containing one must be reported as *incomplete*, not as a confident total. The
  distinction between "0 km planned" and "we do not know" is load-bearing: only the
  first is safe to plan against.
- **Overlapping bookings.** Distances sum. Overlap is a booking-conflict concern
  (Phase 12), not a distance-aggregation concern; the calendar reports what was booked
  and does not silently resolve conflicts.
- **Unplanned driving.** By definition absent. The calendar shows *planned* distance
  only, and must be labelled as such wherever it is surfaced, so that a low planned
  total is never mistaken for a low expected SoC drain. This is exactly why the reserve
  floor in section 6.4 exists independently of any calendar data.

#### Exposure

Preferred shape is a sensor whose state is the next N days' total or today's planned
distance, with the per-day breakdown in an attribute:

```text
sensor.planned_distance_today       - km, state
  attributes:
    horizon_days: 7
    days:
      - date: 2026-08-10
        distance_km: 310
        complete: true
        bookings: 1
      - date: 2026-08-11
        distance_km: null
        complete: false
        bookings: 1
```

BitCruise could alternatively provide its own Home Assistant `calendar` entity
combining planned drives and planned charging windows. That is attractive for
dashboards, but it means implementing the calendar entity platform and deciding how
charging windows and bookings coexist in one entity. Evaluate it only after the sensor
form has proven useful.

A custom Lovelace card remains a non-goal (section 2); this data must be usable from
standard HA cards.

### Raising the charge target for a long trip

Treat vehicle target mutation as an **optional** actuator capability. On the reference
installation it does not exist: the Volvo integration exposes the target as a
read-only `sensor`, with no `number` or `select` to write. Other vehicle integrations
may expose a writable target, so both paths are supported — but the prompt-based path
is the one that must work, because it is the one that is real today.

#### Remember the normal target first

Before anything is raised, the current target is captured and persisted as
`normal_target_soc`. Without it there is no way to tell the user what to set the car
back to, and no way to detect that they already have. This value is recorded when the
first raise is proposed, not continuously, so that a temporarily raised target is
never mistaken for the normal one.

#### Prompting

When `required_departure_soc` exceeds the current target and the trip falls within the
configured horizon:

- if a writable target actuator is configured, set it, subject to the approval policy;
- otherwise notify the user to raise it manually, and ask for **100%**.

Asking for 100% rather than the exact computed requirement is deliberate. The
requirement is an estimate carrying real error — consumption varies with temperature,
speed and load — and a partial value is fiddly to set in a vehicle app. Rounding up
costs a little battery longevity on one occasion; being 3% short strands the driver.
A setting may later allow requesting the exact figure instead.

No prompt is issued when the target already meets or exceeds the requirement.

#### Verifying the user acted

The target sensor is watched after prompting. Three outcomes:

- **raised in time** — trip preparation is satisfied, and planning proceeds to the new
  target;
- **not raised as departure approaches** — warn again, and make the shortfall explicit
  through `can_meet_target` and `estimated_soc_at_departure`. The plan must never
  imply the trip is covered because a prompt was sent;
- **raised to something between the normal target and the requirement** — treat as
  partial: plan to what was actually set, and keep the shortfall visible.

#### Restoring afterwards

A raised target left in place quietly degrades the battery over months, and the user
who raised it is unlikely to remember. Once the trip has ended — the booking's end
time has passed, or the booking was cancelled — BitCruise:

- exposes `binary_sensor.charge_target_raised`, on for as long as the target remains
  above `normal_target_soc`;
- sends one notification naming the value to restore: *"Trip finished. Charge target
  is still 100%. Set it back to 90%."*

The persistent binary sensor is what carries the state; the notification is a
convenience. A repeated notification would be nagging, and a notification alone would
be missed.

The state clears when the target returns to `normal_target_soc` or below. If the user
sets some other lower value, that becomes the new normal rather than an error — the
household's intent wins, consistent with ADR-003.

Where a writable actuator exists, automatic restoration is available but must be
policy-gated rather than assumed, since silently lowering a target the user raised
deliberately is its own surprise.

Important distinction:

> "Trip requires more energy than normal target" is not the same as "Trip is longer than one battery charge."

For a trip longer than usable battery range, 100% departure SoC helps but does not solve the whole trip. The integration must explicitly mark `intermediate_charge_required = true` rather than implying the trip is covered.

### Booking conflict decisions

Keep **booking conflict decisions** inside this project's domain logic. Replying to invitations is out of scope entirely; see below.

Create a pure module, e.g. `booking_policy.py`:

```python
BookingDecision(
    decision=ACCEPT | DECLINE | NEEDS_REVIEW,
    reason=...,
    conflicts=(...),
)
```

Initial deterministic policy:

- no overlap with an accepted reservation -> ACCEPT;
- overlap -> DECLINE;
- missing/ambiguous times -> NEEDS_REVIEW;
- simultaneous tentative events -> configurable/manual review initially.

Potential later policy: household member priority; buffer time before/after booking; booking modification semantics; minimum charge/travel turnaround; destination-dependent handover time.

The charging planner is never responsible for parsing invitation emails.

### Replying to invitations is out of scope

BitCruise decides whether a booking conflicts. It never replies to the invitation.

That is a deliberate boundary, not a deferral. Answering an invitation means speaking a
provider's scheduling protocol, holding that provider's credentials, and tracking a
mutation lifecycle that has nothing to do with charging a car. A separate lightweight
project owns it and consumes the decision.

What stays here is the part that is genuinely car-domain: given the bookings, does this
request conflict, and what does that mean for vehicle availability and charging
deadlines. That is testable from fixtures with no network access and no provider code
at all, which is exactly why it belongs on this side of the line.

The charging planner is likewise never responsible for parsing invitation emails.

---

## 18. Multiple vehicles and shared resources

### Current state

V1 supports exactly **one vehicle**. This is enforced, not merely undocumented:
`manifest.json` sets `single_config_entry: true`, so Home Assistant will not allow a
second BitCruise entry to be created.

That enforcement is deliberate. The naive multi-car implementation — let the user add
one config entry per car and run independent planners — quietly produces wrong and
occasionally disruptive behavior, because the cars are not actually independent.

### Why independent planners are not enough

Two planners optimizing separately against the same inputs converge on the same
answer, which is precisely the problem:

| Shared resource | What goes wrong |
| --- | --- |
| The cheapest price window | Both cars independently choose the same window. Every night. Contention is guaranteed, not occasional. |
| One charger | Two approved plans claim the same charger at the same time. The second car silently gets nothing, and the plan still reports success. |
| House supply / main fuse | Two chargers at 10 kW each may exceed what the connection can carry. This trips a breaker rather than degrading gracefully. |
| Charge priority | Nothing decides which car gets the cheap window. Ordering ends up determined by config entry creation order, which is invisible to the user. |

Only the fully independent case — two cars, two chargers, ample supply headroom —
works correctly with naive multi-entry, and there is no way for the integration to
verify it is in that case.

### Target architecture

Multi-vehicle is a **coordination layer above N planners**, not a change to the
planner. The planner stays single-vehicle and pure, exactly as ADR-002 requires.

```text
Vehicle A state ─┐
                 ├─► Planner A ─► requirement A ─┐
Vehicle B state ─┘                               │
                                                 ├─► Allocator ─► approved schedules
Prices ──────────────────────────────────────────┤                (non-conflicting)
Constraints (charger count, supply limit) ───────┘
```

The allocator receives each vehicle's *energy requirement and deadline* rather than a
finished window, and solves the assignment problem across shared constraints:

- assign charging windows so no charger is double-booked;
- keep concurrent draw within a configured household power limit;
- apply a deterministic priority rule when demands collide;
- return one schedule per vehicle, each still subject to the normal approval flow.

Priority must be explicit and configurable rather than emergent. Candidate rules, in
rough order of usefulness: earliest deadline first; explicit per-vehicle priority;
reserve-floor breaches (section 6.4) before any `NORMAL` demand; largest deficit first
as a tie-break.

### Config entry model

Two viable shapes, to be decided when the work is scheduled:

- **One config entry per vehicle**, plus household-wide settings (supply limit,
  priority) held separately. Matches how users think, and the standard Home Assistant
  pattern, but household constraints have no natural home and the entries must
  discover one another.
- **One entry with per-vehicle subentries.** Household constraints live on the parent
  entry, which is architecturally cleaner. Requires the config subentry APIs — verify
  their current state against Home Assistant developer documentation before choosing,
  rather than assuming availability.

Relaxing `single_config_entry` later is a backwards-compatible change: existing
single-vehicle installations keep working and gain the ability to add a second.

### Interaction with other features

- **Calendar bookings** (section 17) become per-vehicle. A `CarBooking` must identify
  *which* car it reserves, so the booking convention needs a vehicle field once more
  than one vehicle exists.
- **Reserve floor** (section 6.4) is per-vehicle, but a floor breach on one car
  competes with a normal plan on another. The priority rule must state which wins.
- **Planned distance calendar** (section 17) becomes per-vehicle with an optional
  household total.

### Until then

A household with two cars today has one supported option: run BitCruise for the car
whose charging benefits most from optimization, and handle the other manually. The
README must say this plainly rather than leaving users to discover the restriction in
the config flow.

---

## 19. Architecture decision records

### ADR-001 - Do not couple V1 to vendor APIs

**Decision:** consume existing HA entities and invoke HA actions.

**Why:** Volvo, Zaptec, and Energi Data Service are already integrated with HA. Direct vendor access would duplicate authentication, create more breakage points, and make the project less reusable.

### ADR-002 - Planner is pure Python

**Decision:** charging optimization has no Home Assistant dependency.

**Why:** easier tests, deterministic behavior, future reuse, less restart/event-loop complexity.

### ADR-003 - Accepted plan is immutable until explicit replacement

**Decision:** a recalculation creates a proposal rather than silently mutating the approved schedule.

**Why:** household intent beats small price optimization and avoids surprising charger behavior.

### ADR-004 - Calendar input uses HA calendar entities first

**Decision:** future Fastmail bookings enter through HA's CalDAV integration initially.

**Why:** provider independence and no duplicated credential handling.

### ADR-005 - Replying to invitations is out of scope

**Decision:** BitCruise may decide whether a car booking conflicts, and exposes that decision. It never accepts or declines an invitation. A separate project owns that and consumes the decision.

**Why:** conflict logic is car-domain logic and testable from fixtures. Replying means speaking a provider's scheduling protocol, holding its credentials, and tracking a mutation lifecycle — none of which has anything to do with charging a car, and all of which would have to be maintained here forever.

### ADR-006 - Trip requirements feed the same charging planner

**Decision:** calendar/trip features produce a required target SoC and ready-by deadline. They do not create a second charging optimizer.

**Why:** one source of truth for charging behavior.

### ADR-007 - The reserve floor is separate from the charge target

**Decision:** availability ("can we drive now?") and readiness ("ready by 07:00?") are
two independent SoC requirements. The floor alters the planner's search window and
objective; it never alters how much energy the target requires. `reserve_floor_pct`
enters the domain model in V1 even though urgency-aware planning lands later.

**Why:** cost optimization deliberately parks the battery low until just before a
deadline, which is exactly wrong for unplanned trips. Retrofitting a second SoC
concept would later force changes to `PlanningInput`, `ChargePlan`, persistence, and
the approval state machine simultaneously.

### ADR-008 - Multi-vehicle support is an allocation layer, not N planners

**Decision:** the planner stays single-vehicle and pure. Multiple vehicles are handled
by a coordination layer that receives per-vehicle requirements and resolves shared
constraints — charger availability, household supply limit, and priority — before
schedules are proposed. V1 enforces one vehicle via `single_config_entry`.

**Why:** independent planners fed identical prices converge on identical windows, so
contention is systematic rather than rare. Double-booking a charger or exceeding the
supply limit are failures the planner cannot detect from inside a single vehicle's
context. Blocking the second entry is more honest than shipping a configuration that
appears to work and silently fails to charge a car.

---

## 20. Reference documentation

Current official documentation is the source of truth. Re-check during implementation; this project intentionally targets moving platforms.

- Home Assistant developer docs: https://developers.home-assistant.io/
- Config flows: https://developers.home-assistant.io/docs/core/integration/config_flow/
- Integration quality scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
- Calendar entity: https://developers.home-assistant.io/docs/core/entity/calendar/
- Home Assistant CalDAV integration: https://www.home-assistant.io/integrations/caldav/
- HACS integration publishing: https://www.hacs.xyz/docs/publish/integration/
- HACS custom repositories: https://www.hacs.xyz/docs/faq/custom_repositories/
- Fastmail developer API: https://www.fastmail.com/dev/
