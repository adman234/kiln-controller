#!/usr/bin/env python3
'''Force the relay output OFF.

systemd runs this before kiln-controller starts and after it stops or
crashes (ExecStartPre / ExecStopPost), so the elements can never be left
on by a dead process. Deliberately tiny: no blinka, no web stack.
'''
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

try:
    from settings import settings
    pin = int(settings.gpio_heat)
    invert = bool(settings.gpio_heat_invert)
    simulate = bool(settings.simulate)
except Exception as e:  # never fail the service because of this script
    print("relay-off: could not read settings (%s), using BCM 23" % e)
    pin, invert, simulate = 23, False, False

if simulate:
    sys.exit(0)

try:
    import RPi.GPIO as GPIO
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH if invert else GPIO.LOW)
    print("relay-off: BCM %d driven %s (off)" % (pin, "high" if invert else "low"))
except Exception as e:
    # fall back to the raspberry pi tools
    tool = shutil.which("pinctrl") or shutil.which("raspi-gpio")
    if tool:
        subprocess.run([tool, "set", str(pin), "op", "dh" if invert else "dl"], check=False)
        print("relay-off: BCM %d set off with %s" % (pin, tool))
    else:
        print("relay-off: could not switch BCM %d off: %s" % (pin, e))
