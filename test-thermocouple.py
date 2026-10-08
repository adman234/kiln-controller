#!/usr/bin/env python3
'''Test your temperature sensor.

Uses the sensor board, thermocouple type and pins from the web UI
settings (storage/settings.json) or config.py.

    ./test-thermocouple.py

Prints the temperature once a second. Touch or warm the thermocouple
and make sure the value changes. Stop kiln-controller first
(sudo systemctl stop kiln-controller) so the two don't fight over the
sensor. The same information is shown live in the web UI under
Settings -> Diagnostics.
'''
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib'))
from settings import settings
from sensors import create_sensor
from units import to_display

if settings.simulate:
    print("simulate is on (config.py / settings), turn it off to test real hardware")
    sys.exit(1)

print("sensor board:      %s" % settings.sensor_board)
print("thermocouple type: %s" % settings.thermocouple_type)
print("spi mode:          %s" % settings.spi_mode)
if settings.spi_mode == "software":
    print("    clock=BCM%d miso=BCM%d mosi=BCM%d" % (settings.spi_sclk, settings.spi_miso, settings.spi_mosi))
print("    chip select=BCM%d\n" % settings.spi_cs)

sensor = create_sensor()
sensor.start()
scale = settings.temp_scale
while True:
    time.sleep(1)
    d = sensor.diagnostics()
    t = d["temperature_c"]
    cj = d["cold_junction_c"]
    msg = "%s  temp %s%s" % (datetime.datetime.now().strftime("%H:%M:%S"),
                             "%.2f" % to_display(t, scale) if t is not None else "----", scale.upper())
    if cj is not None:
        msg += "  cold junction %.2f%s" % (to_display(cj, scale), scale.upper())
    msg += "  errors %d%%" % d["error_percent"]
    if d["recent_errors"]:
        msg += "  last error: %s" % d["recent_errors"][-1]["error"]
    print(msg)
