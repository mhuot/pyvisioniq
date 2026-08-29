"""Group measured trip efficiency into temperature bands.

Cold weather is the largest single influence on how far the car goes per kWh,
so the dashboard plots efficiency against temperature and the planner costs a
future trip at the temperature it is forecast to be driven in. Both need the
same bands computed the same way, which is why this is not inlined in either.

Bands are aggregated by energy, never by averaging per-trip ratios: a two mile
hop and a seventy-six mile run are not equal evidence about a band, and
mean-of-ratios treats them as though they were.
"""

KM_PER_MILE = 1.60934
BIN_WIDTH_C = 5


def bin_label(celsius):
    """The label of the 5 degree band a temperature falls in."""
    start = int(celsius // BIN_WIDTH_C) * BIN_WIDTH_C
    return f"{start} to {start + BIN_WIDTH_C}°C"


def temperature_bins(points):
    """Aggregate efficiency points into temperature bands, energy-weighted.

    Each point needs a ``temperature`` (Celsius), an ``efficiency`` (distance
    per kWh) and a ``distance``. Returns bands sorted by temperature.
    """
    buckets = {}
    for point in points:
        label = bin_label(point["temperature"])
        bucket = buckets.setdefault(
            label,
            {"temperatures": [], "efficiencies": [], "count": 0, "distance": 0.0, "energy": 0.0},
        )
        bucket["temperatures"].append(point["temperature"])
        bucket["efficiencies"].append(point["efficiency"])
        bucket["count"] += 1
        bucket["distance"] += point["distance"]
        # The energy this trip implies, so the band can be totalled by energy.
        bucket["energy"] += point["distance"] / point["efficiency"]

    bands = []
    for label, bucket in buckets.items():
        if not bucket["efficiencies"] or bucket["energy"] <= 0 or bucket["distance"] <= 0:
            continue
        wh_per_mile = bucket["energy"] * 1000.0 / bucket["distance"]
        bands.append(
            {
                "temperature_range": label,
                "avg_temperature": round(
                    sum(bucket["temperatures"]) / len(bucket["temperatures"]), 1
                ),
                "avg_efficiency": round(bucket["distance"] / bucket["energy"], 2),
                "wh_per_mile": round(wh_per_mile, 1),
                "wh_per_km": round(wh_per_mile / KM_PER_MILE, 1),
                "total_energy_kwh": round(bucket["energy"], 1),
                "trip_count": bucket["count"],
                "total_distance": round(bucket["distance"], 1),
                "best_efficiency": round(max(bucket["efficiencies"]), 2),
                "worst_efficiency": round(min(bucket["efficiencies"]), 2),
            }
        )

    return sorted(bands, key=lambda band: band["avg_temperature"])


def wh_per_mile_at(bands, celsius, min_trips=3):
    """Consumption expected at a temperature, from the measured bands.

    Prefers the band the temperature falls in. When that band is empty or too
    thin to trust, falls back to the nearest band with enough trips behind it,
    because a single trip's band is noise rather than a measurement.

    Returns (wh_per_mile, basis) or (None, reason) when nothing qualifies.
    """
    usable = [band for band in bands if band["trip_count"] >= min_trips]
    if not usable:
        return None, "no temperature band has enough trips"

    label = bin_label(celsius)
    exact = next((band for band in usable if band["temperature_range"] == label), None)
    if exact:
        return exact["wh_per_mile"], f"measured at {label} ({exact['trip_count']} trips)"

    nearest = min(usable, key=lambda band: abs(band["avg_temperature"] - celsius))
    return (
        nearest["wh_per_mile"],
        f"nearest measured band {nearest['temperature_range']} "
        f"({nearest['trip_count']} trips); none at {label}",
    )


def seasonal_adjustment(bands, forecast_c, history_c, min_trips=3):
    """How much more energy a route needs at one temperature than another.

    A route's own history is the best predictor of what it costs, but it was
    driven at whatever temperature it happened to be. Scaling that figure by
    the ratio between the forecast band and the band it was measured in keeps
    the route's character -- sustained highway is not a band average -- while
    still moving it for the season.

    Returns (multiplier, basis). The multiplier is 1.0 when the data cannot
    support an adjustment, so a caller can always multiply by it safely.
    """
    forecast_wh, forecast_basis = wh_per_mile_at(bands, forecast_c, min_trips)
    history_wh, _ = wh_per_mile_at(bands, history_c, min_trips)

    if not forecast_wh or not history_wh:
        return 1.0, "no seasonal adjustment: not enough measured trips by temperature"

    ratio = forecast_wh / history_wh
    return round(ratio, 3), (
        f"scaled {ratio:.2f}x for {forecast_c:.0f}°C vs the "
        f"{history_c:.0f}°C this route was measured at ({forecast_basis})"
    )
