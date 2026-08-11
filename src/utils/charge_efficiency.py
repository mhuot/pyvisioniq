"""Measure how much wall energy actually reaches the traction battery.

The smart plug meters AC energy drawn at the wall; the vehicle reports state of
charge. The ratio between them is the onboard charger's real-world efficiency,
which on 120V L1 runs far below the ~100% people assume and materially changes
how long a target charge takes.

Two measurement hazards are handled explicitly:

* **SOC quantisation.** State of charge is reported as whole percent, so a
  session that gains only a few points carries enormous relative error -- at a
  74 kWh pack, one point is 0.74 kWh, so a 3-point session is +/-33%. Sessions
  below ``MIN_SOC_POINTS`` are dropped rather than shown as noise.
* **The charge-limit tail.** When the car reaches its configured limit it stops
  accepting energy while the plug keeps drawing a trickle. Counting that tail
  would understate efficiency, so each session is trimmed to the window over
  which SOC was actually rising.
"""

import numpy as np
import pandas as pd

from src.utils.plug_sessions import detect_charging_spans

# One SOC point is ~0.74 kWh on a 74 kWh pack. Requiring 8 points holds
# quantisation error to about +/-12.5%; below that the figure is mostly noise.
MIN_SOC_POINTS = 8

# Physically possible band. Values outside it indicate a measurement problem
# (a missed plug sample, or SOC moving for a reason other than this charge)
# rather than a genuinely strange session.
MIN_PLAUSIBLE_PCT = 40.0
MAX_PLAUSIBLE_PCT = 100.0

# How far from a span boundary a battery reading may sit and still describe it.
LOOKUP_TOLERANCE = pd.Timedelta(minutes=45)


def _integrate_watts(samples, start, end):
    """Trapezoidal kWh drawn at the wall between two timestamps."""
    window = samples[(samples["timestamp"] >= start) & (samples["timestamp"] <= end)]
    if len(window) < 2:
        return 0.0
    hours = window["timestamp"].astype("int64").to_numpy() / 1e9 / 3600.0
    return float(np.trapz(window["watts"].to_numpy(), hours)) / 1000.0


def _rising_window(battery_df, start, end):
    """Trim a span to the period over which SOC was actually increasing.

    Returns (t_start, t_end, soc_gain) or None. This removes the flat tail after
    the car hits its charge limit, which would otherwise look like lost energy.
    """
    rows = battery_df[
        (battery_df["timestamp"] >= start - LOOKUP_TOLERANCE)
        & (battery_df["timestamp"] <= end + LOOKUP_TOLERANCE)
    ].sort_values("timestamp")
    if len(rows) < 2:
        return None

    levels = rows["battery_level"].to_numpy()
    times = rows["timestamp"].to_numpy()

    # argmin/argmax both return the FIRST occurrence, which is what we want at
    # each end: start where SOC was still at its lowest, and stop the moment it
    # first reached its peak. Taking the last occurrence of the peak instead
    # would keep the flat tail after the car hit its charge limit -- the exact
    # window this function exists to discard.
    first_idx = int(np.argmin(levels))
    last_idx = int(np.argmax(levels))
    if last_idx <= first_idx:
        return None

    gain = float(levels[last_idx] - levels[first_idx])
    if gain <= 0:
        return None
    return pd.Timestamp(times[first_idx]), pd.Timestamp(times[last_idx]), gain


def _mean_temperature(battery_df, start, end):
    """Average ambient temperature across a window, preferring the weather feed."""
    rows = battery_df[(battery_df["timestamp"] >= start) & (battery_df["timestamp"] <= end)]
    for column in ("meteo_temp", "temperature", "vehicle_temp"):
        if column in rows.columns:
            values = pd.to_numeric(rows[column], errors="coerce").dropna()
            if not values.empty:
                return round(float(values.mean()), 1)
    return None


def compute_efficiency_points(battery_df, ac_samples, pack_kwh, min_soc_points=MIN_SOC_POINTS):
    """Return one AC-to-pack efficiency measurement per qualifying charge session.

    Each point carries the wall energy, the pack energy, the resulting
    efficiency percentage, and the ambient temperature, so the caller can plot
    efficiency over time or against temperature.
    """
    if battery_df.empty or ac_samples.empty:
        return []

    battery_df = battery_df.dropna(subset=["timestamp", "battery_level"]).sort_values("timestamp")
    points = []

    for span in detect_charging_spans(ac_samples):
        if span.get("ongoing"):
            continue
        trimmed = _rising_window(battery_df, span["start"], span["end"])
        if trimmed is None:
            continue
        soc_start, soc_end, soc_gain = trimmed
        if soc_gain < min_soc_points:
            continue

        ac_kwh = _integrate_watts(ac_samples, soc_start, soc_end)
        if ac_kwh <= 0:
            continue
        pack_delivered = soc_gain / 100.0 * pack_kwh
        efficiency = pack_delivered / ac_kwh * 100.0
        if not MIN_PLAUSIBLE_PCT <= efficiency <= MAX_PLAUSIBLE_PCT:
            continue

        hours = (soc_end - soc_start).total_seconds() / 3600.0
        points.append(
            {
                "start_time": soc_start.isoformat(),
                "duration_hours": round(hours, 2),
                "soc_gain": round(soc_gain, 1),
                "ac_kwh": round(ac_kwh, 2),
                "pack_kwh": round(pack_delivered, 2),
                "lost_kwh": round(ac_kwh - pack_delivered, 2),
                "efficiency_pct": round(efficiency, 1),
                "avg_ac_kw": round(ac_kwh / hours, 2) if hours > 0 else None,
                "avg_pack_kw": round(pack_delivered / hours, 2) if hours > 0 else None,
                "temperature": _mean_temperature(battery_df, soc_start, soc_end),
                # Quantisation error on this specific measurement, so the UI can
                # show how much to trust an individual point.
                "uncertainty_pct": round(1.0 / soc_gain * 100.0, 1),
            }
        )

    return sorted(points, key=lambda point: point["start_time"])


def summarize(points):
    """Reduce efficiency points to the headline figures for a stat tile."""
    if not points:
        return {
            "count": 0,
            "efficiency_pct": None,
            "median_efficiency_pct": None,
            "total_ac_kwh": 0.0,
            "total_pack_kwh": 0.0,
            "total_lost_kwh": 0.0,
        }
    efficiencies = sorted(point["efficiency_pct"] for point in points)
    total_ac = sum(point["ac_kwh"] for point in points)
    total_pack = sum(point["pack_kwh"] for point in points)
    return {
        "count": len(points),
        # Energy-weighted, i.e. total delivered over total drawn. This is the
        # headline figure: it answers "of everything I paid for, how much
        # reached the battery", and it is dominated by the long sessions whose
        # quantisation error is smallest. A plain median over-weights short
        # sessions, which read high.
        "efficiency_pct": round(total_pack / total_ac * 100.0, 1) if total_ac else None,
        "median_efficiency_pct": round(float(np.median(efficiencies)), 1),
        "total_ac_kwh": round(total_ac, 1),
        "total_pack_kwh": round(total_pack, 1),
        "total_lost_kwh": round(total_ac - total_pack, 1),
    }
