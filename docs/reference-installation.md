# Reference installation

What the real entities look like on the installation BitCruise is developed against.
Captured **2026-08-09** from Home Assistant OS 2026.8.1.

This exists so design decisions can be checked against reality instead of assumptions,
and so a future session does not have to ask for this again. Entity names differ
between installations — nothing here may be hard-coded. It is evidence about the
*shape* of the data, not configuration.

> **Sanitized.** No VIN, GPS coordinates, address, charger PIN, serial numbers,
> installation IDs, MAC, IMEI/ICCID/IMSI, or keys appear here, and none may be added.
> The charger's own entity ID is written as `<charger>` because the name is arbitrary.
>
> **Never paste `binary_sensor.<charger>` attributes anywhere.** The Zaptec integration
> puts the charger PIN, cellular identifiers, MAC address, meter public key, and the
> site address into that single entity's attributes.

---

## Vehicle — Volvo XC40 Recharge, via the Home Assistant Volvo integration

### Entities BitCruise uses

| Entity | Example | Unit / class | Role |
| --- | --- | --- | --- |
| `sensor.volvo_xc40_battery` | `47.0` | `%`, `device_class: battery` | State of charge |
| `sensor.volvo_xc40_battery_capacity` | `81.608` | `kWh`, `device_class: energy_storage` | Usable capacity — **discoverable, not user-entered** |
| `sensor.volvo_xc40_target_battery_charge_level` | `90` | `%`, **no device class** | Charge target — **read-only** |
| `sensor.volvo_xc40_charging_connection_status` | `disconnected` | `enum`: `connected` / `disconnected` / `fault` | Plug status |
| `sensor.volvo_xc40_charging_status` | `idle` | `enum`: `charging` / `discharging` / `done` / `error` / `idle` / `scheduled` | Charge state |
| `sensor.volvo_xc40_car_connection` | `available` | `enum`: `available` / `car_in_use` / `no_internet` / `ota_installation_in_progress` / `power_saving_mode` | **Data freshness** |

### Entities useful later

| Entity | Example | Unit | Role |
| --- | --- | --- | --- |
| `sensor.volvo_xc40_distance_to_empty_battery` | `260` | `km` | **Preferred** trip energy input — live, temperature-aware |
| `sensor.volvo_xc40_trip_manual_average_energy_consumption` | `17.9` | `kWh/100km` | Trip energy fallback: a past average |
| `sensor.volvo_xc40_odometer` | ~44 000 | `km` | Planned vs actual distance |
| `sensor.volvo_xc40_estimated_charging_time` | `0` | `min` | Cross-check on predicted duration |
| `sensor.volvo_xc40_charging_power` | `0` | `W` | Observed charge rate |
| `binary_sensor.volvo_xc40_engine_status` | `off` | `running` | Car in use |

### Traps

- **`sensor.volvo_xc40_charging_limit` is amps (`32 A`), not a target percentage.**
  It looks like a charge limit and is not one. The target is
  `target_battery_charge_level`.
- **The target sensor has no `device_class`.** An entity picker filtered on
  `device_class: battery` will not show it. Filter on `%` unit, or on domain alone.
- **`charging_connection_status` is a `sensor`, not a `binary_sensor`**, and has a
  third state `fault` that is neither connected nor disconnected. Collapsing `fault`
  into "disconnected" hides a real charging failure.

### Not available

- **No writable charge target.** The target is exposed as a read-only `sensor`; there
  is no `number` or `select` for it.
- **No car-side charge control.** The only buttons are climatisation, honk, flash and
  lock. Starting and stopping charging must go through the charger.
- The remaining entities are body, lights, tyres, doors, service intervals, lock and
  location — none relevant here.

---

## Charger — Zaptec Go 2, via the Home Assistant Zaptec integration

| Entity | Example | Role |
| --- | --- | --- |
| `sensor.<charger>_charger_mode` | `connected_requesting` | **Authoritative execution state.** `device_class: enum`, and its `options` attribute declares exactly: `unknown` / `disconnected` / `connected_requesting` / `connected_charging` / `connected_finished`. Verified against the live entity. |
| `binary_sensor.<charger>_authorization_required` | `on` | Whether authorization is needed |
| `button.<charger>_authorize_charging` | — | Authorize |
| `button.<charger>_deauthorize_charging` | — | Deauthorize |
| `button.<charger>_resume_charging` | `unavailable` | Start / resume |
| `button.<charger>_stop_charging` | `unavailable` | Stop / pause |
| `switch.<charger>_charging` | `unavailable` | Alternative start/stop |
| `sensor.<charger>_charge_power` | `0` | Charge power, in **W** |
| `sensor.<charger>_session_total_charge` | `0.0` | Session energy, kWh |
| `number.<charger>_charger_max_current` | `16` | Writable current limit, A |
| `binary_sensor.<charger>_online` | `on` | Charger reachable |

