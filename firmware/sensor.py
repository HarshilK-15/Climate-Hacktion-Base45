# sensor.py - measures AC current from an SCT-013 clamp wired to GP26 (physical pin 31)
import time
import math
from array import array
from machine import ADC
import config

adc = ADC(26)
COUNTS_TO_VOLTS = 3.3 / 65535


def read_amps_raw():
    """Sample the clamp for SAMPLE_WINDOW_MS. Returns (rms_amps, number_of_samples)."""
    buf = array("H")
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < config.SAMPLE_WINDOW_MS:
        buf.append(adc.read_u16())
    n = len(buf)
    mean = sum(buf) / n          # the ~1.65 V middle point set by the two resistors
    total = 0.0
    for s in buf:
        d = s - mean
        total += d * d
    rms_volts = math.sqrt(total / n) * COUNTS_TO_VOLTS
    amps = rms_volts * config.AMPS_PER_VOLT * config.CAL_FACTOR / config.SPLITTER_MULTIPLIER
    return amps, n


def read_amps():
    """Same as read_amps_raw, but removes the no-load noise floor."""
    amps, n = read_amps_raw()
    noise = config.NOISE_AMPS
    if amps <= noise:
        return 0.0, n
    return math.sqrt(amps * amps - noise * noise), n
