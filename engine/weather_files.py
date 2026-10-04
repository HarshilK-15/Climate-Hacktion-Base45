"""Load Data Sci's real weather files into the shape the engine wants. Owner: Elec B.

Data Sci writes data/out/<place>_ghi.csv (NASA POWER, ALLSKY_SFC_SW_DWN). The engine wants
ghi_years: shape (years, 8760), float64, W/m2, local time. This module bridges the two and
refuses files that would silently give wrong answers (wrong units, gaps, missing-value codes).

    ghi_years, label, info = nearest_weather(lat, lon)   # real file if one is close, else synthetic
"""
import math
from pathlib import Path

import numpy as np

WEATHER_DIR = Path(__file__).parent.parent / "data" / "out"
HOURS = 8760

# Copied from data/download_power.py ISLANDS (Data Sci): keep in sync if they add places.
PLACES = {
    "funafuti": (-8.52, 179.20),
    "suva": (-18.14, 178.44),
    "nukualofa": (-21.14, -175.20),
    "port_vila": (-17.73, 168.32),
    "tarawa": (1.45, 173.03),
    "apia": (-13.85, -171.75),
}

# Physics, not assumption: the sun delivers 1361 W/m2 above the atmosphere (solar constant),
# so ground-level GHI above ~1400 W/m2 means wrong units or corrupt data.
MAX_GHI_WM2 = 1400.0
# ASSUMPTION: a weather file within 500 km has the same sunshine statistics (yearly total,
# seasons, cloudy-spell length) even though individual cloudy hours differ. Fine for sizing
# panels; not for hour-by-hour forecasting. Tighten if Data Sci downloads per-site files.
MAX_DISTANCE_KM = 500.0
# Brief 3.4 asks for ~20 years; P90 (the 1-in-10 bad year) needs at least 10 to mean anything.
MIN_YEARS_FOR_P90 = 10


def load_ghi_csv(path, lon=None):
    """Read one weather file -> (ghi_years (Y, 8760) W/m2 float64, info dict).

    Accepts the brief's format (one column, kW/m2) and Data Sci's (timestamp, ghi_wm2).
    Units come from the header (ghi_kwm2 / ghi_wm2), or from magnitude if there is none.
    Leap days are dropped so every year is 8760 hours. Raises ValueError on bad data.
    """
    path = Path(path)
    with open(path) as f:
        first = f.readline().strip()
    has_header = any(c.isalpha() for c in first.replace("e-", "").replace("E-", ""))
    raw = np.loadtxt(path, delimiter=",", skiprows=int(has_header), dtype=str, ndmin=2)
    header = [h.strip().lower() for h in first.split(",")] if has_header else []
    stamps = raw[:, 0] if raw.shape[1] >= 2 else None
    values = raw[:, -1].astype(float)

    if np.isnan(values).any():
        raise ValueError(f"{path.name}: {int(np.isnan(values).sum())} empty/NaN hours - fill gaps first")
    if (values < 0).any():
        raise ValueError(f"{path.name}: negative sunshine (NASA's -999 missing code?) in "
                         f"{int((values < 0).sum())} hours")
    col = header[-1] if header else ""
    if "kwm2" in col or "kw_m2" in col or (not col and values.max() <= 2.0):
        values = values * 1000.0
    elif not ("wm2" in col or "w_m2" in col or not col):
        raise ValueError(f"{path.name}: can't tell the units of column '{col}' (want ghi_wm2 or ghi_kwm2)")
    if values.max() > MAX_GHI_WM2:
        raise ValueError(f"{path.name}: {values.max():.0f} W/m2 is above what reaches the ground - wrong units?")

    warnings = []
    years = None
    if stamps is not None:
        leap = np.char.find(stamps, "-02-29") >= 0
        values, stamps = values[~leap], stamps[~leap]
        years = sorted({s[:4] for s in stamps})
    if len(values) % HOURS:
        raise ValueError(f"{path.name}: {len(values)} hours is not a whole number of years")
    ghi_years = values.reshape(-1, HOURS)
    n = ghi_years.shape[0]

    by_hour = ghi_years.reshape(n, 365, 24).mean(axis=(0, 1))
    peak_hour = int(by_hour.argmax())
    if stamps is not None and stamps[0].endswith("Z") and lon is not None:
        utc_noon = round((12 - lon / 15) % 24)
        if min(abs(peak_hour - utc_noon), 24 - abs(peak_hour - utc_noon)) > 3 and 10 <= peak_hour <= 13:
            warnings.append("timestamps say UTC ('Z') but the sun peaks at local noon: "
                            "they are local solar time (fine for the engine; label is wrong)")
    if n < MIN_YEARS_FOR_P90:
        warnings.append(f"only {n} year(s) of weather: P90 needs >= {MIN_YEARS_FOR_P90} "
                        f"(brief wants ~20; run: python -m data.weather fetch)")

    info = {
        "file": path.name,
        "years": years or n,
        "n_years": n,
        "kwh_per_m2_per_year": [round(float(y.sum()) / 1000) for y in ghi_years],
        "peak_hour": peak_hour,
        "warnings": warnings,
    }
    return ghi_years, info


def _distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (haversine), handles the 180 degree date line."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_weather(lat, lon, weather_dir=WEATHER_DIR, max_km=MAX_DISTANCE_KM):
    """Real sunshine for a site -> (ghi_years, label, info).

    Uses the closest data/out/<place>_ghi.csv within max_km. If none exists (yet), falls back
    to Data Sci's synthetic pattern so nobody is ever blocked, and says so in the label.
    """
    found = []
    for place, (plat, plon) in PLACES.items():
        f = Path(weather_dir) / f"{place}_ghi.csv"
        if f.exists():
            found.append((_distance_km(lat, lon, plat, plon), place, f, plon))
    if found:
        km, place, f, plon = min(found)
        if km <= max_km:
            ghi_years, info = load_ghi_csv(f, lon=plon)
            info.update(place=place, distance_km=round(km))
            yrs = info["years"]
            span = f"{yrs[0]}-{yrs[-1]}" if isinstance(yrs, list) else f"{yrs} years"
            label = f"NASA POWER hourly, {place.replace('_', ' ').title()} {span}, {round(km)} km away"
            return ghi_years, label, info
    from data.weather import synthetic_ghi
    return (synthetic_ghi(lat, lon).astype(float), "SYNTHETIC weather (no real file within "
            f"{max_km:.0f} km)", {"place": None, "warnings": ["synthetic weather"]})


if __name__ == "__main__":
    # python -m engine.weather_files : check every real file the engine can see
    for place, (plat, plon) in PLACES.items():
        f = WEATHER_DIR / f"{place}_ghi.csv"
        if not f.exists():
            print(f"MISSING {f}")
            continue
        g, info = load_ghi_csv(f, lon=plon)
        print(f"{place:10s} {g.shape[0]} x {g.shape[1]}  kWh/m2/yr {info['kwh_per_m2_per_year']}  "
              f"peak {info['peak_hour']}:00")
        for w in info["warnings"]:
            print(f"           ! {w}")
