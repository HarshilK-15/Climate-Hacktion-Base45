"""Checks the Pico W's HTTP endpoint from a laptop on the same Wi-Fi. Standard library only.

Usage:  python tools/test_pico_api.py <pico-ip>[:port]
        python tools/test_pico_api.py 192.168.1.50
        python tools/test_pico_api.py localhost:8080     (desktop test: python firmware/api.py)
Exit code 0 = all passed.
"""
import json
import sys
import time
import urllib.error
import urllib.request

FIELDS = {"timestamp": str, "device_id": str, "voltage_v": (int, float),
          "current_a": (int, float), "power_kw": (int, float)}
# Assumed sanity ranges (not from a spec): mains voltage 100-260 V, current 0-100 A.
V_RANGE, A_RANGE = (100, 260), (0, 100)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  - {detail}" if detail else ""))


def get(url, method="GET", timeout=5):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    base = "http://" + sys.argv[1].removeprefix("http://").rstrip("/")

    try:
        status, _, body = get(base + "/api/health")
    except Exception as e:
        print(f"FAIL  cannot reach {base}: {e}\n"
              "      Check the IP (printed by the Pico on boot), same Wi-Fi, 2.4 GHz network.")
        sys.exit(1)
    check("GET /api/health returns 200 {ok:true}", status == 200 and json.loads(body).get("ok") is True)

    # The first reading can take up to READ_EVERY_S (10 s) after boot, so retry on 503.
    for _ in range(15):
        status, headers, body = get(base + "/api/reading")
        if status != 503:
            break
        time.sleep(1)
    check("GET /api/reading returns 200", status == 200, f"got {status}")
    h = {k.lower(): v for k, v in headers.items()}
    check("Content-Type is application/json", "application/json" in h.get("content-type", ""))
    check("CORS header present (website can fetch it)", "access-control-allow-origin" in h)

    try:
        data = json.loads(body)
    except ValueError:
        check("body is valid JSON", False, body[:80])
        sys.exit(1)
    check("body is valid JSON", True)
    for key, typ in FIELDS.items():
        check(f"field '{key}' present with right type", isinstance(data.get(key), typ), repr(data.get(key)))
    v, a = data.get("voltage_v"), data.get("current_a")
    if isinstance(v, (int, float)):
        check(f"voltage in {V_RANGE} V", V_RANGE[0] <= v <= V_RANGE[1], f"{v} V")
    if isinstance(a, (int, float)):
        check(f"current in {A_RANGE} A", A_RANGE[0] <= a <= A_RANGE[1], f"{a} A")
    if isinstance(v, (int, float)) and isinstance(a, (int, float)) and isinstance(data.get("power_kw"), (int, float)):
        check("power_kw ~= V*A/1000", abs(v * a / 1000 - data["power_kw"]) < 0.01 + 0.02 * data["power_kw"],
              f"{v}*{a}/1000={v * a / 1000:.3f} vs {data['power_kw']}")

    status, _, _ = get(base + "/api/reading", method="OPTIONS")
    check("OPTIONS preflight returns 204", status == 204, f"got {status}")
    status, _, _ = get(base + "/nope")
    check("unknown path returns 404", status == 404, f"got {status}")

    # Readings should refresh: poll for up to 30 s and expect the timestamp or current to change.
    print("...waiting up to 30 s to see a fresh reading (skip with Ctrl+C)")
    try:
        first = json.loads(get(base + "/api/reading")[2])
        changed = False
        for _ in range(30):
            time.sleep(1)
            now = json.loads(get(base + "/api/reading")[2])
            if now != first:
                changed = True
                break
        check("reading updates over time", changed, "unchanged for 30 s (expected against the desktop "
              "stub, which serves fixed data; on a real Pico it means the main loop is stuck)")
    except KeyboardInterrupt:
        print("skipped")

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
