# config.py - edit these values for your setup (Pico W, MicroPython)

# --- Wi-Fi (Pico W only works on 2.4 GHz networks) ---
WIFI_SSID = "your-hotspot-name"
WIFI_PASSWORD = "your-hotspot-password"

# --- Where readings are sent ---
# First test: your laptop running test_receiver.py, e.g. "http://192.168.43.20:8000/api/readings"
# Later: the real URL Mech B gives you, e.g. "https://your-app.vercel.app/api/readings"
API_URL = "http://192.168.0.10:8000/api/readings"
DEVICE_TOKEN = "change-me"   # shared secret the server checks
DEVICE_ID = "pico-01"
SITE_ID = "demo-home"

# --- Sensor settings ---
AMPS_PER_VOLT = 20.0      # from the clamp label: SCT-013-020 = 20, -030 = 30, -010 = 10, -005 = 5
CAL_FACTOR = 1.00         # tune in calibration (Step 6)
VOLTS = 240.0             # set to the voltage your plug-in meter shows
NOISE_AMPS = 0.0          # set from calibrate.py with nothing switched on (Step 5)
SPLITTER_MULTIPLIER = 1   # 1 = splitter's x1 slot, 10 = x10 slot (or number of wire turns)

# --- Timing ---
SAMPLE_WINDOW_MS = 200    # about 10 cycles of 50 Hz mains
READ_EVERY_S = 10         # one reading every 10 seconds
SEND_EVERY_N = 6          # send a batch every 6 readings (about once a minute)
MAX_BUFFER = 500          # max unsent readings kept if Wi-Fi is down
