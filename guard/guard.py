import time
import requests
from typing import List, Dict, Any

from guard.rules import evaluate

SERVER_URL = "http://127.0.0.1:8000"
POLL_INTERVAL_SECONDS = 5


def fetch_readings() -> List[Dict[str, Any]]:
    """Polls the web app endpoint for recent telemetry readings."""
    try:
        response = requests.get(f"{SERVER_URL}/api/readings", timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as err:
        print(f"[Guard] Error fetching readings: {err}")
    return []


def post_alert(alert: Dict[str, Any]):
    """Sends a detected anomaly to the POST endpoint."""
    # Format payload for backend server compatibility
    payload = {
        "timestamp": alert.get("at"),
        "rule": alert.get("type"),
        "severity": alert.get("severity"),
        "message": alert.get("title"),
        "details": alert.get("facts", {})
    }

    # Try POST to /api/alerts/ (with trailing slash) first, then /api/alerts
    endpoints = [f"{SERVER_URL}/api/alerts/", f"{SERVER_URL}/api/alerts"]

    for url in endpoints:
        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code in (200, 201):
                print(f"[Guard] Alert posted successfully: {alert.get('type')}")
                return
            elif response.status_code == 405:
                continue  # Try next endpoint variant if 405 Method Not Allowed
            else:
                print(f"[Guard] Failed to post alert ({response.status_code}): {response.text}")
                return
        except Exception as err:
            print(f"[Guard] Error posting alert: {err}")
            return


def run_guard_loop():
    """Main continuous polling and rule enforcement loop."""
    print("[Guard] Starting Anomaly Detection Service...")
    seen_alerts = set()

    while True:
        readings = fetch_readings()
        
        # Evaluate rules using evaluate() from guard/rules.py
        alerts = evaluate(readings)

        for alert in alerts:
            alert_id = f"{alert.get('type')}_{alert.get('at')}"
            
            # Skip posting default "no_data" info message repeatedly
            if alert.get("type") == "no_data":
                continue

            if alert_id not in seen_alerts:
                post_alert(alert)
                seen_alerts.add(alert_id)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_guard_loop()