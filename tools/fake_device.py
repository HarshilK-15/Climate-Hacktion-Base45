# fake_device.py - pretends to be the Pico so nobody waits for the hardware.
# Runs on a LAPTOP with normal Python 3.
#   Synthetic data:     python fake_device.py http://localhost:8000/api/readings
#   Replay real data:   python fake_device.py http://localhost:8000/api/readings --replay received.jsonl
import sys
import json
import math
import random
import time
import datetime
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/api/readings"
REPLAY = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--replay" else None
TOKEN, DEVICE, SITE, VOLTS = "change-me", "fake-01", "demo-home", 240.0
READ_EVERY_S, SEND_EVERY_N = 2, 5   # faster than the real Pico, for development


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def synthetic_watts():
    h = datetime.datetime.now().hour + datetime.datetime.now().minute / 60
    fridge = 120 if (time.time() // 600) % 2 == 0 else 5     # compressor cycles every 10 min
    daytime = 100 * math.sin(math.pi * (h - 6) / 12) ** 2 if 6 <= h <= 18 else 0
    evening = 800 if 17 <= h < 21 else 0                      # cooking and lights
    kettle = 2200 if random.random() < 0.03 else 0
    return max(0.0, fridge + daytime + evening + kettle + random.gauss(0, 15))


def post(batch):
    body = json.dumps({"device_id": DEVICE, "site_id": SITE, "readings": batch}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print("sent", len(batch), "->", r.status)
    except Exception as e:
        print("send failed:", e)


def readings():
    if REPLAY:
        with open(REPLAY) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        print("replaying", len(rows), "real readings with current timestamps")
        for r in rows:
            yield r["amps"], r["watts"]
    else:
        while True:
            w = synthetic_watts()
            yield w / VOLTS, w


seq, batch = 0, []
for amps, watts in readings():
    seq += 1
    batch.append({"ts": now_iso(), "amps": round(amps, 3), "watts": round(watts, 1), "seq": seq})
    if len(batch) >= SEND_EVERY_N:
        post(batch)
        batch = []
    time.sleep(READ_EVERY_S)
if batch:
    post(batch)
