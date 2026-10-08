'''PID autotune using the relay (Astrom-Hagglund) method.

The kiln is heated to the setpoint, then the elements are switched
fully on below (setpoint - hysteresis) and fully off above
(setpoint + hysteresis). The kiln settles into a steady oscillation.
From the oscillation amplitude and period we get the ultimate gain Ku
and period Pu, and from those PID gains via a tuning rule.

This is more reliable for kilns than the old step-response tuner
(kiln-tuner.py) because it measures the kiln at the temperature you
care about, includes its real lag, and needs no curve fitting.
'''
import logging
import math
import time

log = logging.getLogger(__name__)

# multipliers: Kp = a*Ku, Ti = b*Pu, Td = c*Pu
RULES = {
    "tyreus_luyben": {"label": "Conservative (Tyreus-Luyben)", "kp": 1 / 2.2, "ti": 2.2, "td": 1 / 6.3},
    "no_overshoot": {"label": "No overshoot", "kp": 0.2, "ti": 0.5, "td": 1 / 3.0},
    "some_overshoot": {"label": "Some overshoot", "kp": 0.33, "ti": 0.5, "td": 1 / 3.0},
    "ziegler_nichols": {"label": "Classic Ziegler-Nichols (aggressive)", "kp": 0.6, "ti": 0.5, "td": 0.125},
}
DEFAULT_RULE = "tyreus_luyben"


def gains_from_ultimate(ku, pu, rule):
    '''returns kiln-controller style gains. Note pid_ki is inverted:
    iterm += error * dt / ki, so ki = Ti / Kp'''
    r = RULES[rule]
    kp = r["kp"] * ku
    ti = r["ti"] * pu
    td = r["td"] * pu
    return {"kp": kp, "ki": ti / kp, "kd": kp * td}


class RelayAutotuner(object):
    def __init__(self, setpoint, output_high=1.0, hysteresis=3.0, cycles=3,
                 max_overshoot=80.0, timeout=8 * 3600, window_size=100, now=None):
        if not 0.1 <= output_high <= 1.0:
            raise ValueError("output_high must be between 0.1 and 1")
        if hysteresis <= 0:
            raise ValueError("hysteresis must be > 0")
        if cycles < 2:
            raise ValueError("need at least 2 cycles")
        self.setpoint = float(setpoint)
        self.output_high = float(output_high)
        self.output_low = 0.0
        self.hysteresis = float(hysteresis)
        self.cycles = int(cycles)
        self.max_overshoot = float(max_overshoot)
        self.timeout = float(timeout)
        self.window_size = window_size
        self.started = now if now is not None else time.time()
        self.elapsed = 0.0

        self.phase = "heating"  # heating -> relay -> done | failed
        self.relay_on = True
        self.peaks = []
        self.troughs = []
        self.switch_on_times = []
        self.cur_max = -math.inf
        self.cur_min = math.inf
        self.error = None
        self.result = None

    @property
    def finished(self):
        return self.phase in ("done", "failed")

    def fail(self, msg):
        log.error("autotune failed: %s" % msg)
        self.phase = "failed"
        self.error = msg

    def update(self, temp, now):
        '''feed a temperature (C) at time now (s). returns output 0..1'''
        if self.finished:
            return 0.0
        self.elapsed = now - self.started
        if temp is None:
            return 0.0
        if self.elapsed > self.timeout:
            self.fail("timed out after %.1f hours without a steady oscillation" % (self.timeout / 3600))
            return 0.0
        if temp > self.setpoint + self.max_overshoot:
            self.fail("temperature %.0fC went more than %.0f degrees over the setpoint" % (temp, self.max_overshoot))
            return 0.0

        self.cur_max = max(self.cur_max, temp)
        self.cur_min = min(self.cur_min, temp)

        if self.relay_on and temp > self.setpoint + self.hysteresis:
            # switch off
            if self.phase == "relay":
                self.troughs.append(self.cur_min)
            self.phase = "relay"
            self.relay_on = False
            self.cur_max = temp
            log.info("autotune: relay off at %.1f" % temp)
        elif not self.relay_on and temp < self.setpoint - self.hysteresis:
            # switch on
            self.peaks.append(self.cur_max)
            self.switch_on_times.append(now)
            self.relay_on = True
            self.cur_min = temp
            log.info("autotune: relay on at %.1f, peak was %.1f" % (temp, self.peaks[-1]))
            if self.cycles_done() >= self.cycles:
                self.finish()
                return 0.0

        return self.output_high if self.relay_on else self.output_low

    def cycles_done(self):
        return max(0, len(self.switch_on_times) - 1)

    def finish(self):
        # the first peak includes the energy stored during the initial
        # heat-up, so skip it
        peaks = self.peaks[1:]
        troughs = self.troughs[-len(peaks):]
        times = self.switch_on_times
        periods = [b - a for a, b in zip(times, times[1:])]
        if not peaks or not troughs or not periods:
            self.fail("not enough data")
            return
        a = (sum(peaks) / len(peaks) - sum(troughs) / len(troughs)) / 2.0
        pu = sum(periods) / len(periods)
        if a <= 0 or pu <= 0:
            self.fail("could not measure an oscillation")
            return
        d = (self.output_high - self.output_low) / 2.0 * self.window_size
        if a > self.hysteresis:
            ku = 4 * d / (math.pi * math.sqrt(a * a - self.hysteresis * self.hysteresis))
        else:
            ku = 4 * d / (math.pi * a)

        spread = (max(periods) - min(periods)) / pu if len(periods) > 1 else 0
        rules = {}
        for key, r in RULES.items():
            g = gains_from_ultimate(ku, pu, key)
            g["label"] = r["label"]
            rules[key] = g
        self.result = {
            "ku": ku, "pu": pu, "amplitude": a, "periods": periods,
            "period_spread": spread,
            "rules": rules, "recommended": DEFAULT_RULE,
            "warning": ("oscillation periods varied by %d%%, consider running again" % (spread * 100))
                       if spread > 0.3 else None,
        }
        self.phase = "done"
        log.info("autotune done: Ku=%.3f Pu=%.1fs amplitude=%.2f -> %s" % (ku, pu, a, rules[DEFAULT_RULE]))

    def status(self):
        return {
            "phase": self.phase,
            "setpoint": self.setpoint,
            "hysteresis": self.hysteresis,
            "output_high": self.output_high,
            "relay_on": self.relay_on,
            "elapsed": self.elapsed,
            "cycles_done": self.cycles_done(),
            "cycles_needed": self.cycles,
            "peak": self.peaks[-1] if self.peaks else None,
            "trough": self.troughs[-1] if self.troughs else None,
            "amplitude": self.result["amplitude"] if self.result else None,
            "error": self.error,
            "result": self.result,
        }
