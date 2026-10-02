# calibrate.py - run from Thonny to check readings. Sends nothing over Wi-Fi.
# 1) Nothing switched on: note "raw amps" and put that value in config.NOISE_AMPS
# 2) Load switched on: compare "watts" with your plug-in energy meter
import time
import config
import sensor

print("Reading once a second. Press Stop / Ctrl+C to end.")
while True:
    raw, n = sensor.read_amps_raw()
    amps, _ = sensor.read_amps()
    print("raw amps: %.3f | amps: %.3f | watts: %6.0f | samples: %d"
          % (raw, amps, amps * config.VOLTS, n))
    time.sleep(1)
