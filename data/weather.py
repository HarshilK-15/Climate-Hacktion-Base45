"""Hourly sunshine (global horizontal irradiance, W/m2) for a site.

Owner: Data Sci.

Real data comes from NASA POWER (free, no account needed). Download it once with:
    python -m data.weather fetch            # all sites in data/sites.json
    python -m data.weather fetch kadavu     # one site
Files are cached in data/cache/ so the demo works offline afterwards.

If no cached file exists, a SYNTHETIC year is generated so the app still runs.
The app always shows which one it used. Never present synthetic weather as real.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)
HOURS = 8760
NASA_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
DEFAULT_YEARS = list(range(2005, 2025))   # 20 full years


def load_sites():
    return json.loads((HERE / "sites.json").read_text())["sites"]


def get_site(site_id):
    for s in load_sites():
        if s["id"] == site_id:
            return s
    return None


def _cache_file(lat, lon):
    return CACHE / f"ghi_{lat:.2f}_{lon:.2f}.npz"


def get_ghi(lat, lon):
    """Returns (ghi, label). ghi has shape (years, 8760) in W/m2, local solar time."""
    f = _cache_file(lat, lon)
    if f.exists():
        d = np.load(f)
        years = d["years"].tolist()
        return d["ghi"], f"NASA POWER hourly, {years[0]}-{years[-1]} ({len(years)} years)"
    return synthetic_ghi(lat, lon), "SYNTHETIC weather (run: python -m data.weather fetch)"


def synthetic_ghi(lat, lon, n_years=20):
    """Plausible but made-up sunshine: clear-sky curve x random daily cloudiness with cloudy spells."""
    seed = int(abs(lat * 1000) + abs(lon * 1000))
    rng = np.random.default_rng(seed)
    phi = math.radians(lat)
    out = np.zeros((n_years, HOURS), dtype=np.float32)
    hours = np.arange(24) + 0.5
    for y in range(n_years):
        k = 0.7
        for d in range(365):
            # cloudiness with memory, plus rare multi-day storms
            k = 0.6 * k + 0.4 * rng.uniform(0.35, 0.95)
            if rng.random() < 0.015:
                k = rng.uniform(0.1, 0.3)
            decl = math.radians(23.45) * math.sin(2 * math.pi * (284 + d + 1) / 365)
            ha = np.radians(15 * (hours - 12))
            sin_el = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * np.cos(ha)
            clear = 1000 * np.clip(sin_el, 0, None) ** 1.15
            out[y, d * 24:(d + 1) * 24] = clear * k
    return out


def fetch_nasa(lat, lon, years=DEFAULT_YEARS):
    import requests
    rows, got = [], []
    for y in years:
        params = {
            "parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
            "latitude": lat, "longitude": lon,
            "start": f"{y}0101", "end": f"{y}1231",
            "format": "JSON", "time-standard": "LST",
        }
        for attempt in range(3):
            try:
                r = requests.get(NASA_URL, params=params, timeout=120)
                r.raise_for_status()
                series = r.json()["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
                break
            except Exception as e:
                print(f"  {y}: attempt {attempt + 1} failed ({e})")
                time.sleep(5)
        else:
            print(f"  {y}: skipped")
            continue
        vals = []
        for key in sorted(series):
            if key[4:8] == "0229":       # drop 29 Feb so every year has 8760 hours
                continue
            v = series[key]
            vals.append(np.nan if v is None or v < 0 else v)   # -999 = missing
        arr = np.array(vals[:HOURS], dtype=np.float32)
        if len(arr) < HOURS:
            print(f"  {y}: only {len(arr)} hours, skipped")
            continue
        arr = _fill_gaps(arr)
        rows.append(arr)
        got.append(y)
        print(f"  {y}: ok, {arr.sum() / 1000 / 365:.2f} kWh/m2/day average")
        time.sleep(1)                       # be polite to NASA's servers
    if not rows:
        raise RuntimeError("No years downloaded - check your internet connection")
    np.savez_compressed(_cache_file(lat, lon), ghi=np.stack(rows), years=np.array(got))
    return len(got)


def _fill_gaps(arr):
    """Replace missing hours with the same hour from the previous day (or 0)."""
    for i in np.where(np.isnan(arr))[0]:
        arr[i] = arr[i - 24] if i >= 24 and not np.isnan(arr[i - 24]) else 0.0
    return arr


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "fetch":
        wanted = sys.argv[2] if len(sys.argv) > 2 else "all"
        for s in load_sites():
            if wanted in ("all", s["id"]):
                print(f"Downloading {s['name']}, {s['country']} ({s['lat']}, {s['lon']})")
                n = fetch_nasa(s["lat"], s["lon"])
                print(f"  saved {n} years")
    else:
        print(__doc__)
