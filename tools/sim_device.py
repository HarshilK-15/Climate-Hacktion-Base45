"""Simulated island generator sensor - the no-hardware version of the Pico W.

Owner: Elec A (when there is no hardware).

It sends readings in exactly the same format as the real Pico, so the rest of the app
cannot tell the difference - except that its device name starts with "sim-", and the
app then labels everything "Simulated sensor". Never present this as real measurement.

The demand it sends comes from the same village load model the engine uses
(data/loads.py), for a real site in data/sites.json, at the real local time of day,
plus realistic noise and fridge-compressor cycling.

Run (server must be running first):
    python tools/sim_device.py                       # Kadavu, live from now
    python tools/sim_device.py --site abaiang        # another island
    python tools/sim_device.py --backfill-days 7     # first create 7 days of history
                                                     # (fills the daily pattern instantly)
While it runs, type a command and press Enter to stage a demo moment:
    spike      demand suddenly triples for a few readings (a fault or big motor)
    offline    sensor goes silent for 3 minutes (Guard: "sensor has gone quiet")
    night      demand drops to night level for 6 minutes (Guard: "running almost empty")
    normal     back to normal
    help       show these commands
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loads import build_load        # noqa: E402
from data.weather import get_site        # noqa: E402

p = argparse.ArgumentParser(description="Simulated island generator sensor")
p.add_argument("url", nargs="?", default="http://localhost:8000/api/readings")
p.add_argument("--site", default="kadavu")
p.add_argument("--interval", type=float, default=2.0, help="seconds between readings")
p.add_argument("--batch", type=int, default=5, help="readings per upload")
p.add_argument("--backfill-days", type=float, default=0, help="days of history to create first (max 7)")
p.add_argument("--token", default="change-me")
args = p.parse_args()

site = get_site(args.site)
if not site:
    sys.exit(f"Unknown site '{args.site}'. Choose one of the ids in data/sites.json")
LOAD_KW, _, INFO = build_load(site)
DEVICE = f"sim-{site['id']}"
GEN_KW = float(site.get("generator_kw") or 0)
mode = {"name": "normal", "until": 0.0, "spikes": 0}


def demand_kw(t_local):
    """Village demand (kW) at a local time, smoothly between hourly model values."""
    hour_of_year = (t_local.timetuple().tm_yday - 1) % 365 * 24 + t_local.hour
    a = LOAD_KW[hour_of_year % 8760]
    b = LOAD_KW[(hour_of_year + 1) % 8760]
    frac = t_local.minute / 60 + t_local.second / 3600
    kw = a + (b - a) * frac
    fridge = 0.3 if (t_local.minute // 10) % 2 == 0 else 0.0   # freezer compressor cycling
    return max(0.0, kw * random.gauss(1.0, 0.03) + fridge)


def reading(t_utc, kw, seq):
    watts = kw * 1000
    amps = watts / (1.732 * 415 * 0.85)        # per phase, 415 V three-phase, power factor 0.85
    return {"ts": t_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "amps": round(amps, 2),
            "watts": round(watts, 1), "seq": seq}


def post(batch):
    body = json.dumps({"device_id": DEVICE, "site_id": site["id"], "readings": batch}).encode()
    req = urllib.request.Request(args.url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + args.token})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception as e:
        print("  upload failed:", e)
        return None


def backfill(days):
    days = min(days, 7)
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=days)
    batch, n, t = [], 0, start
    print(f"Creating {days:g} days of history at 1-minute resolution...")
    while t < now - dt.timedelta(seconds=30):
        n += 1
        batch.append(reading(t, demand_kw(t.astimezone()), n))
        if len(batch) == 500:
            post(batch)
            batch = []
        t += dt.timedelta(minutes=1)
    if batch:
        post(batch)
    print(f"  done: {n:,} readings")


def commands():
    for line in sys.stdin:
        cmd = line.strip().lower()
        now = time.time()
        if cmd == "spike":
            mode.update(spikes=3)
            print(">> spike: demand triples for 3 readings")
        elif cmd == "offline":
            mode.update(name="offline", until=now + 180)
            print(">> offline: sensor silent for 3 minutes")
        elif cmd == "night":
            mode.update(name="night", until=now + 360)
            print(">> night: night-time demand for 6 minutes")
        elif cmd == "normal":
            mode.update(name="normal", until=0, spikes=0)
            print(">> back to normal")
        elif cmd:
            print(__doc__.split("While it runs,")[1])


print(f"Simulated sensor '{DEVICE}' for {site['name']}, {site['country']}")
print(f"  model: {INFO['daily_kwh']} kWh/day, peak {INFO['peak_kw']} kW, generator {GEN_KW:g} kW")
print(f"  sending to {args.url}")
if args.backfill_days > 0:
    backfill(args.backfill_days)
print("Live now. Type spike / offline / night / normal and press Enter.\n")
threading.Thread(target=commands, daemon=True).start()

seq, batch = 10_000_000, []
while True:
    now_ts = time.time()
    if mode["name"] != "normal" and now_ts > mode["until"]:
        mode["name"] = "normal"
        print(">> back to normal")
    t = dt.datetime.now(dt.timezone.utc)
    if mode["name"] == "offline":
        batch = []
        time.sleep(args.interval)
        continue
    kw = demand_kw(t.astimezone())
    if mode["name"] == "night":
        kw = float(LOAD_KW.min()) * random.gauss(1.0, 0.03)
    if mode["spikes"] > 0:
        kw *= 3
        mode["spikes"] -= 1
    seq += 1
    batch.append(reading(t, kw, seq))
    if len(batch) >= args.batch:
        status = post(batch)
        print(f"  {t.astimezone().strftime('%H:%M:%S')}  {kw:6.1f} kW  [{mode['name']}]  sent {len(batch)} -> {status}")
        batch = []
    time.sleep(args.interval)
