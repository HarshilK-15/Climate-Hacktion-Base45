# test_receiver.py - run on your LAPTOP (normal Python 3, nothing to install):
#     python test_receiver.py
# Prints every batch the Pico sends and saves all readings to received.jsonl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import datetime


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        now = datetime.datetime.now(datetime.timezone.utc)
        print(now.strftime("%H:%M:%S"), "batch from", body.get("device_id"),
              "| auth:", self.headers.get("Authorization"))
        with open("received.jsonl", "a") as f:
            for r in body.get("readings", []):
                r.setdefault("ts", None)
                if r["ts"] is None:
                    r["ts"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                r["device_id"] = body.get("device_id")
                r["site_id"] = body.get("site_id")
                print("   ", r)
                f.write(json.dumps(r) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass  # keep the output clean


print("Listening on port 8000.")
print("Set config.API_URL to http://<this laptop's IP>:8000/api/readings")
HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
