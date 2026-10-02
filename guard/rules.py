"""Guard: watches the live sensor and raises alerts.

Owner: Data Sci.

On an island, the sensor stays on the generator after solar is installed. These rules
spot problems early so a system doesn't quietly fail and slide back to diesel.
For the home demo, the measured appliance circuit plays the part of "the generator".

Each alert is plain data (facts only). Mech A's agent turns facts into a text message.
"""
import statistics
from datetime import datetime, timezone

# ---- settings (tune for the demo) ----
DEMO_GENERATOR_W = 3000      # pretend size of "the generator" for the home demo circuit
OFFLINE_AFTER_S = 90         # no reading for this long -> sensor offline
LOW_LOAD_FRACTION = 0.30     # generator below 30% of its size = wasting fuel
LOW_LOAD_MINUTES = 5
SPIKE_FACTOR = 3.0           # reading 3x above the recent typical value
SPIKE_MIN_W = 1000
SUN_HOURS = (10, 15)         # local hours when solar should cover the load
RUNNING_W = 20               # above this, we treat the circuit as "running"


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def evaluate(readings, now=None, local_hour=None, generator_w=None):
    """readings: list of dicts with ts (ISO, UTC) and watts, oldest first.
    Returns a list of alert dicts."""
    now = now or datetime.now(timezone.utc)
    gen_w = generator_w or DEMO_GENERATOR_W
    local_hour = datetime.now().hour if local_hour is None else local_hour
    alerts = []
    if not readings:
        return [_alert("no_data", "info", "Waiting for the first reading",
                       {"hint": "Start the Pico or tools/fake_device.py"}, now)]

    last = readings[-1]
    age = (now - _parse(last["ts"])).total_seconds()
    if age > OFFLINE_AFTER_S:
        alerts.append(_alert("sensor_offline", "high", "Sensor has gone quiet",
                             {"seconds_since_last_reading": int(age), "last_watts": last["watts"]}, now))
        return alerts

    recent = [r for r in readings if (now - _parse(r["ts"])).total_seconds() <= LOW_LOAD_MINUTES * 60]
    watts = [r["watts"] for r in recent]

    # Spike: any reading in the last minute far above what was typical before it
    fresh = [r for r in readings if (now - _parse(r["ts"])).total_seconds() <= 60]
    before = [r["watts"] for r in readings if (now - _parse(r["ts"])).total_seconds() > 60][-30:]
    if fresh and len(before) >= 5:
        typical = statistics.median(before)
        peak = max(fresh, key=lambda r: r["watts"])
        if peak["watts"] > max(SPIKE_MIN_W, SPIKE_FACTOR * max(typical, 1)):
            alerts.append(_alert("spike", "medium", "Sudden jump in demand",
                                 {"watts_now": round(peak["watts"]), "typical_watts": round(typical)}, now))

    covered = (now - _parse(readings[0]["ts"])).total_seconds() >= LOW_LOAD_MINUTES * 60 * 0.9
    if watts and covered and all(RUNNING_W < w < LOW_LOAD_FRACTION * gen_w for w in watts):
        avg = sum(watts) / len(watts)
        alerts.append(_alert("low_load", "medium", "Generator running almost empty",
                             {"average_watts": round(avg), "generator_watts": round(gen_w),
                              "percent_of_size": round(100 * avg / gen_w),
                              "minutes": LOW_LOAD_MINUTES}, now))

    if watts and SUN_HOURS[0] <= local_hour < SUN_HOURS[1] and sum(watts) / len(watts) > RUNNING_W:
        alerts.append(_alert("diesel_in_sunshine", "low", "Running on diesel in full sun",
                             {"average_watts": round(sum(watts) / len(watts)), "local_hour": local_hour}, now))
    return alerts


def _alert(kind, severity, title, facts, now):
    return {"type": kind, "severity": severity, "title": title, "facts": facts,
            "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
