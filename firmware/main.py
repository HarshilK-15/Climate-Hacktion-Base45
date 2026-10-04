# main.py - runs automatically when the Pico W powers on.
# Measures every READ_EVERY_S seconds, buffers readings, sends batches over Wi-Fi.
# Press Stop (Ctrl+C) in Thonny to interrupt it.
import time
import json
import gc
import network
import ntptime
from machine import Pin
import config
import sensor
import api

try:
    import requests
except ImportError:
    import urequests as requests

led = Pin("LED", Pin.OUT)
wlan = network.WLAN(network.STA_IF)


def blink(times, gap=0.15):
    for _ in range(times):
        led.on()
        time.sleep(gap)
        led.off()
        time.sleep(gap)


def connect_wifi():
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to Wi-Fi:", config.WIFI_SSID)
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(30):   # ~30 s: phone hotspots can be slow to accept the Pico
            if wlan.isconnected():
                break
            blink(1, 0.5)
    if wlan.isconnected():
        print("Wi-Fi OK, Pico IP:", wlan.ifconfig()[0])
        return True
    print("Wi-Fi failed - check name/password and that the hotspot is 2.4 GHz")
    return False


def sync_clock():
    try:
        ntptime.settime()
        print("Clock synced (UTC)")
        return True
    except Exception as e:
        print("Clock sync failed:", e)
        return False


def iso_now():
    t = time.gmtime()
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % t[:6]


def send(batch):
    body = json.dumps({
        "device_id": config.DEVICE_ID,
        "site_id": config.SITE_ID,
        "readings": batch,
    })
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + config.DEVICE_TOKEN,
    }
    try:
        r = requests.post(config.API_URL, data=body, headers=headers)
        ok = 200 <= r.status_code < 300
        print("Sent", len(batch), "readings -> HTTP", r.status_code)
        r.close()
        return ok
    except Exception as e:
        print("Send failed:", e)
        return False


def main():
    online = connect_wifi()
    clock_ok = sync_clock() if online else False
    if online:
        api.start()
    buffer = []
    seq = 0
    while True:
        amps, n = sensor.read_amps()
        seq += 1
        reading = {
            "ts": iso_now() if clock_ok else None,   # server uses receive time if None
            "amps": round(amps, 3),
            "watts": round(amps * config.VOLTS, 1),
            "seq": seq,
        }
        buffer.append(reading)
        if len(buffer) > config.MAX_BUFFER:
            buffer.pop(0)
        print(reading, "| samples:", n)
        led.toggle()

        if len(buffer) >= config.SEND_EVERY_N:
            if not wlan.isconnected():
                online = connect_wifi()
                if online and not clock_ok:
                    clock_ok = sync_clock()
            if wlan.isconnected() and send(buffer):
                buffer = []
        # Expose newest reading at GET /api/reading (shape = contracts/reading.json)
        api.latest.update({
            "timestamp": reading["ts"] or iso_now(),
            "device_id": config.DEVICE_ID,
            "voltage_v": config.VOLTS,   # assumption: fixed value, no voltage sensor fitted
            "current_a": reading["amps"],
            "power_kw": round(reading["watts"] / 1000, 3),
        })
        gc.collect()
        # wait READ_EVERY_S, but keep answering API requests meanwhile
        end = time.ticks_add(time.ticks_ms(), config.READ_EVERY_S * 1000)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            api.poll()
            time.sleep_ms(50)


main()