### Traps

- **Control entities are `unavailable` while the car is unplugged.** `resume`, `stop`
  and `switch.charging` were all unavailable in this snapshot. An unavailable control
  means "cannot act yet", not "failed" — and a configured action cannot be assumed
  callable at plan start.
- **The dashboard shows a translated label, not the state.** `charger_mode` renders as
  "Waiting" in the UI while its actual state is `connected_requesting`. Match the raw
  state; never the word on the screen. The same trap applies to any `device_class: enum`
  sensor, and it is invisible until a comparison silently stops matching.
- `charge_power` is **W**; the planner works in kW.
- Real capability is **16 A three-phase ≈ 11 kW**, not the 10 kW originally assumed.
  `charger_max_current` is writable, so power should be configurable.
- `charger_mode` plus `authorization_required` give a clean state machine. Prefer them
  over pressing buttons blindly, per DESIGN.md section 9.

---

## Prices — Energi Data Service v1.6.20 with Carnot

Sanitized snapshot: [`tests/fixtures/energidataservice.json`](../tests/fixtures/energidataservice.json).

| Attribute | Example | Notes |
| --- | --- | --- |
| state | `0.45676235125` | Current hour only — the forward curve is in attributes |
| `raw_today` | 24 × `{hour, price}` | `hour` is ISO-8601 **with local offset**; the key is `hour`, not `start` |
| `raw_tomorrow` | 24 × `{hour, price}` | Empty until `tomorrow_valid` |
| `forecast` | ~8 days × `{hour, price}` | Carnot; same shape |
| `tomorrow_valid` | `true` | Gate for next-day actual prices |
| `unit` | `kWh` | Can also be `MWh` or `Wh` — **must be normalized** |
| `currency` | `DKK` | Carry through to the cost sensor |
| `use_cent` | `false` | Must be honoured |
| `unit_of_measurement` | `DKK/kWh` | |
| `device_class` | `monetary` | Usable as a picker filter |
| `tariffs` | hourly table + fixed | See open question below |

### Traps

- Timestamps parse directly via `datetime.fromisoformat` and are already aware. They
  must still go through `to_utc` before any comparison or subtraction.
- In this snapshot `forecast` began the day *after* tomorrow, so it did not overlap the
  actual prices. **Do not rely on that.** When `tomorrow_valid` is false the forecast
  covers tomorrow, and actual must supersede forecast per interval.

### Resolved: tariffs are already included in the price

**Energi Data Service applies tariffs and surcharges itself.** The exposed price is
the true all-in price paid per kWh.

Confirmed 2026-08-09 by the installation owner, and consistent with the data: the
20:00 → 21:00 drop of `0.277` closely tracks the `0.263` tariff step.

Consequences:

- **Never add the `tariffs` attribute to the price.** Doing so double-counts and
  inflates estimated cost by roughly 40%.
- The `tariffs` attribute is informational only. It may be useful later for explaining
  *why* a window is expensive, but it must not enter the cost calculation.
- The hourly tariff table already shapes the curve — `0.11` overnight against `0.428`
  for 17:00–21:00 — so the peak/off-peak signal the optimizer needs is present in
  `raw_today` without any extra work.
- A price source that does *not* include tariffs would need a different adapter, and
  that difference must be a property of the adapter rather than a user setting.

---

## Conclusions that shaped the design

1. **The charge target is readable but not writable.** BitCruise can read `90%` and
   plan to it. The future "raise the target to 100% for a long trip" behaviour cannot
   set it, and must notify the user to change it manually — which is exactly the
   optional-actuator model in DESIGN.md section 17.
2. **The car already predicts its own range**, and recalculates it as the temperature
   and recent driving change. `distance_to_empty_battery` against the current state of
   charge answers "what does 100 km cost me" without needing battery capacity or any
   consumption figure at all, and without going stale as the battery ages. The
   measured `17.9 kWh/100km` is the fallback, not the primary input: it is a past
   average, and the range estimate is the manufacturer's prediction of what is next.
   See `DESIGN.md` §17, "Distance to empty is the better input".
3. **`car_connection` is a staleness signal.** `no_internet` and `power_saving_mode`
   mean the SoC reading may be old. Under DESIGN.md section 3.4, planning on a stale
   SoC is precisely the class of mistake that must be refused rather than guessed at.
4. **Capacity is discoverable**, so a manual kWh entry is an override, not a
   requirement.
5. **All charge control goes through the charger**, since the car exposes none.
