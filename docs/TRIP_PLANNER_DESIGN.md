# Trip Planner — Design Notes

Status: **built**. The planner ships as a section on the dashboard's Now panel,
backed by a shared core, a plan store and collector-driven alerting. These notes
record the design decisions and the measurement lessons that motivated them, so
the reasoning behind the code is not lost. Section 8 keeps the questions that
are still genuinely open.

Examples in this document use anonymised placeholders (distances and energy
figures are real magnitudes from the data; place names and coordinates are
deliberately omitted — this is a public repository).

---

## 1. What the planner answers

> Given where the battery is now, what still has to be driven before departure,
> and how fast the charger actually refills the pack: **is the trip reachable
> without a DC fast charge — and if not, how large a stop does it need, and
> where in the schedule does it fit best?**

Secondary questions, in priority order:

1. What state of charge will I depart with, and arrive with?
2. How many plugged-in hours do I need per day between now and departure
   (the *duty cycle*), and is that realistic for my habits?
3. If a fast charge is unavoidable, how many minutes and roughly what cost?
4. How does the picture change with the season / forecast temperature?

## 2. What already exists

| Piece | Where | State |
|---|---|---|
| Planning core | `src/utils/trip_planner.py` | done, tested |
| Destination detection | `GET /api/planner/destinations` | done |
| Reachability assessment | `GET /api/planner/assess` | done |
| CLI checker (cron + NATS alerts) | `tools/trip_readiness_check.py` | thin wrapper over the shared core; the duplicated logic was unified |
| Plan store | `src/storage/plan_store.py` | named plans in `data/trip_plans.json`, fcntl-locked |
| Plan CRUD | `GET/POST/DELETE /api/planner/trips` | done |
| Temperature bands | `src/utils/efficiency.py` | shared by the chart and the planner |
| Weather forecast | `src/utils/weather.py` | `get_forecast` / `forecast_for`, horizon follows the departure |
| Alert decision | `notify_on_change` in the core | transitions only, plus a daily reminder; records nothing it failed to send |
| Alert delivery | `trip_readiness_check.py --watch`, host cron | on the host: NATS binds loopback there and its CLI is not in the container |
| UI | Now panel, `planner-section` | done |

## 3. Principles, each learned the hard way

These are the load-bearing decisions. Every one of them replaced an assumption
that produced a materially wrong answer during live use.

### 3.1 Measure, never assume

- **Charger throughput is pack-side, not wall-side.** The L1 charger reports
  ~1.35 kW at the plug; only ~0.95 kW reaches the pack (~70% efficiency,
  normal for 120V). Planning on the wall figure made every hours-to-charge
  estimate ~30% optimistic and once produced a "no fast charge needed" verdict
  for a trip that required one. DC fast measured ~86% (session receipt kWh vs
  SOC gain). Public L2 sits between. Every charger type the planner models
  must carry its own measured efficiency.
- **Trip energy comes from past runs of the same route** where any exist.
  Highway consumption on a real ~155 mi leg measured ~306 Wh/mi against a
  ~256 Wh/mi band average — band averages mix town driving and understate
  long trips badly. A fixed default (340 Wh/mi) is used only when there is no
  history and no forecast adjustment.
- **Short local trips are far worse than averages suggest** (~550 Wh/mi
  observed: cold starts, climate, low speed). A day of errands at the
  destination costs disproportionately more per mile than the drive there.

### 3.2 Derive progress from state of charge, not from logs

Trip logs and the odometer both lag the battery reading — sometimes by hours.
Mid-trip, prorating "planned energy" against logged miles double-counted a
round trip against a battery that had already paid for it, producing a phantom
118%-duty alarm and a 3× overestimate of the DC time needed. SOC deltas come
from the same row as the battery level, so they cannot lag it. Rules:

