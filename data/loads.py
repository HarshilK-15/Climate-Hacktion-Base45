"""Builds a full year (8760 hours) of island electricity demand in kW.

Owner: Data Sci.

Two ways to get the daily pattern ("shape"):
  1. A typical village pattern (default, illustrative assumption)
  2. The pattern MEASURED by the live sensor (measure, don't guess)
The total amount is set by the number of households and buildings, or - better -
by how much diesel the island actually buys each month.
"""
import numpy as np

HOURS = 8760

# ---- ASSUMPTIONS (illustrative - Data Sci: replace with sourced values) ----
KWH_PER_HOUSEHOLD_PER_DAY = 2.5
KWH_PER_LITRE_DIESEL = 3.0        # small diesel generators typically deliver ~2.5-3.5 kWh per litre
CLINIC_AVG_KW = 1.0               # lights, fans, vaccine fridge, equipment
CLINIC_CRITICAL_KW = 0.4          # what must NEVER go off: vaccine fridge + emergency light
SCHOOL_KW = 3.0                   # school hours on weekdays
# Typical household pattern by hour 0..23 (relative): low at night, evening peak
HOUSEHOLD_SHAPE = np.array([0.5, 0.5, 0.5, 0.5, 0.5, 0.6, 0.8, 1.0, 0.8, 0.7, 0.7, 0.7,
                            0.7, 0.7, 0.7, 0.7, 0.8, 1.1, 1.8, 2.0, 1.9, 1.5, 1.0, 0.7])


def blend_live_shape(hourly_means):
    """hourly_means: list of 24 values (watts or None if no data for that hour).
    Returns (shape of 24 numbers, list of measured hours). Unmeasured hours use the
    typical pattern, rescaled so the two parts join up sensibly."""
    measured = [h for h, v in enumerate(hourly_means) if v is not None and v > 0]
    if not measured:
        return HOUSEHOLD_SHAPE.copy(), []
    m = np.array([hourly_means[h] for h in measured], dtype=float)
    typ = HOUSEHOLD_SHAPE[measured]
    k = m.mean() / typ.mean()
    shape = HOUSEHOLD_SHAPE.copy() * k
    for h, v in zip(measured, m):
        shape[h] = v
    return shape / shape.mean() * HOUSEHOLD_SHAPE.mean(), measured


def build_load(site, household_shape=None):
    """Returns (load_kw array of 8760, info dict)."""
    shape = HOUSEHOLD_SHAPE if household_shape is None else np.asarray(household_shape, float)
    per_house_kw = shape / shape.sum() * KWH_PER_HOUSEHOLD_PER_DAY      # kW for each hour of a day
    houses = per_house_kw * float(site.get("households", 0))

    load = np.zeros(HOURS)
    critical = np.zeros(HOURS)
    for d in range(365):
        weekday = d % 7 < 5
        day = houses.copy()
        crit = np.zeros(24)
        if site.get("has_clinic"):
            day += CLINIC_AVG_KW
            crit += CLINIC_CRITICAL_KW
        if site.get("has_school") and weekday:
            day[8:15] += SCHOOL_KW
        other = float(site.get("other_kw", 0) or 0)       # e.g. ice plant / community freezer
        day[8:18] += other
        load[d * 24:(d + 1) * 24] = day
        critical[d * 24:(d + 1) * 24] = crit

    info = {"basis": "households and buildings"}
    litres = site.get("diesel_litres_per_month")
    if litres:
        target_daily = float(litres) * KWH_PER_LITRE_DIESEL / 30.4
        factor = target_daily / (load.sum() / 365)
        load *= factor
        info["basis"] = f"scaled to match {float(litres):,.0f} litres of diesel per month"
    info.update({
        "daily_kwh": round(float(load.sum()) / 365, 1),
        "peak_kw": round(float(load.max()), 1),
        "night_min_kw": round(float(load.min()), 1),
        "critical_kw": round(float(critical.max()), 2),
    })
    return load, critical, info
