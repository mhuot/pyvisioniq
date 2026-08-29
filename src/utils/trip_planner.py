"""Plan whether a trip is reachable on the charging available before departure.

Answers the question that matters before a long drive: given where the battery
is now, what still has to be driven, and how fast the charger actually refills
the pack, is the trip achievable without stopping to fast charge.

Everything is measured rather than assumed:

* Trip energy comes from days the car actually went there, derived from state of
  charge so it captures legs the trip log missed.
* Daily usage is matched to the season. An annual median describes neither half
  of the year: lesson Wednesdays run 8.0 kWh in summer against 12.2 in winter.
* Charge rate is energy reaching the pack, which on 120V L1 is about 70% of what
  the plug reports.
"""

import os
from datetime import datetime

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.60934

# Two endpoints within this distance are treated as the same place. Wide enough
# that separate stops in one city -- a charger, a hotel, a restaurant -- group
# as one destination rather than three.
CLUSTER_RADIUS_KM = 10.0
# Somewhere has to be visited this often before it is worth offering as a preset.
MIN_VISITS = 2
# Anything closer than this is local driving, not a destination.
MIN_DISTANCE_KM = 20.0
# Beyond this a journey dominates whatever else happens that day.
LONG_TRIP_KM = 80.0

WARM_MONTHS = (5, 6, 7, 8, 9)
COLD_MONTHS = (11, 12, 1, 2, 3)

# A day realistically offers 13-18 h plugged in. Beyond that a plan depends on
# charging essentially every hour the car is parked.
DUTY_ON_TRACK = 0.55
DUTY_TIGHT = 0.75

# The smart plug reports every 5 minutes; the vehicle is polled every 90-150.
# Whether the car is plugged in is therefore far better answered by the plug,
# and a stale vehicle reading has already produced a "NOT plugged in" warning
# for a car that had been charging for half an hour.
PLUG_FRESH = pd.Timedelta(minutes=15)
PLUG_CHARGING_WATTS = 100.0