- Consumption for "today" = sum of SOC **drops** only (netting rises off would
  let an afternoon of charging mask the morning's driving).
- Consumed energy is charged against the day's planned events in order, so a
  finished trip stops counting even before any log catches up.

### 3.3 Combine sources; each has blind spots

Neither the SOC series nor the trip log records a day completely, and they
fail on *different* days:

- SOC misses energy when a charge starts between polls (observed: pack reached
  14%, next poll saw 37% already charging — the gap vanishes from SOC deltas).
- The trip log misses energy when a leg's distance is recorded without its
  consumption (observed: 178 mi logged against 9.6 kWh).

`day_energy_combined()` takes the per-day **maximum** of the two. Both measure
the same quantity, so the max cannot overstate.

### 3.4 Don't attribute a day's energy to a destination automatically

A day's total is only a fair proxy for a journey when the day held nothing
else — and that cannot be determined from the data. The busiest day at the
~230 km destination totals 84.5 kWh, more than the pack holds (other driving
plus an intervening fast charge). Failed approaches, kept here so they are not
retried:

1. *Median of visit days* — understates long trips (short-visit days dilute).
2. *Max of visit days* — nonsense for near destinations (a 44 kWh day
   "attributed" to a ~15 kWh errand run).

Current compromise: return **every visit day with its own figure**, flag days
exceeding pack capacity, and suggest a default — largest plausible day for
journeys ≥ 80 km (the trip dominates its day), median for shorter ones (the
day dominates the trip). The UI must let the user pick a specific visit day.

### 3.5 Match the season — annual medians describe nothing

A recurring weekday errand measured 8.0 kWh (May–Sep) vs 12.2 kWh (Nov–Mar);
the all-year median of 9.6 fits neither half. The elevated winter figure is
cold-weather efficiency, **not** idling — verify the story behind a number
before encoding it. `seasonal_daily_energy()` prefers same-weekday-same-season
(n ≥ 4), then same-season, then all days, and reports which basis it used so
the UI can display confidence honestly.

### 3.6 Prefer the freshest sensor per question

- *Is the car plugged in?* → smart-plug webhook (5-min samples), falling back
  to the vehicle poll (90–150 min) when the feed is stale/missing. A stale
  vehicle poll once reported "NOT plugged in" for a car 36 minutes into a
  session.
- *State of charge?* → vehicle only; nothing else knows it.
- Always surface reading age; warn when stale rather than presenting old data
  as current.

### 3.7 Report uncertainty instead of drawing lines through noise

Whole-percent SOC puts ±2% on a typical measurement. Where the data cannot
support a conclusion (see the battery-health endpoint's `below_noise_floor`
verdict), say so explicitly. For the planner: round trips to whole minutes of
DC, not decimals, and state the basis ("measured from 3 past runs" vs
"estimated from distance").

### 3.8 Duty cycle is the actionable output

"Need 29 plugged hours out of the 59 remaining (50% duty)" proved far more
usable than a bare kWh deficit. Thresholds from observed habits: ≤55% duty =
on track, 55–75% = tight, >75% = off track (13–18 plugged h/day is realistic;
beyond that assumes charging every parked hour). These belong in config, not
code.

### 3.9 Frame outputs as range and arrival %, not multipliers

"1.88× energy per mile" and "47% less range" are the same fact; only the
second tells you whether you can get home. Prefer projected departure %,
arrival %, and miles. The 80%/100% two-tone treatment on the efficiency charts
(solid bar to the 80%-charge range, lighter cap to 100%) is the established
visual language — reuse it for "is this destination inside the solid bar".

## 4. UI (built)

Decisions taken, and what shipped:

- **Trip input: both modes, known destinations first.** A picker of detected
  destinations (from `/api/planner/destinations`) showing distance, visit
  count, per-visit energy, and the suggested figure — plus a manual distance
  entry for anywhere new.
- **Scope: single-trip readiness first.** One departure datetime + one
  destination. The full week-planner (several days of driving plus a trip) is
  a later iteration; the assessment endpoint already accepts the pieces.

Proposed layout (Planner as a fifth tab, or a section on Now):

```
[ destination picker ▾ | or distance ____ mi ]   [ departure date/time ]
[ charger: L1 0.95 kW ▾ ]                        [ arrival buffer: 10% ]

  ┌────────────────────────────────────────────────┐
  │  ON TRACK — depart ~95%, arrive ~23%           │
  │  needs 19 plugged hours of the 36 remaining    │
  │  basis: measured from 4 past visits · warm-    │
  │  season weekday usage (n=28)                   │
  └────────────────────────────────────────────────┘

  [ SOC trajectory sparkline: now → departure → arrival ]
  [ if short: "≈13 min DC fast (~$9) — cheapest late in the
    schedule when SOC is low" ]
```

Requirements carried over from the rest of the dashboard:

- WCAG AAA: computed contrast, no colour-only state, 44px targets, keyboard
  operable, status conveyed in text ("ON TRACK") not colour alone.
- Respect both unit toggles (distance and temperature are independent).
- Show the reading age of the SOC it planned from.

## 5. Weather integration (built)

Shipped broadly as planned. Two things the plan got wrong, worth recording:

- The horizon cannot be fixed at seven days. The form defaults to a departure a
  week out, which fell one day outside a seven-day request and silently fell
  back to the flat default. `forecast_for` now asks for as many days as it
  takes to reach the departure, capped at Open-Meteo's ~16.
- Forecasts get their own cache duration (3 h) rather than the 30 minutes
  current conditions use.

The original plan, which the implementation follows:

1. Add `get_forecast(lat, lon, days)` using the same Open-Meteo endpoint
   (`daily=temperature_2m_max,temperature_2m_min,precipitation_probability`)
   and cache discipline. Open-Meteo's horizon (~7 days) comfortably covers an
   L1 planning window (3–5 days).
2. Assessment uses forecast temperature at departure to pick the Wh/mi from
   the measured temperature bins (energy-weighted, already exposed by
   `/api/temperature-efficiency`) instead of the fixed default.
3. Blend rule when a route has history: scale the measured route figure by
   (bin Wh/mi at forecast temp ÷ bin Wh/mi at the temps the history was driven
   at). Keeps the route's character (sustained highway ≠ band average) while
   adjusting for season.
4. Precipitation: literature penalty is ~5–15%; we have no measured wet-road
   figure yet. Show as a stated assumption, and log trips with forecast so a
   measured figure can eventually replace it.
5. Daily-usage seasonality already handles "weather" implicitly for the
   between-days driving; the forecast only needs to touch the trip leg itself.

## 6. Charging-stop advice (later iteration)

Lessons worth encoding when this gets built:

- A stop is cheapest in time when SOC is low (curve is flattest); a stop late
  in the outbound leg beats topping up before departure.
- Compare $/kWh **into the pack** (rate ÷ measured efficiency), not posted
  rate: a $1/h 4 kW L2 ≈ $0.28/kWh delivered vs ~$0.76 DC — but combined
  parking+charging near $2.50/h reaches break-even with DC.
- Slow destination charging changes the *next* leg's requirement; hours parked
  at a destination are an input, not trivia.
- Never recommend a specific charger the car has no history with; surface
  "you have charged here before, peak X kW" where history exists, and
  otherwise direct the user to a live-status source. A recommendation to stop
  at an assumed-midpoint charger with no verification was a real mistake made
  during manual planning.

## 7. Data quality prerequisites

- `energy_source` (metered vs derived) now tags charging sessions; derived
  rows equal SOC × configured capacity *by construction* and must never feed
  capacity or efficiency measurement. 620 of 791 historical rows were derived.
- `trips.csv` distance is **miles** (verified against the km odometer over
  354 driving days); several historical mislabels came from assuming km.
- Concurrent writes are guarded: `CSVStorage._write_lock` and
  `PlanStore._write_lock` both take an fcntl lock on a sidecar file. The
  planner's one write path (the plan store) uses it, and an eight-process test
  asserts nothing is lost.

## 8. Questions, answered and still open

Resolved by the build:

1. **Where it lives** — a section on Now, not a fifth tab.
2. **Persistence** — plans persist and are re-assessed every collector cycle.
   The alerting hazard is worth remembering: the cron checker announced *every*
   off-track run, fine four times a day and intolerable on a cycle that can run
   every fifteen minutes. Only transitions are published, plus one reminder a
   day, with the last status persisted so a restart does not re-announce.

   Where alerting *runs* was decided by deployment, not preference. The
   collector cannot reach NATS at all: the server binds loopback on the host
   and the CLI is not in the image. Worse than failing, a container-side
   attempt would still mark the status as seen and hide the transition from
   anything that could publish. So the decision lives in the shared core and
   delivery lives in a host cron, and a status is only recorded once its
   announcement has actually gone out.
5. **Charge limit** — an explicit input, defaulting to 80%. Note the asymmetry
   found in live use: a limit caps charging but does not discharge a pack that
   is already above it, so the projection floors at the current SOC.

Still open:

3. Multi-day trips: model destination charging (rate × parked hours) as an
   input? The single-trip model treats the return as a separate assessment.
   Live experience says this matters most where there is no fast charging at
   the destination, which is exactly when the return leg decides the plan.
4. Should destination names be user-editable labels? Presets currently render
   as "230 km away — 6 visits", which is safe in a screenshot but hard to tell
   apart when two places are a similar distance out.
6. When history and forecast disagree strongly (e.g. measured summer route,
   winter departure), how loudly should the adjustment be surfaced? Today the
   basis string states it and nothing escalates.
7. Precipitation is stated but never applied, for want of a measured wet-road
   figure. Logging trips against their forecast would eventually produce one.

## 9. Privacy

The repository is public. Rules already in force, which the planner UI must
preserve:

- `tools/trip_plan.json` (real plans: places, dates) is gitignored; only the
  anonymised example ships. The planner must never write real destinations,
  coordinates, or schedules into tracked files, fixtures, or docs.
- Detected destinations exist only in API responses computed at runtime from
  local data — never persisted to the repo.
- Screenshots in issues/PRs should use the coarse auto-labels, not addresses.
