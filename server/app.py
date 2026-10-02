"""Shipless server: one program that connects everything. Owner: Mech B.

Start it from the project folder:
    python -m server.app
Then open http://localhost:8000 in a browser.

  Pico W / fake device --POST /api/readings--> SQLite --> Live screen + Guard alerts
  Plan screen --POST /api/plan--> load builder + weather + engine + AI explainer
  Map screen  --GET /api/portfolio--> engine run for every island in data/sites.json
"""
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import agent
from data import loads, weather
from engine import sim
from guard import rules
from server import db

ROOT = Path(__file__).resolve().parent.parent
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "change-me")

app = FastAPI(title="Shipless")
db.init()


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- sensor data in
class Reading(BaseModel):
    ts: Optional[str] = None
    amps: float = 0.0
    watts: float
    seq: Optional[int] = None


class Batch(BaseModel):
    device_id: str
    site_id: Optional[str] = "demo-home"
    readings: List[Reading]


@app.post("/api/readings")
def post_readings(batch: Batch, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {DEVICE_TOKEN}":
        raise HTTPException(401, "Wrong or missing device token (check DEVICE_TOKEN on both sides)")
    now = utcnow()
    rows = []
    for r in batch.readings:
        ts = now
        if r.ts:
            try:
                t = datetime.fromisoformat(r.ts.replace("Z", "+00:00"))
                age = (now - t).total_seconds()
                if -300 < age < 8 * 86400:      # accept history up to 8 days old; ignore a badly set clock
                    ts = t
            except ValueError:
                pass
        rows.append({"device_id": batch.device_id, "site_id": batch.site_id, "ts": iso(ts),
                     "received_at": iso(now), "amps": r.amps, "watts": r.watts, "seq": r.seq})
    db.insert(rows)
    return {"ok": True, "stored": len(rows)}


# ---------------------------------------------------------------- live screen
@app.get("/api/readings")
def get_readings(minutes: int = 15):
    return db.since(iso(utcnow() - timedelta(minutes=minutes)))


@app.get("/api/status")
def status():
    last = db.latest()
    if not last:
        return {"online": False, "last": None, "age_s": None}
    age = (utcnow() - datetime.fromisoformat(last["ts"].replace("Z", "+00:00"))).total_seconds()
    return {"online": age < rules.OFFLINE_AFTER_S, "last": last, "age_s": round(age)}


@app.post("/api/readings/clear")
def clear_readings():
    db.clear()
    return {"ok": True}


# ---------------------------------------------------------------- guard alerts
@app.get("/api/alerts")
def alerts():
    recent = db.since(iso(utcnow() - timedelta(minutes=30)))
    gen_w = rules.DEMO_GENERATOR_W
    last = db.latest()
    site = weather.get_site(last["site_id"]) if last and last.get("site_id") else None
    if site and site.get("generator_kw"):
        gen_w = float(site["generator_kw"]) * 1000      # simulated island: use its generator size
    out = rules.evaluate(recent, generator_w=gen_w)
    for a in out:
        a["sms"] = agent.alert_sms(a)
    return out


# ---------------------------------------------------------------- measured daily pattern
@app.get("/api/live-shape")
def live_shape():
    rows = db.since(iso(utcnow() - timedelta(days=7)))
    sums, counts = [0.0] * 24, [0] * 24
    for r in rows:
        h = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone().hour
        sums[h] += r["watts"]
        counts[h] += 1
    means = [sums[h] / counts[h] if counts[h] else None for h in range(24)]
    simulated = bool(rows) and all(r["device_id"].startswith(("sim-", "fake-")) for r in rows)
    return {"hourly_watts": [round(m, 1) if m is not None else None for m in means],
            "hours_measured": sum(1 for c in counts if c), "readings": len(rows), "simulated": simulated}


# ---------------------------------------------------------------- planning
@app.get("/api/sites")
def sites():
    return weather.load_sites()


class DescribeRequest(BaseModel):
    text: str


@app.post("/api/describe")
def describe(req: DescribeRequest):
    return agent.parse_description(req.text)


class PlanRequest(BaseModel):
    site_id: str
    name: Optional[str] = None
    households: Optional[int] = None
    has_clinic: Optional[bool] = None
    has_school: Optional[bool] = None
    other_kw: Optional[float] = None
    generator_kw: Optional[float] = None
    diesel_litres_per_month: Optional[float] = None
    diesel_price_per_litre: Optional[float] = None
    use_live_shape: bool = False


def run_plan(site, use_live_shape=False):
    shape, measured, simulated = None, [], False
    if use_live_shape:
        ls = live_shape()
        shape, measured = loads.blend_live_shape(ls["hourly_watts"])
        simulated = ls["simulated"]
    load, critical, info = loads.build_load(site, shape)
    ghi, label = weather.get_ghi(site["lat"], site["lon"])
    result = sim.simulate(site, load, critical, ghi, label)
    source = "SIMULATED sensor" if simulated else "live sensor"
    info["pattern"] = (f"from the {source} ({len(measured)} of 24 hours covered, "
                       f"rest from the typical pattern)" if use_live_shape and measured
                       else "typical village pattern (assumption)")
    result["load"].update(info)
    return result


@app.post("/api/plan")
def plan(req: PlanRequest):
    base = weather.get_site(req.site_id)
    if not base:
        raise HTTPException(404, f"Unknown site '{req.site_id}'")
    site = dict(base)
    for k, v in req.model_dump(exclude={"site_id", "use_live_shape"}).items():
        if v is not None:
            site[k] = v
    result = run_plan(site, req.use_live_shape)
    result["explanation"] = agent.explain(result)
    return result


# ---------------------------------------------------------------- portfolio map
_portfolio = {"ready": False, "islands": [], "error": None}
_portfolio_lock = threading.Lock()


def build_portfolio():
    with _portfolio_lock:
        if _portfolio["ready"]:
            return
        islands = []
        for s in weather.load_sites():
            r = run_plan(s)
            islands.append({"site": r["site"], "weather": r["weather"], "today": r["today"],
                            "design": r["design"], "no_fuel_ship": r["no_fuel_ship"]})
        _portfolio.update(ready=True, islands=islands)


@app.get("/api/portfolio")
def portfolio():
    if not _portfolio["ready"]:
        build_portfolio()
    isl = _portfolio["islands"]
    tot = lambda f: sum(f(i) for i in isl)
    return {
        "islands": isl,
        "totals": {
            "islands": len(isl),
            "purchase_cost": tot(lambda i: i["design"]["purchase_cost"]),
            "litres_saved_p50": tot(lambda i: i["design"]["litres_saved_p50"]),
            "litres_saved_p90": tot(lambda i: i["design"]["litres_saved_p90"]),
            "money_saved_per_year_p50": tot(lambda i: i["design"]["money_saved_per_year_p50"]),
            # ~2.68 kg CO2 per litre of diesel burned (ASSUMPTION - Data Sci: cite a source)
            "tonnes_co2_avoided_p50": round(tot(lambda i: i["design"]["litres_saved_p50"]) * 2.68 / 1000),
        },
    }


# Pre-compute the map in the background when the server starts
threading.Thread(target=build_portfolio, daemon=True).start()


# ---------------------------------------------------------------- web pages (keep last)
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    import uvicorn
    ip = lan_ip()
    print("\n  Shipless is starting.")
    print("  Open the app:         http://localhost:8000")
    print(f"  Pico W API_URL:       http://{ip}:8000/api/readings")
    print(f"  Device token:         {DEVICE_TOKEN}")
    print(f"  AI explanations:      {'ON (' + agent.MODEL + ')' if agent.has_key() else 'OFF - using templates (set ANTHROPIC_API_KEY to turn on)'}\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
