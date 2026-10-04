# api.py - tiny HTTP server on the Pico W so the website can GET the latest reading.
#   GET /api/reading  -> {"timestamp","device_id","voltage_v","current_a","power_kw",...}  (see contracts/reading.json)
#   GET /api/health   -> {"ok": true}
# Non-blocking: call poll() often from the main loop (main.py does this while it waits).
# Also runs on desktop Python for testing: python firmware/api.py
import json
import time

try:
    import socket
except ImportError:  # pragma: no cover
    import usocket as socket

PORT = 80          # use 8080 on desktop if 80 needs privileges
# Assumption: the website is served from a different origin, so the browser needs CORS.
# "*" is fine for read-only demo data; tighten to the site's origin for anything real.
ALLOW_ORIGIN = "*"

_srv = None
latest = {}        # main.py updates this with the newest reading dict


def start(port=PORT):
    global _srv
    _srv = socket.socket()
    try:
        _srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    _srv.bind(("0.0.0.0", port))
    _srv.listen(2)
    _srv.setblocking(False)
    print("API listening on port", port)


def _respond(conn, status, body, ctype="application/json"):
    data = body if isinstance(body, bytes) else body.encode()
    head = ("HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            "Access-Control-Allow-Origin: %s\r\nAccess-Control-Allow-Methods: GET, OPTIONS\r\n"
            "Access-Control-Allow-Headers: Content-Type\r\nCache-Control: no-store\r\n"
            "Connection: close\r\n\r\n") % (status, ctype, len(data), ALLOW_ORIGIN)
    conn.send(head.encode() + data)


def poll():
    """Serve at most one pending request. Returns immediately if none."""
    if _srv is None:
        return
    try:
        conn, _ = _srv.accept()
    except OSError:
        return  # nothing waiting
    try:
        conn.settimeout(2)
        req = conn.recv(1024).decode("utf-8", "ignore")
        line = req.split("\r\n", 1)[0].split(" ")
        method, path = (line + ["", ""])[:2]
        path = path.split("?")[0]
        if method == "OPTIONS":
            _respond(conn, "204 No Content", "")
        elif method != "GET":
            _respond(conn, "405 Method Not Allowed", '{"error":"GET only"}')
        elif path == "/api/reading":
            if latest:
                _respond(conn, "200 OK", json.dumps(latest))
            else:
                _respond(conn, "503 Service Unavailable", '{"error":"no reading yet"}')
        elif path == "/api/health":
            _respond(conn, "200 OK", '{"ok":true}')
        else:
            _respond(conn, "404 Not Found", '{"error":"not found"}')
    except Exception as e:
        print("API error:", e)
    finally:
        conn.close()


if __name__ == "__main__":
    latest.update({"timestamp": "2026-10-04T00:00:00Z", "device_id": "pico-01",
                   "voltage_v": 240.0, "current_a": 8.71, "power_kw": 2.09})
    start(8080)
    while True:
        poll()
        time.sleep(0.05)