# Beyond this the collector is probably down, and a plan computed from a stale
# state of charge is worse than no answer because it looks current.
DEFAULT_MAX_READING_AGE_HOURS = 3.0


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    radians = np.radians
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = (
        np.sin(d_lat / 2) ** 2
        + np.cos(radians(lat1)) * np.cos(radians(lat2)) * np.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def energy_by_day(battery_df, pack_kwh):
    """Energy drawn from the pack per calendar day, from SOC drops.

    Derived from the battery readings rather than the trip log, which lags and
    can miss whole legs -- the June Duluth run recorded a 7 mile arrival and not
    the 157 miles that preceded it. Only drops are summed; netting rises off
    would let an afternoon on the charger mask the morning's driving.
    """
    frame = battery_df.dropna(subset=["timestamp", "battery_level"]).sort_values("timestamp")
    if frame.empty:
        return pd.Series(dtype=float)
    frame = frame.assign(day=frame["timestamp"].dt.date)
    drops = frame.groupby("day")["battery_level"].apply(
        lambda levels: -levels.astype(float).diff().clip(upper=0).sum()
    )
    return drops / 100.0 * pack_kwh


def day_energy_combined(trips_df, battery_df, pack_kwh):
    """Best available energy per day, taking the larger of two sources.

    Neither source is complete on its own, and they fail on different days.
    State of charge misses energy when a charge starts between polls: on the
    June Duluth run the pack reached 14% but the poller next saw 37%, already
    charging. The trip log misses energy when the car reports a leg's distance
    without its consumption: the same day logged 178 miles against 9.6 kWh.
    The following day the trip log was complete at 175 miles and 56.4 kWh.

    Taking the maximum covers each source's blind spot. It cannot overstate a
    day, since both are measurements of the same thing.
    """
    soc_based = energy_by_day(battery_df, pack_kwh)

    trips = trips_df.dropna(subset=["date"]).copy()
    trips["total_consumed"] = pd.to_numeric(trips["total_consumed"], errors="coerce")
    logged = trips.assign(day=trips["date"].dt.date).groupby("day")["total_consumed"].sum() / 1000.0

    combined = pd.concat([soc_based, logged], axis=1).max(axis=1)
    return combined.dropna()


def find_destinations(trips_df, battery_df, pack_kwh, home=None):
    """Group past trip endpoints into destinations with their measured cost.

    Each visit is returned with what that whole day cost, and the caller chooses
    which to plan against. A single automatic figure is not offered because a
    day's energy only describes the journey when the day held nothing else, and
    that cannot be determined from the data: the busiest Duluth day totals more
    than the pack capacity.
    """
    frame = trips_df.dropna(subset=["end_latitude", "end_longitude", "date"]).copy()
    if frame.empty:
        return []

    if home is None:
        # The most frequented endpoint is home.
        rounded = frame.assign(
            key=frame["end_latitude"].round(2).astype(str)
            + ","
            + frame["end_longitude"].round(2).astype(str)
        )
        top = rounded["key"].value_counts().idxmax()
        home_rows = rounded[rounded["key"] == top]
        home = (home_rows["end_latitude"].median(), home_rows["end_longitude"].median())

    frame["from_home_km"] = haversine_km(
        frame["end_latitude"], frame["end_longitude"], home[0], home[1]
    )
    away = frame[frame["from_home_km"] >= MIN_DISTANCE_KM].copy()
    if away.empty:
        return []

    daily_energy = day_energy_combined(trips_df, battery_df, pack_kwh)

    # Greedy clustering: seed on the furthest-visited points and absorb anything
    # within CLUSTER_RADIUS_KM. Good enough for a handful of regular places, and
    # it avoids a dependency for what is effectively a proximity grouping.
    clusters = []
    for row in away.sort_values("from_home_km", ascending=False).itertuples():
        for cluster in clusters:
            if (
                haversine_km(row.end_latitude, row.end_longitude, cluster["lat"], cluster["lon"])
                <= CLUSTER_RADIUS_KM
            ):
                cluster["rows"].append(row)
                break
        else:
            clusters.append({"lat": row.end_latitude, "lon": row.end_longitude, "rows": [row]})

    destinations = []
    for cluster in clusters:
        days = sorted({row.date.date() for row in cluster["rows"]})
        if len(days) < MIN_VISITS:
            continue
        visits = [
            {"date": day.isoformat(), "kwh": round(float(daily_energy[day]), 1)}
            for day in days
            if day in daily_energy.index and daily_energy[day] > 0
        ]
        if not visits:
            continue
        costs = [v["kwh"] for v in visits]

        # A day's energy is only a fair proxy for the journey when that day held
        # nothing else. It cannot be attributed automatically: the largest Duluth
        # day reads 84.5 kWh, more than the pack holds, because it also contained
        # other driving and a fast charge. So every visit is offered with its own
        # figure and the caller picks which one to plan against, rather than the
        # planner inventing a single number it cannot justify.
        plausible = [c for c in costs if c <= pack_kwh]
        # For a long journey the trip dominates its day, so the largest plausible
        # day is a fair proxy. For a short one the rest of the day dominates
        # instead -- the biggest Anoka day is 44.2 kWh against a run that costs
        # about 15 -- so the median is the better default there.
        if not plausible:
            headline = min(costs)
        elif haversine_km(cluster["lat"], cluster["lon"], home[0], home[1]) >= LONG_TRIP_KM:
            headline = max(plausible)
        else:
            headline = float(np.median(plausible))
        destinations.append(
            {
                "lat": round(float(cluster["lat"]), 4),
                "lon": round(float(cluster["lon"]), 4),
                "distance_km": round(
                    float(haversine_km(cluster["lat"], cluster["lon"], home[0], home[1])),
                    1,
                ),
                "visits": len(days),
                # The planning figure is the largest day observed, not the
                # median. Underestimating a trip strands you; overestimating
                # only means charging longer than strictly necessary.
                "energy_kwh": round(float(headline), 1),
                "energy_median_kwh": round(float(np.median(costs)), 1),
                "energy_range_kwh": [round(float(min(costs)), 1), round(float(max(costs)), 1)],
                "visit_days": visits,
                # Days above pack capacity held more than this one journey.
                "days_over_capacity": int(sum(1 for c in costs if c > pack_kwh)),
                "last_visit": days[-1].isoformat(),
            }
        )

    return sorted(destinations, key=lambda d: d["energy_kwh"], reverse=True)


def seasonal_daily_energy(trips_df, battery_df, pack_kwh, when, road_trip_kwh=25.0):
    """Typical energy for a weekday in the same season as ``when``.

    Road-trip days are excluded so they do not inflate what a normal day costs,
    and the season is matched because the annual median describes neither summer
    nor winter.
    """
    # Combined source, so a day whose energy the trip log captured but the
    # poller missed still counts at its true cost.
    daily = day_energy_combined(trips_df, battery_df, pack_kwh)
    if daily.empty:
        return None

    frame = pd.DataFrame({"day": daily.index, "kwh": daily.to_numpy()})
    frame["month"] = pd.to_datetime(frame["day"]).dt.month
    frame["weekday"] = pd.to_datetime(frame["day"]).dt.dayofweek
    frame = frame[(frame["kwh"] > 0) & (frame["kwh"] <= road_trip_kwh)]
    if frame.empty:
        return None

    months = WARM_MONTHS if when.month in WARM_MONTHS else COLD_MONTHS
    seasonal = frame[frame["month"].isin(months)]
    same_weekday = seasonal[seasonal["weekday"] == when.weekday()]

    # Prefer the same weekday in the same season, then the season, then anything.
    for candidate, basis in (
        (same_weekday, "same weekday, same season"),
        (seasonal, "same season"),
        (frame, "all days"),
    ):
        if len(candidate) >= 4:
            return {
                "kwh": round(float(candidate["kwh"].median()), 1),
                "basis": basis,
                "sample": int(len(candidate)),
            }
    return {
        "kwh": round(float(frame["kwh"].median()), 1),
        "basis": "all days",
        "sample": len(frame),
    }


def energy_consumed_on(battery_df, day, pack_kwh):
    """Energy drawn from the pack on one calendar day, from SOC drops.

    Derived from the battery readings themselves rather than from trip logs or
    the odometer. Both of those lag: after one round trip the SOC had already
    fallen 52 points while trips.csv still showed only the outbound leg and the
    odometer had not moved for two polls. Prorating planned energy against
    mileage therefore counted that trip as half-pending on top of a battery
    that had already paid for all of it.

    The SOC reading cannot lag itself, so this is accurate the moment the poll
    lands, including partway through a trip.
    """
    frame = battery_df.dropna(subset=["timestamp", "battery_level"])
    rows = frame[frame["timestamp"].dt.date == day].sort_values("timestamp")
    if len(rows) < 2:
        return 0.0

    # Sum the drops only. Rises are charging, and netting them off would let an
    # afternoon on the charger mask the morning's driving.
    deltas = rows["battery_level"].astype(float).diff()
    return float(-deltas[deltas < 0].sum()) / 100.0 * pack_kwh


def plug_charging_state(plug_df, now):
    """Charging state from the smart plug, or None when it cannot be trusted.

    Returns None when the webhook feed is empty or older than PLUG_FRESH, so
    the caller falls back to the vehicle's own flag rather than acting on a
    stale plug reading.
    """
    if plug_df is None or plug_df.empty:
        return None

    frame = plug_df.dropna(subset=["timestamp", "watts"]).sort_values("timestamp")
    if frame.empty:
        return None

    row = frame.iloc[-1]
    age = now - row["timestamp"].to_pydatetime()
    if age > PLUG_FRESH or age < pd.Timedelta(minutes=-5):
        return None

    return {
        "is_charging": bool(row["watts"] >= PLUG_CHARGING_WATTS),
        "watts": float(row["watts"]),
        "age_minutes": round(age.total_seconds() / 60, 1),
    }


def remaining_load(planned_driving, consumed_today, today):
    """Sum planned energy still ahead, discounting what today already spent.

    Energy already consumed today is charged against today's planned events in
    order, so a trip that has happened stops counting even when the trip log
    has not caught up yet. Past days are dropped entirely.
    """
    total = 0.0
    detail = []
    budget = consumed_today

    for event in planned_driving:
        day = event["date"]
        if isinstance(day, str):
            day = datetime.strptime(day, "%Y-%m-%d").date()
        if day < today:
            continue

        kwh = float(event["kwh"])
        label = event.get("label", day.isoformat())

        if day == today and budget > 0:
            used = min(budget, kwh)
            budget -= used
            left = kwh - used
            # Within a rounding error of done: call it done rather than
            # leaving a fraction of a kWh pending forever.
            if left <= 0.05 * kwh:
                detail.append(f"{label}: done ({consumed_today:.1f} kWh used today)")
                continue
            total += left
            detail.append(f"{label}: {left:.1f} kWh left ({used:.1f} of {kwh:.1f} kWh used)")
            continue

        total += kwh
        detail.append(f"{label}: {kwh:.1f} kWh pending")

    return total, detail


def load_readiness_inputs(data_dir, now):
    """Read the newest battery reading and the plug state from a data directory.

    The one place that touches the filesystem, shared because the CLI, the
    collector and the web app all need exactly these two answers and must not
    disagree about staleness rules.
    """
    battery = pd.read_csv(os.path.join(data_dir, "battery_status.csv"))
    battery["timestamp"] = pd.to_datetime(battery["timestamp"], errors="coerce", format="mixed")
    battery = battery.dropna(subset=["timestamp", "battery_level"]).sort_values("timestamp")
    if battery.empty:
        raise ValueError("no usable rows in battery_status.csv")

    plug = None
    plug_path = os.path.join(data_dir, "plug_power.csv")
    if os.path.exists(plug_path):
        try:
            plug = pd.read_csv(plug_path)
            plug["timestamp"] = pd.to_datetime(plug["timestamp"], errors="coerce")
            plug["watts"] = pd.to_numeric(plug["watts"], errors="coerce")
        except (OSError, ValueError, KeyError):
            plug = None

    return battery, plug_charging_state(plug, now)


def assess_trip(
    *,
    now,
    departure,
    soc_pct,
    pack_kwh,
    charger_kw,
    trip_kwh,
    arrival_buffer_pct,
    daily_kwh=0.0,
    dcfc_kw=100.0,
    planned_driving=None,
    consumed_today=0.0,
    charge_limit_pct=100.0,
    is_charging=None,
    charging_source=None,
    plug_watts=None,
    plug_age_minutes=None,
    reading_at=None,
    max_reading_age_hours=DEFAULT_MAX_READING_AGE_HOURS,
):
    """Decide whether a trip is reachable, and what it would take if not.

    Driving between now and departure is costed one of two ways. Given a
    ``planned_driving`` itinerary -- dated events carrying their own energy --
    each day is charged individually and today's events are discounted by what
    the pack has already spent, so a trip that has happened stops counting even
    before the trip log catches up. Without one, ``daily_kwh`` is multiplied by
    the whole days remaining, which is all a caller can offer when it knows the
    departure but not the itinerary.

    ``charge_limit_pct`` is the ceiling the car will actually charge to. It
    defaults to a full pack, but the car normally sits at a lower limit and is
    raised to 100% for a trip; projecting to 100% regardless would promise
    charge the car was never going to accept.
    """
    hours_left = (departure - now).total_seconds() / 3600.0
    if hours_left <= 0:
        return {"status": "departed", "hours_to_departure": 0}

    if planned_driving:
        pending, detail = remaining_load(planned_driving, consumed_today, now.date())
        days_until = max(0, (departure.date() - now.date()).days)
    else:
        # Whole days between now and departure still cost their usual driving.
        days_until = max(0, (departure.date() - now.date()).days)
        pending = daily_kwh * days_until
        detail = []

    current_kwh = soc_pct / 100.0 * pack_kwh
    target_kwh = trip_kwh + arrival_buffer_pct / 100.0 * pack_kwh
    needed = target_kwh + pending - current_kwh
    plugged_hours = max(0.0, needed / charger_kw)
    duty = plugged_hours / hours_left

    if duty <= DUTY_ON_TRACK:
        status = "on_track"
    elif duty <= DUTY_TIGHT:
        status = "tight"
    else:
        status = "off_track"

    # How short the plan would be if charging continued at a realistic duty
    # rather than every parked hour.
    realistic = hours_left * DUTY_ON_TRACK * charger_kw
    shortfall = max(0.0, needed - realistic)

    ceiling_kwh = charge_limit_pct / 100.0 * pack_kwh
    projected = min(ceiling_kwh, current_kwh + realistic - pending)

    result = {
        "status": status,
        "checked_at": now.isoformat(timespec="seconds"),
        "soc_pct": round(float(soc_pct), 1),
        "hours_to_departure": round(hours_left, 1),
        "days_until": days_until,
        "pending_drive_kwh": round(pending, 1),
        "trip_kwh": round(trip_kwh, 1),
        "charge_needed_kwh": round(needed, 1),
        "plugged_hours_needed": round(plugged_hours, 1),
        "duty_cycle_pct": round(duty * 100, 0),
        "shortfall_kwh": round(shortfall, 1),
        "dcfc_minutes": round(shortfall / dcfc_kw * 60, 0) if shortfall > 0 else 0,
        "charge_limit_pct": charge_limit_pct,
        "projected_departure_pct": round(projected / pack_kwh * 100, 0),
        "projected_arrival_pct": round((projected - trip_kwh) / pack_kwh * 100, 0),
        "detail": detail,
    }

    if is_charging is not None:
        result["is_charging"] = bool(is_charging)
        result["charging_source"] = charging_source
        result["plug_watts"] = plug_watts
        result["plug_age_minutes"] = plug_age_minutes

    if reading_at is not None:
        age_hours = (now - reading_at).total_seconds() / 3600.0
        result["reading_at"] = reading_at.isoformat(timespec="seconds")
        result["reading_age_hours"] = round(age_hours, 1)
        result["stale_reading"] = age_hours > max_reading_age_hours

    return result


def assess_plan(plan, data_dir, now=None):
    """Assess a stored plan against the live data directory.

    The plan carries the trip as a share of the pack (``trip_soc_pct``), which
    is how it is measured in practice -- departure SOC minus arrival SOC on a
    past run of the same route.
    """
    now = now or datetime.now()
    pack_kwh = plan["pack_kwh"]

    battery, plug = load_readiness_inputs(data_dir, now)
    latest = battery.iloc[-1]

    # Prefer the smart plug for plugged-in state: it is minutes old rather than
    # up to two and a half hours. SOC still comes from the vehicle, which is
    # the only thing that knows it.
    is_charging = bool(latest["is_charging"])
    charging_source = "vehicle"
    if plug is not None:
        is_charging = plug["is_charging"]
        charging_source = "smart plug"

    return assess_trip(
        now=now,
        departure=datetime.strptime(plan["departure"], "%Y-%m-%d %H:%M"),
        soc_pct=float(latest["battery_level"]),
        pack_kwh=pack_kwh,
        charger_kw=plan["charger_kw"],
        trip_kwh=plan["trip_soc_pct"] / 100.0 * pack_kwh,
        arrival_buffer_pct=plan["arrival_buffer_pct"],
        dcfc_kw=plan.get("dcfc_kw", 100.0),
        planned_driving=plan.get("planned_driving"),
        consumed_today=energy_consumed_on(battery, now.date(), pack_kwh),
        charge_limit_pct=plan.get("charge_limit_pct", 100.0),
        is_charging=is_charging,
        charging_source=charging_source,
        plug_watts=plug["watts"] if plug else None,
        plug_age_minutes=plug["age_minutes"] if plug else None,
        reading_at=latest["timestamp"].to_pydatetime(),
        max_reading_age_hours=plan.get("max_reading_age_hours", DEFAULT_MAX_READING_AGE_HOURS),
    )
