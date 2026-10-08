#!/usr/bin/env python3
'''Test the output that drives your relay.

    ./test-output.py

Switches the output on for five seconds and off for five seconds,
forever (Ctrl-C to stop, the output is turned off on exit). Measure the
voltage between the output and a ground pin, or watch the SSR's LED.
Stop kiln-controller first (sudo systemctl stop kiln-controller).
The web UI has a one-shot version under Settings -> Diagnostics.
'''
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib'))
from settings import settings
import digitalio
import board
from sensors import pin

heater = digitalio.DigitalInOut(pin(settings.gpio_heat))
heater.direction = digitalio.Direction.OUTPUT
off = bool(settings.gpio_heat_invert)
on = not off

print("\nboard: %s" % (board.board_id))
print("heater output on BCM pin %d, invert = %r\n" % (settings.gpio_heat, settings.gpio_heat_invert))

try:
    while True:
        heater.value = on
        print("%s heater on" % datetime.datetime.now())
        time.sleep(5)
        heater.value = off
        print("%s heater off" % datetime.datetime.now())
        time.sleep(5)
finally:
    heater.value = off
