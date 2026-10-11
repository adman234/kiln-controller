'''Element current sensing with a clamp-on current transformer (CT).

A CT such as the YHDC SCT-013 around one element wire, read by an ADS1115
ADC over I2C, measures the element current:

  - pre-fire check: a 1 second pulse when a firing starts (and when a
    delayed start is set). No current: the controller waits for you to
    switch the kiln on and press Try again, or to ignore the sensor
  - no current while the relay is on: power lost or an element failed
  - current while the relay is off: the SSR is stuck on
  - optional: measured amps instead of the nameplate kW for energy and cost

Samples are only taken while the relay holds one state, so every reading
belongs to a known "on" or "off" period. See docs/current-sensor.md.
'''
import logging
import math
import time

from settings import settings

log = logging.getLogger(__name__)

# ADS1115 registers and config bits
_REG_CONVERSION = 0x00
_REG_CONFIG = 0x01
_MUX = {"diff_0_1": 0b000, "diff_2_3": 0b011, "a0": 0b100, "a1": 0b101, "a2": 0b110, "a3": 0b111}
_PGA = {6.144: 0b000, 4.096: 0b001, 2.048: 0b010, 1.024: 0b011, 0.512: 0b100, 0.256: 0b101}


def rms(samples):
    '''AC RMS: the DC part (bias voltage, ADC offset) is removed first'''
    n = len(samples)
    if n < 2:
        return 0.0
    mean = sum(samples) / n
    return math.sqrt(sum((s - mean) ** 2 for s in samples) / n)


class Ads1115Ct(object):
    '''CT read by an ADS1115 in continuous mode at 860 samples/s'''
    settle = 0.2    # seconds after the relay switches before measuring
    window = 0.25   # seconds sampled per measurement (15 mains cycles at 60Hz)

    def __init__(self):
        import board
        import busio
        from adafruit_bus_device.i2c_device import I2CDevice
        address = int(str(settings.ct_i2c_address), 0)
        self.i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
        self.dev = I2CDevice(self.i2c, address)
        self.full_scale = float(settings.ct_adc_range)
        self.amps_per_volt = float(settings.ct_amps_per_volt)
        cfg = (_MUX[settings.ct_channel] << 12) | (_PGA[self.full_scale] << 9) | (0b111 << 5) | 0b11
        # MODE bit 8 = 0: continuous conversion
        self.dev.write(bytes([_REG_CONFIG, cfg >> 8, cfg & 0xFF]))
        self.buf = bytearray(2)
        self.ptr = bytes([_REG_CONVERSION])
        self.last_error = None
        log.info("current sensor: ADS1115 at %s, %s, %.3fV range, %.1f A/V" % (
            settings.ct_i2c_address, settings.ct_channel, self.full_scale, self.amps_per_volt))

    def read_volts(self):
        self.dev.write_then_readinto(self.ptr, self.buf)
        raw = (self.buf[0] << 8) | self.buf[1]
        if raw & 0x8000:
            raw -= 1 << 16
        return raw * self.full_scale / 32768.0

    def measure(self, heater_on=None):
        '''RMS amps over one window, or None if the ADC could not be read'''
        samples = []
        end = time.monotonic() + self.window
        try:
            while time.monotonic() < end:
                samples.append(self.read_volts())
                if len(samples) % 25 == 0:
                    time.sleep(0)   # let the web server run (gevent)
        except OSError as e:
            self.last_error = str(e)
            log.error("current sensor read failed: %s" % e)
            return None
        self.last_error = None
        return rms(samples) * self.amps_per_volt


class SimulatedCt(object):
    '''for simulation mode and tests. fault: None, "no_current" or "stuck"'''
    settle = 0.0
    window = 0.0

    def __init__(self, amps=None):
        self.amps = amps if amps is not None else settings.kw_elements * 1000.0 / settings.mains_voltage
        self.fault = None
        self.last_error = None

    def measure(self, heater_on):
        if self.fault == "no_current":
            return 0.05
        if self.fault == "stuck":
            return self.amps
        return self.amps if heater_on else 0.05


def create_current_sensor(simulate=False):
    '''returns (sensor or None, error message or None)'''
    if settings.ct_sensor == "none":
        return None, None
    if simulate:
        return SimulatedCt(), None
    try:
        return Ads1115Ct(), None
    except Exception as e:
        log.exception("could not start the current sensor")
        return None, "current sensor (ADS1115 at %s) not found: %s" % (settings.ct_i2c_address, e)


class CurrentMonitor(object):
    '''decides from on/off samples whether something is wrong.
    add() returns (kind, message) once a problem has lasted long enough.'''

    def __init__(self):
        self.reset()

    def reset(self):
        self.no_current_since = None
        self.stuck_since = None
        self.low_since = None
        self.low_warned = False
        self.no_current_alerted = False

    def add(self, amps, heater_on, now, running, normal_amps=None):
        '''normal_amps: the usual current with the elements on, if known'''
        if amps is None:
            return None
        threshold = settings.ct_on_threshold
        # on and off samples alternate; each kind only resets its own timer
        if heater_on:
            if amps < threshold:
                self.low_since = None
                if not (running and settings.ct_detect_no_current) or self.no_current_alerted:
                    self.no_current_since = None
                    return None
                if self.no_current_since is None:
                    self.no_current_since = now
                if now - self.no_current_since >= settings.ct_no_current_seconds:
                    self.no_current_since = None
                    self.no_current_alerted = True
                    return ("no_current", "The elements are switched on but no current flows (%.1f A). "
                            "Kiln unplugged, breaker or kiln switch off, or an element or relay failed." % amps)
                return None
            self.no_current_since = None
            if self.no_current_alerted:
                self.no_current_alerted = False
                return ("current_back", "Current is flowing again (%.1f A)." % amps)
            low = settings.ct_low_amps
            if running and low and amps < low and not self.low_warned:
                if self.low_since is None:
                    self.low_since = now
                if now - self.low_since >= 60:
                    self.low_warned = True
                    return ("low_current", "Element current is only %.1f A (expected at least %.1f A). "
                            "An element may have failed." % (amps, low))
            else:
                self.low_since = None
            return None

        # a stuck SSR passes the full element current. A loose or unplugged
        # CT picking up hum reads far less, so once the normal current is
        # known only half of it or more counts.
        if normal_amps:
            threshold = max(threshold, 0.5 * normal_amps)
        if amps < threshold or not settings.ct_detect_stuck:
            self.stuck_since = None
            return None
        if self.stuck_since is None:
            self.stuck_since = now
        if now - self.stuck_since >= settings.ct_stuck_seconds:
            self.stuck_since = None
            return ("stuck", "%.1f A is flowing with the elements switched off. The relay (SSR) is "
                    "probably stuck on. Switch off power to the kiln." % amps)
        return None
