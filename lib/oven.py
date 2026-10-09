import collections
import datetime
import json
import logging
import os
import threading
import time

from settings import settings, STORAGE_DIR, atomic_write_json
from sensors import create_sensor, TempSensorSimulated
from profiles import Profile, ProfileStore, ProfileError
from history import RunHistory
from autotune import RelayAutotuner
from notify import notifier
from current import CurrentMonitor, create_current_sensor
from units import to_display, delta_to_display
import sdnotify
import config

log = logging.getLogger(__name__)

# Profile is re-exported for older scripts and tests (from oven import Profile)
__all__ = ["Oven", "RealOven", "SimulatedOven", "PID", "Profile"]

SCHEDULE_FILE = os.path.join(STORAGE_DIR, "scheduled.json")
STATE_SAVE_INTERVAL = 30  # seconds between automatic restart state writes


class DupFilter(object):
    def __init__(self):
        self.msgs = set()

    def filter(self, record):
        rv = record.msg not in self.msgs
        self.msgs.add(record.msg)
        return rv


class Duplogger():
    def __init__(self):
        self.log = logging.getLogger("%s.dupfree" % (__name__))
        dup_filter = DupFilter()
        self.log.addFilter(dup_filter)

    def logref(self):
        return self.log


duplog = Duplogger().logref()


class Output(object):
    '''GPIO outputs: the solid state relay that switches the elements,
    plus the optional safety contactor (in series with the SSR, closed only
    while firing) and heartbeat for an external watchdog relay.'''
    def __init__(self):
        import digitalio
        from sensors import pin

        def out(bcm, value):
            o = digitalio.DigitalInOut(pin(bcm))
            o.direction = digitalio.Direction.OUTPUT
            o.value = value
            return o

        self.active = False
        self.off = bool(settings.gpio_heat_invert)
        self.on = not self.off
        self.heater = out(settings.gpio_heat, self.off)

        self.contactor = None
        self.contactor_closed = False
        self.c_off = bool(settings.gpio_contactor_invert)
        if settings.gpio_contactor is not None and settings.gpio_contactor >= 0:
            self.contactor = out(settings.gpio_contactor, self.c_off)
            log.info("safety contactor on BCM %d" % settings.gpio_contactor)

        self.heartbeat = None
        self.hb = False
        if settings.gpio_heartbeat is not None and settings.gpio_heartbeat >= 0:
            self.heartbeat = out(settings.gpio_heartbeat, False)
            log.info("watchdog heartbeat on BCM %d" % settings.gpio_heartbeat)

    def close_contactor(self):
        if self.contactor is not None and not self.contactor_closed:
            self.heater.value = self.off
            self.contactor.value = not self.c_off
            self.contactor_closed = True
            # let the contacts settle before the SSR switches any current
            time.sleep(0.3)

    def open_contactor(self):
        self.heater.value = self.off
        if self.contactor is not None and self.contactor_closed:
            self.contactor.value = self.c_off
            self.contactor_closed = False

    def beat(self):
        if self.heartbeat is not None:
            self.hb = not self.hb
            self.heartbeat.value = self.hb

    def heater_on(self):
        self.close_contactor()
        self.heater.value = self.on

    def heater_off(self):
        self.heater.value = self.off

    def heat(self, sleepfor):
        self.heater_on()
        time.sleep(sleepfor)

    def cool(self, sleepfor):
        '''no active cooling, so sleep'''
        self.heater.value = self.off
        time.sleep(sleepfor)

    def force_off(self):
        self.heater.value = self.off


class Board(object):
    '''This represents a blinka board where this code runs.'''
    def __init__(self):
        log.info("board: %s" % (self.name))
        self.temp_sensor.start()


class RealBoard(Board):
    def __init__(self):
        import board
        self.name = board.board_id
        self.temp_sensor = create_sensor()
        Board.__init__(self)


class SimulatedBoard(Board):
    def __init__(self):
        self.name = "simulated"
        self.temp_sensor = TempSensorSimulated()
        Board.__init__(self)


class Oven(threading.Thread):
    '''parent oven class. this has all the common code
       for either a real or simulated oven'''
    def __init__(self, store=None, history=None):
        threading.Thread.__init__(self)
        self.daemon = True
        self.temperature = 0
        self.time_step = settings.sensor_time_wait
        self.store = store or ProfileStore()
        self.history = history or RunHistory()
        self.ovenwatcher = None
        self.scheduled = None
        self.autotuner = None
        self.last_autotune = None
        self.pending_relay_test = 0
        self.last_error = None
        self.last_state_save = 0
        self.heat_rate = 0
        self.heat_rate_temps = []
        self.output_level = 0.0
        self.safety_samples = collections.deque()
        # current sensor (CT): subclasses set self.ct before calling us
        self.ct = getattr(self, "ct", None)
        self.ct_error = getattr(self, "ct_error", None)
        self.ct_monitor = CurrentMonitor()
        self.ct_last = None          # last sample: {"amps", "heater_on", "time"}
        self.ct_on_amps = None       # last reading with the elements on
        self.ct_last_sample = {True: -1e9, False: -1e9}
        self.ct_fault = None
        self.prefire = None          # result of the last pre-fire check
        self.tc_bad_since = None
        self.tc_alerted = False
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        self.cost = 0
        self.kwh = 0
        self.heat_on_total = 0
        self.state = "IDLE"
        self.profile = None
        self.start_time = 0
        self.startat = 0
        self.runtime = 0
        self.run_start_runtime = 0
        self.run_started_iso = None
        self.run_started_clock = None
        self.max_temp = None
        self.totaltime = 0
        self.target = 0
        self.heat = 0
        self.pid = PID(ki=settings.pid_ki, kd=settings.pid_kd, kp=settings.pid_kp)
        self.catching_up = False
        self.catchup_since = None
        self.behind_notified = False
        self.prefire_pending = False
        self.prefire_tries = 0
        self.prefire_next = 0.0
        if hasattr(self, "ct_monitor"):
            self.ct_monitor.reset()

    # ------------------------------------------------------------------
    # time helpers (overridden by the simulator)
    def clock(self):
        return time.time()

    def pid_now(self):
        return datetime.datetime.now()

    def current_temp(self):
        '''median sensor temperature + calibration offset (C), or None'''
        try:
            return self.board.temp_sensor.offset_temperature()
        except AttributeError:
            return None

    # ------------------------------------------------------------------
    @staticmethod
    def get_start_from_temperature(profile, temp):
        target_temp = profile.get_target_temperature(0)
        if temp is not None and temp > target_temp + 5:
            startat = profile.find_next_time_from_temperature(temp)
            log.info("seek_start is in effect, starting at: {} s, {} deg".format(round(startat), round(temp)))
        else:
            startat = 0
        return startat

    def set_heat_rate(self, clock, temp):
        '''heat rate is the heating rate in degrees/hour'''
        if temp is None:
            return
        numtemps = 60
        self.heat_rate_temps.append((clock, temp))
        if len(self.heat_rate_temps) > numtemps:
            self.heat_rate_temps = self.heat_rate_temps[-1 * numtemps:]
        time2, temp2 = self.heat_rate_temps[-1]
        time1, temp1 = self.heat_rate_temps[0]
        if time2 > time1:
            self.heat_rate = ((temp2 - temp1) / (time2 - time1)) * 3600

    def run_profile(self, profile, startat=0, allow_seek=True):
        '''startat is in minutes'''
        with self.lock:
            if self.state == "TUNING":
                raise RuntimeError("autotune is running, stop it first")
            if self.state in ("RUNNING", "PAUSED"):
                self.record_run("replaced by a new run")
            log.debug('run_profile run on thread ' + threading.current_thread().name)
            runtime = startat * 60
            if allow_seek and self.state in ("IDLE", "SCHEDULED") and settings.seek_start:
                runtime += self.get_start_from_temperature(profile, self.current_temp())

            self.reset()
            self.clear_schedule()
            self.last_error = None
            self.startat = runtime
            self.runtime = runtime
            self.run_start_runtime = runtime
            self.run_started_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
            self.run_started_clock = self.clock()
            self.start_time = self.get_start_time()
            self.profile = profile
            self.totaltime = profile.get_duration()
            self.state = "RUNNING"
            self.arm_prefire()
            self.save_automatic_restart_state(force=True)
            log.info("Running schedule %s starting at %d minutes" % (profile.name, runtime / 60))

    def abort_run(self, reason="stopped by user"):
        with self.lock:
            if self.state in ("RUNNING", "PAUSED"):
                self.notify_run_end(reason)
                self.record_run(reason)
            if self.state == "TUNING" and self.autotuner and not self.autotuner.finished:
                self.autotuner.fail(reason)
                self.last_autotune = self.autotuner.status()
            self.autotuner = None
            self.clear_schedule()
            self.reset()
            self.output_off()
            self.save_automatic_restart_state(force=True)

    def pause(self):
        with self.lock:
            if self.state != "RUNNING":
                return False
            self.state = "PAUSED"
            return True

    def resume(self):
        with self.lock:
            if self.state != "PAUSED":
                return False
            self.state = "RUNNING"
            return True

    def output_off(self):
        '''subclasses force the relay off'''
        self.output_level = 0.0

    def get_start_time(self):
        return datetime.datetime.now() - datetime.timedelta(milliseconds=self.runtime * 1000)

    def kiln_must_catch_up(self):
        '''shift the whole schedule forward in time by one time_step
        to wait for the kiln to catch up'''
        if self.current_temp() is None:
            # no trustworthy reading: the elements are off, so hold the
            # schedule instead of letting it run on without us
            self.start_time = self.get_start_time()
            return
        if settings.kiln_must_catch_up:
            temp = self.current_temp()
            if temp is None:
                return
            # kiln too cold, wait for it to heat up
            if self.target - temp > settings.pid_control_window:
                log.info("kiln must catch up, too cold, shifting schedule")
                self.start_time = self.get_start_time()
                self.catching_up = True
                return
            # kiln too hot, wait for it to cool down
            if temp - self.target > settings.pid_control_window:
                log.info("kiln must catch up, too hot, shifting schedule")
                self.start_time = self.get_start_time()
                self.catching_up = True
                return
            self.catching_up = False

    def update_runtime(self):
        runtime_delta = datetime.datetime.now() - self.start_time
        if runtime_delta.total_seconds() < 0:
            runtime_delta = datetime.timedelta(0)
        self.runtime = runtime_delta.total_seconds()

    def update_target_temp(self):
        self.target = self.profile.get_target_temperature(self.runtime)

    def check_emergency(self):
        '''returns an error message if the kiln must be shut down'''
        temp = self.current_temp()
        if temp is not None and temp >= settings.emergency_shutoff_temp:
            log.error("emergency!!! temperature too high")
            if not settings.ignore_temp_too_high:
                return "emergency: temperature %.0fC reached emergency shutoff %.0fC" % (temp, settings.emergency_shutoff_temp)
        # thermocouple errors are handled by handle_sensor_health(): the
        # elements stay off while there is no trustworthy reading
        return None

    TC_ALERT_SECONDS = 30

    def handle_sensor_health(self, temp):
        '''no trustworthy temperature (sensor errors, stale or out of range
        readings) counts as "too hot": the elements stay off. Alert after
        TC_ALERT_SECONDS, stop only if tc_error_action says so.
        returns True if the run was stopped'''
        now = self.clock()
        if temp is not None:
            if self.tc_alerted:
                notifier.send("Thermocouple readings are back",
                              "The temperature reads %.0f\u00b0%s again, heating has resumed." % (
                                  to_display(temp, settings.temp_scale), settings.temp_scale.upper()), key="tc-back")
                if self.last_error and self.last_error.startswith("No trustworthy temperature"):
                    self.last_error = None
            self.tc_bad_since = None
            self.tc_alerted = False
            return False
        if self.tc_bad_since is None:
            self.tc_bad_since = now
        bad_for = now - self.tc_bad_since
        if not self.tc_alerted and bad_for >= self.TC_ALERT_SECONDS:
            self.tc_alerted = True
            errors = getattr(self.board.temp_sensor, "recent_errors", None)
            why = errors[-1]["error"] if errors else "no reading"
            msg = ("No trustworthy temperature for %d seconds (%s). The elements are held off and the schedule "
                   "waits until readings come back. Check the thermocouple and its wiring." % (bad_for, why))
            notifier.send("Thermocouple problem", msg, urgent=True, key="sensor")
            self.last_error = msg
        if (settings.tc_error_action == "stop" and self.ct_running() and
                bad_for >= settings.tc_error_stop_minutes * 60):
            msg = "stopped: no trustworthy temperature for %d minutes" % (bad_for / 60)
            self.abort_run(msg)
            self.last_error = msg
            return True
        return False

    def reset_if_emergency(self):
        msg = self.check_emergency()
        if msg:
            self.abort_run(msg)
            self.last_error = msg

    def reset_if_schedule_ended(self):
        if self.runtime > self.totaltime:
            log.info("schedule ended, shutting down")
            log.info("total cost = %s%.2f" % (settings.currency_type, self.cost))
            self.abort_run("completed")

    def notify_run_end(self, reason):
        name = self.profile.name if self.profile else "firing"
        if reason == "completed":
            if settings.notify_on_complete:
                notifier.send("Firing complete", "%s finished. %.1f kWh, %s%.2f." % (
                    name, self.kwh, settings.currency_type, self.cost), key="complete-%s" % time.time())
        elif reason != "stopped by user" and not reason.startswith("replaced"):
            notifier.send("Firing stopped", "%s stopped: %s" % (name, reason), urgent=True,
                          key="stopped-%s" % reason)

    def add_energy(self, heat_on_seconds):
        self.heat_on_total += heat_on_seconds
        if settings.ct_energy and self.ct is not None and self.ct_on_amps:
            kw = settings.mains_voltage * self.ct_on_amps / 1000.0
            self.kwh += kw * heat_on_seconds / 3600.0
        else:
            self.kwh += settings.kw_elements * heat_on_seconds / 3600.0
        self.cost = self.kwh * settings.kwh_rate

    # ------------------------------------------------------------------
    # safety monitoring: stuck relay, no heating, behind schedule
    def safety_sample(self, temp):
        if temp is None:
            return
        now = self.clock()
        self.safety_samples.append((now, temp, self.output_level))
        keep = max(settings.runaway_minutes, settings.stall_minutes) * 60 + 120
        while self.safety_samples and now - self.safety_samples[0][0] > keep:
            self.safety_samples.popleft()

    def safety_window(self, seconds):
        '''samples covering the last `seconds`, or None if we have not
        been watching that long'''
        if not self.safety_samples:
            return None
        now = self.safety_samples[-1][0]
        if now - self.safety_samples[0][0] < seconds:
            return None
        return [x for x in self.safety_samples if now - x[0] <= seconds]

    def check_safety(self):
        '''returns (kind, message) for a dangerous condition, else None'''
        scale = settings.temp_scale
        u = "\u00b0" + scale.upper()
        if settings.runaway_detect:
            w = self.safety_window(settings.runaway_minutes * 60)
            if w and all(x[2] == 0 for x in w):
                rise = w[-1][1] - w[0][1]
                if rise > settings.runaway_rise:
                    return ("runaway", "Kiln heated %.0f%s in %d minutes with the elements switched off. "
                            "The relay (SSR) is probably stuck on. Switch off power to the kiln." % (
                                delta_to_display(rise, scale), u, settings.runaway_minutes))
        if settings.stall_detect and self.state in ("RUNNING", "PAUSED", "TUNING"):
            w = self.safety_window(settings.stall_minutes * 60)
            if w and all(x[2] >= 0.95 for x in w):
                rise = w[-1][1] - w[0][1]
                if rise < settings.stall_rise:
                    return ("stall", "No temperature rise (%.1f%s) after %d minutes at full power at %.0f%s. "
                            "Check the thermocouple is in the kiln, and the elements and relay." % (
                                delta_to_display(rise, scale), u, settings.stall_minutes,
                                to_display(w[-1][1], scale), u))
        return None

    def handle_safety(self):
        problem = self.check_safety()
        if not problem:
            return
        kind, msg = problem
        log.error("SAFETY: %s" % msg)
        notifier.send("KILN ALARM" if kind == "runaway" else "Kiln not heating", msg, urgent=True, key=kind)
        stop = kind == "stall" or settings.runaway_action == "stop"
        if stop and self.state in ("RUNNING", "PAUSED", "TUNING"):
            self.abort_run("safety: " + msg)
        self.output_off()
        self.last_error = msg
        self.safety_samples.clear()

    def check_behind_schedule(self):
        if not self.catching_up:
            self.catchup_since = None
            return
        now = self.clock()
        if self.catchup_since is None:
            self.catchup_since = now
        elif not self.behind_notified and now - self.catchup_since > settings.behind_schedule_minutes * 60:
            self.behind_notified = True
            notifier.send("Kiln behind schedule",
                          "%s has been waiting for the kiln to catch up for %d minutes (at %.0f\u00b0%s, target %.0f). "
                          "Elements may be wearing out or the ramp is too fast." % (
                              self.profile.name if self.profile else "The firing",
                              (now - self.catchup_since) / 60,
                              to_display(self.current_temp() or 0, settings.temp_scale), settings.temp_scale.upper(),
                              to_display(self.target, settings.temp_scale)), key="behind")

    # ------------------------------------------------------------------
    # current sensor (CT)
    CT_SAMPLE_INTERVAL = 2.0  # seconds between readings in the same relay state
    CT_IDLE_INTERVAL = 5.0

    PREFIRE_RETRY_SECONDS = 20.0

    def arm_prefire(self, scheduled=False):
        enabled = self.ct is not None and bool(settings.ct_prefire_check)
        if scheduled:
            enabled = enabled and bool(settings.ct_prefire_on_schedule)
        self.prefire_pending = enabled
        self.prefire_tries = 0
        self.prefire_next = 0.0

    def ct_running(self):
        return self.state in ("RUNNING", "PAUSED", "TUNING")

    def ct_sample(self, heater_on, phase_seconds):
        '''measure during a relay phase of phase_seconds if there is time and
        it is due. returns the seconds spent measuring'''
        ct = self.ct
        if ct is None or phase_seconds < ct.settle + ct.window + 0.05:
            return 0.0
        now = self.clock()
        # idle: an occasional look for a stuck relay is enough
        interval = self.CT_SAMPLE_INTERVAL if heater_on or self.ct_running() else self.CT_IDLE_INTERVAL
        if now - self.ct_last_sample[heater_on] < interval:
            return 0.0
        self.ct_last_sample[heater_on] = now
        t0 = time.monotonic()
        if ct.settle:
            time.sleep(ct.settle)
        amps = ct.measure(heater_on)
        self.ct_record(amps, heater_on)
        return time.monotonic() - t0

    def ct_record(self, amps, heater_on):
        if amps is None:
            return
        now = self.clock()
        self.ct_last = {"amps": round(amps, 2), "heater_on": heater_on, "time": time.time()}
        if heater_on and amps >= settings.ct_on_threshold:
            self.ct_on_amps = amps
        problem = self.ct_monitor.add(amps, heater_on, now, self.ct_running(), normal_amps=self.ct_on_amps)
        if problem and self.ct_fault is None:
            self.ct_fault = problem
            if problem[0] == "stuck":
                # don't wait for the next tick to drop the output (and a
                # contactor if there is one)
                self.output_off()

    def handle_ct_fault(self):
        '''act on what the current sensor found. returns True if the run
        was stopped'''
        if self.ct_fault is None:
            return False
        kind, msg = self.ct_fault
        self.ct_fault = None
        log.error("CURRENT SENSOR: %s" % msg)
        if kind == "current_back":
            notifier.send("Kiln drawing power again", msg, key="ct-back")
            return False
        if kind == "low_current":
            notifier.send("Low element current", msg, key="ct-low")
            return False
        if kind == "stuck":
            stop = settings.ct_stuck_action == "stop"
            notifier.send("KILN ALARM", msg, urgent=True, key="ct-stuck")
            self.output_off()
            self.last_error = msg
            # stopping also cancels a delayed start
            if stop and (self.ct_running() or self.state == "SCHEDULED"):
                self.abort_run("safety: " + msg)
                self.last_error = msg
                return True
            return False
        # no current while on
        stop = settings.ct_no_current_action == "stop"
        notifier.send("Kiln not drawing power", msg + ("" if stop else " The firing continues."),
                      urgent=True, key="ct-none")
        if stop and self.ct_running():
            self.abort_run("safety: " + msg)
            self.output_off()
            self.last_error = msg
            return True
        return False

    def pulse_and_measure(self, seconds):
        '''switch the elements on for seconds and return the amps measured
        meanwhile (None if the sensor could not be read)'''
        raise NotImplementedError

    def prefire_step(self):
        if not self.prefire_waiting():
            self.run_prefire_check()
            return
        # between tries: elements off, schedule held
        self.output_off()
        if self.state == "RUNNING":
            with self.lock:
                self.start_time = self.get_start_time()
        self.idle_wait()

    def prefire_waiting(self):
        '''True while a pre-fire retry is due later: hold the elements off'''
        return self.prefire_pending and self.clock() < self.prefire_next

    def run_prefire_check(self):
        '''one pre-fire pulse. Retries a few times before giving up.
        returns False if the run (or delayed start) was cancelled'''
        seconds = float(settings.ct_prefire_seconds)
        self.prefire_tries += 1
        tries = max(1, int(settings.ct_prefire_tries))
        log.info("pre-fire check %d/%d: elements on for %.1fs" % (self.prefire_tries, tries, seconds))
        amps = self.pulse_and_measure(seconds)
        ok = amps is not None and amps >= settings.ct_on_threshold
        self.prefire = {"ok": ok, "amps": None if amps is None else round(amps, 2), "time": time.time(),
                        "try": self.prefire_tries, "tries": tries, "pending": False}
        if ok:
            self.prefire_pending = False
            self.ct_on_amps = amps
            log.info("pre-fire check passed: %.1f A" % amps)
            return True
        if self.state == "SCHEDULED" and self.scheduled:
            name = self.scheduled.get("profile")
        else:
            name = self.profile.name if self.profile else "Autotune"
        if amps is None:
            # a broken sensor is not evidence that the kiln is off
            self.prefire_pending = False
            msg = "Pre-fire check: the current sensor could not be read (%s). Starting anyway." % (
                getattr(self.ct, "last_error", None) or "no reading")
            log.error(msg)
            notifier.send("Kiln pre-fire check skipped", "%s: %s" % (name, msg), key="prefire-sensor")
            return True
        if self.prefire_tries < tries:
            self.prefire_next = self.clock() + self.PREFIRE_RETRY_SECONDS
            self.prefire["pending"] = True
            log.warning("pre-fire check: no current (%.1f A), trying again in %ds" % (amps, self.PREFIRE_RETRY_SECONDS))
            return True
        self.prefire_pending = False
        msg = ("Pre-fire check: no current with the elements on (%.1f A, %d tries). Is the kiln plugged in and "
               "switched on, and the breaker on?" % (amps, self.prefire_tries))
        stop = settings.ct_prefire_action == "stop"
        if self.state == "SCHEDULED":
            title = "Delayed start cancelled" if stop else "Kiln pre-fire check failed"
            tail = "" if stop else " The delayed start is still set."
        else:
            title = "Kiln did not start" if stop else "Kiln pre-fire check failed"
            tail = "" if stop else " Started anyway."
        log.error(msg)
        notifier.send(title, "%s: %s%s" % (name, msg, tail), urgent=True, key="prefire-%s" % time.time())
        if stop:
            self.abort_run(msg)
            self.last_error = msg
            return False
        return True

    def ct_status(self):
        if self.ct is None and not self.ct_error:
            return None
        return {"enabled": self.ct is not None, "error": self.ct_error or getattr(self.ct, "last_error", None),
                "last": self.ct_last, "on_amps": round(self.ct_on_amps, 2) if self.ct_on_amps else None,
                "prefire": self.prefire, "threshold": settings.ct_on_threshold}

    # ------------------------------------------------------------------
    # run history
    def record_run(self, reason):
        if not self.profile or self.run_started_clock is None:
            return
        elapsed = self.clock() - self.run_started_clock
        if elapsed < 120:
            return
        reached = min(self.runtime, self.totaltime)
        completed = reason == "completed" or reached >= 0.95 * self.totaltime
        try:
            dh = self.profile.degree_hours(self.run_start_runtime, reached)
        except Exception:
            dh = 0
        self.history.add({
            "profile": self.profile.name,
            "started": self.run_started_iso,
            "ended": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "elapsed_s": round(elapsed),
            "schedule_from_s": round(self.run_start_runtime),
            "schedule_to_s": round(reached),
            "completed": completed,
            "reason": reason,
            "heat_on_s": round(self.heat_on_total),
            "kwh": round(self.kwh, 3),
            "cost": round(self.cost, 2),
            "degree_hours": round(dh, 1),
            "max_temp_c": round(self.max_temp, 1) if self.max_temp is not None else None,
            "simulated": bool(settings.simulate),
        })

    # ------------------------------------------------------------------
    # delayed / scheduled starts
    def schedule_profile(self, profile_name, start_at, startat=0):
        '''start_at is a unix timestamp'''
        with self.lock:
            if self.state not in ("IDLE", "SCHEDULED"):
                raise RuntimeError("kiln is busy (%s)" % self.state)
            if self.store.get_profile(profile_name) is None:
                raise ProfileError("profile %s not found" % profile_name)
            self.scheduled = {"profile": profile_name, "start_at": float(start_at), "startat": startat}
            self.state = "SCHEDULED"
            self.arm_prefire(scheduled=True)
            try:
                atomic_write_json(SCHEDULE_FILE, self.scheduled)
            except OSError as e:
                log.error("could not persist schedule: %s" % e)
            log.info("scheduled %s to start at %s" % (profile_name, datetime.datetime.fromtimestamp(start_at)))

    def clear_schedule(self):
        self.scheduled = None
        if os.path.exists(SCHEDULE_FILE):
            try:
                os.remove(SCHEDULE_FILE)
            except OSError:
                pass

    def load_schedule(self):
        '''restore a pending delayed start after a reboot'''
        try:
            with open(SCHEDULE_FILE) as f:
                s = json.load(f)
        except (OSError, ValueError):
            return
        now = time.time()
        if s.get("start_at", 0) > now:
            self.scheduled = s
            self.state = "SCHEDULED"
            log.info("restored scheduled start of %s" % s.get("profile"))
        elif now - s.get("start_at", 0) <= settings.automatic_restart_window * 60:
            self.scheduled = s
            self.start_scheduled()
        else:
            log.info("dropping scheduled start of %s, missed while powered off" % s.get("profile"))
            self.clear_schedule()

    def start_scheduled(self):
        s = self.scheduled
        profile = self.store.get_profile(s["profile"])
        if profile is None:
            self.last_error = "scheduled profile %s no longer exists" % s["profile"]
            log.error(self.last_error)
            self.clear_schedule()
            self.state = "IDLE"
            return
        log.info("starting scheduled run of %s" % profile.name)
        self.run_profile(profile, startat=s.get("startat", 0))
        if settings.notify_on_complete:
            notifier.send("Firing started", "Scheduled firing %s has started." % profile.name,
                          key="started-%s" % time.time())
        if self.ovenwatcher:
            self.ovenwatcher.record(profile)

    # ------------------------------------------------------------------
    # autotune
    def start_autotune(self, setpoint, output_high=1.0, hysteresis=3.0, cycles=3, max_overshoot=80.0):
        with self.lock:
            if self.state not in ("IDLE",):
                raise RuntimeError("kiln is busy (%s)" % self.state)
            if setpoint + max_overshoot >= settings.emergency_shutoff_temp:
                max_overshoot = max(5.0, settings.emergency_shutoff_temp - setpoint - 1)
            if setpoint >= settings.emergency_shutoff_temp:
                raise ValueError("setpoint is above the emergency shutoff temperature")
            self.reset()
            self.autotuner = RelayAutotuner(setpoint, output_high=output_high, hysteresis=hysteresis,
                                            cycles=cycles, max_overshoot=max_overshoot, now=self.clock())
            self.last_autotune = None
            self.last_error = None
            self.target = setpoint
            self.state = "TUNING"
            self.arm_prefire()
            if self.ovenwatcher:
                self.ovenwatcher.record(None)
            log.info("autotune started at %.1fC" % setpoint)

    def autotune_step(self):
        '''returns the output to apply, or None when tuning ended'''
        temp = self.current_temp()
        out = self.autotuner.update(temp, self.clock())
        self.target = self.autotuner.setpoint
        self.output_level = out
        msg = self.check_emergency()
        if msg:
            self.abort_run(msg)
            self.last_error = msg
            return None
        if self.autotuner.finished:
            self.last_autotune = self.autotuner.status()
            if self.autotuner.phase == "done":
                notifier.send("Autotune finished", "Open Settings -> PID & Autotune to apply the new values.",
                              key="autotune-%s" % time.time())
            else:
                notifier.send("Autotune failed", str(self.autotuner.error), urgent=True,
                              key="autotune-%s" % time.time())
            self.autotuner = None
            self.state = "IDLE"
            self.target = 0
            self.output_off()
            return None
        return out

    # ------------------------------------------------------------------
    # automatic restarts
    def get_state(self):
        temp = self.current_temp()
        state = {
            'cost': self.cost,
            'kwh': self.kwh,
            'runtime': self.runtime,
            'temperature': temp if temp is not None else 0,
            'sensor_ok': temp is not None,
            'target': self.target,
            'state': self.state,
            'heat': self.heat,
            'output': self.output_level,
            'heat_rate': self.heat_rate,
            'totaltime': self.totaltime,
            'kwh_rate': settings.kwh_rate,
            'currency_type': settings.currency_type,
            'profile': self.profile.name if self.profile else None,
            'pidstats': self.pid.pidstats,
            'catching_up': self.catching_up,
            'scheduled': self.scheduled,
            'autotune': self.autotuner.status() if self.autotuner else self.last_autotune,
            'error': self.last_error,
            'simulate': bool(settings.simulate),
            'current': self.ct_status(),
        }
        return state

    def save_state(self):
        d = self.get_state()
        d["temp_units"] = "c"
        d["heat_on_s"] = self.heat_on_total
        d.pop("autotune", None)
        d.pop("current", None)
        atomic_write_json(config.automatic_restart_state_file, d, ensure_ascii=False, indent=4)

    def state_file_is_old(self):
        '''True if the state file is older than the restart window,
        cannot be opened or does not exist'''
        if os.path.isfile(config.automatic_restart_state_file):
            state_age = os.path.getmtime(config.automatic_restart_state_file)
            minutes = (time.time() - state_age) / 60
            if minutes <= settings.automatic_restart_window:
                return False
        return True

    def save_automatic_restart_state(self, force=False):
        # only save state if the feature is enabled. Writing is throttled
        # to spare the SD card.
        if not settings.automatic_restarts:
            return False
        now = time.time()
        if not force and now - self.last_state_save < STATE_SAVE_INTERVAL:
            return False
        self.last_state_save = now
        try:
            self.save_state()
        except OSError as e:
            log.error("could not save state: %s" % e)
        return True

    def read_state_file(self):
        try:
            with open(config.automatic_restart_state_file) as infile:
                return json.load(infile)
        except (OSError, ValueError) as e:
            log.error("could not read state file: %s" % e)
            return None

    def should_i_automatic_restart(self):
        if not settings.automatic_restarts:
            return False
        if self.state_file_is_old():
            duplog.info("automatic restart not possible. state file does not exist or is too old.")
            return False
        d = self.read_state_file()
        if not d or d.get("state") not in ("RUNNING", "PAUSED"):
            duplog.info("automatic restart not possible. state = %s" % (d.get("state") if d else None))
            return False
        return True

    def automatic_restart(self):
        d = self.read_state_file()
        if not d:
            return
        startat = d["runtime"] / 60
        profile = self.store.get_profile(d.get("profile"))
        if profile is None:
            log.error("automatic restart: profile %s not found" % d.get("profile"))
            return
        log.info("automatically restarting profile = %s at minute = %d" % (profile.name, startat))
        notifier.send("Kiln restarted after power loss",
                      "%s resumed at %d minutes into the schedule." % (profile.name, startat), key="restart")
        self.run_profile(profile, startat=startat, allow_seek=False)  # We don't want a seek on an auto restart.
        self.add_energy(d.get("heat_on_s", 0) or 0)
        if not self.heat_on_total and d.get("cost"):
            self.cost = d["cost"]
        if d.get("state") == "PAUSED":
            self.pause()
        if self.ovenwatcher:
            self.ovenwatcher.record(profile)

    def set_ovenwatcher(self, watcher):
        log.info("ovenwatcher set in oven class")
        self.ovenwatcher = watcher

    # ------------------------------------------------------------------
    # main loop
    def startup(self):
        # give the sensor a moment to take its first readings
        for _ in range(20):
            if self.current_temp() is not None:
                break
            time.sleep(0.5)
        if self.should_i_automatic_restart():
            self.automatic_restart()
        elif self.state == "IDLE":
            self.load_schedule()

    def run(self):
        try:
            self.startup()
        except Exception:
            log.exception("error during oven startup")
        while True:
            try:
                self.tick()
            except Exception as e:
                self.handle_internal_error(e)
                time.sleep(1)

    def handle_internal_error(self, e):
        '''a bug or unexpected hardware error in one cycle: never leave the
        relay on, tell someone, and by default carry on with the next cycle'''
        log.exception("unexpected error in oven loop")
        try:
            self.output_off()
        except Exception:
            log.exception("could not switch the output off")
        msg = "internal error: %r" % (e,)
        self.last_error = msg
        stop = settings.get("internal_error_action", "notify") == "stop"
        notifier.send("Kiln controller error", "%s. %s" % (
            msg, "The firing was stopped." if stop else "Elements were off for that cycle, the firing continues."),
            urgent=True, key="internal-%s" % type(e).__name__)
        if stop:
            try:
                self.abort_run(msg)
            except Exception:
                log.exception("error while aborting")
                self.reset()
                self.output_off()
            self.last_error = msg

    def beat(self):
        '''called every cycle: tells systemd and any external watchdog
        that the control loop is alive'''
        sdnotify.watchdog()

    def tick(self):
        self.beat()
        temp = self.current_temp()
        self.set_heat_rate(self.clock(), temp)
        self.safety_sample(temp)
        self.handle_safety()
        if self.handle_sensor_health(temp):
            return
        if self.handle_ct_fault():
            return
        if temp is not None and self.state in ("RUNNING", "PAUSED"):
            self.max_temp = temp if self.max_temp is None else max(self.max_temp, temp)

        state = self.state
        if state in ("IDLE", "SCHEDULED"):
            if state == "SCHEDULED" and self.scheduled and time.time() >= self.scheduled["start_at"]:
                with self.lock:
                    self.start_scheduled()
                return
            if state == "SCHEDULED" and self.prefire_pending:
                self.prefire_step()
                return
            if self.pending_relay_test:
                secs, self.pending_relay_test = self.pending_relay_test, 0
                self.relay_test(secs)
                return
            self.output_off()
            self.idle_wait()
            return
        if self.prefire_pending and state in ("RUNNING", "TUNING"):
            self.prefire_step()
            return
        if state == "TUNING":
            with self.lock:
                if self.state != "TUNING":
                    return
                out = self.autotune_step()
            if out is not None:
                self.apply_output(out)
            return
        if state == "PAUSED":
            with self.lock:
                if self.state != "PAUSED":
                    return
                self.start_time = self.get_start_time()
                self.update_runtime()
                self.update_target_temp()
            self.heat_then_cool()
            self.reset_if_emergency()
            return
        if state == "RUNNING":
            with self.lock:
                if self.state != "RUNNING":
                    return
                self.kiln_must_catch_up()
                self.check_behind_schedule()
                self.update_runtime()
                self.update_target_temp()
            self.heat_then_cool()
            self.save_automatic_restart_state()
            self.reset_if_emergency()
            if self.state == "RUNNING":
                self.reset_if_schedule_ended()

    def idle_wait(self):
        time.sleep(1)

    def heat_then_cool(self):
        temp = self.current_temp()
        if temp is None:
            # no valid reading, fail safe
            log.error("no temperature reading, elements off this cycle")
            self.apply_output(0)
            return
        pid = self.pid.compute(self.target, temp, self.pid_now())
        heat_on = self.apply_output(pid)
        self.add_energy(heat_on)
        time_left = self.totaltime - self.runtime
        try:
            log.info("temp=%.2f, target=%.2f, error=%.2f, pid=%.2f, p=%.2f, i=%.2f, d=%.2f, heat_on=%.2f, heat_off=%.2f, run_time=%d, total_time=%d, time_left=%d" %
                     (self.pid.pidstats['ispoint'],
                      self.pid.pidstats['setpoint'],
                      self.pid.pidstats['err'],
                      self.pid.pidstats['pid'],
                      self.pid.pidstats['p'],
                      self.pid.pidstats['i'],
                      self.pid.pidstats['d'],
                      heat_on,
                      self.time_step - heat_on,
                      self.runtime,
                      self.totaltime,
                      time_left))
        except KeyError:
            pass

    def apply_output(self, fraction):
        '''run the elements for fraction of one time_step, blocks for
        time_step. returns seconds the elements were on'''
        raise NotImplementedError

    def request_relay_test(self, seconds):
        if self.state != "IDLE":
            raise RuntimeError("relay test only allowed while idle")
        seconds = float(seconds)
        if not 0 < seconds <= 10:
            raise ValueError("seconds must be between 0 and 10")
        self.pending_relay_test = seconds


class SimulatedOven(Oven):

    def __init__(self, **kw):
        self.board = SimulatedBoard()
        self.ct, self.ct_error = create_current_sensor(simulate=True)
        self.t_env = settings.sim_t_env
        self.c_heat = settings.sim_c_heat
        self.c_oven = settings.sim_c_oven
        self.p_heat = settings.sim_p_heat
        self.R_o_nocool = settings.sim_R_o_nocool
        self.R_ho_noair = settings.sim_R_ho_noair
        self.R_ho = self.R_ho_noair
        self.speedup_factor = settings.sim_speedup_factor
        self.sim_time = time.time()
        self.p_ho = 0
        self.p_env = 0
        self.Q_h = 0

        # set temps to the temp of the surrounding environment
        self.t = self.t_env  # deg C temp of oven
        self.t_h = self.t_env  # deg C temp of heating element

        super().__init__(**kw)
        self.start_time = self.get_start_time()
        log.info("SimulatedOven created")

    def clock(self):
        return self.sim_time

    def pid_now(self):
        return datetime.datetime.fromtimestamp(self.sim_time)

    # runtime is in sped up time, start_time is actual time of day
    def get_start_time(self):
        return datetime.datetime.now() - datetime.timedelta(milliseconds=self.runtime * 1000 / self.speedup_factor)

    def update_runtime(self):
        runtime_delta = datetime.datetime.now() - self.start_time
        if runtime_delta.total_seconds() < 0:
            runtime_delta = datetime.timedelta(0)
        self.runtime = runtime_delta.total_seconds() * self.speedup_factor

    def heating_energy(self, pid):
        # using pid here simulates the element being on for
        # only part of the time_step
        self.Q_h = self.p_heat * self.time_step * pid

    def temp_changes(self):
        # temperature change of heat element by heating
        self.t_h += self.Q_h / self.c_heat
        # energy flux heat_el -> oven
        self.p_ho = (self.t_h - self.t) / self.R_ho
        # temperature change of oven and heating element
        self.t += self.p_ho * self.time_step / self.c_oven
        self.t_h -= self.p_ho * self.time_step / self.c_heat
        # temperature change of oven by cooling to environment
        self.p_env = (self.t - self.t_env) / self.R_o_nocool
        self.t -= self.p_env * self.time_step / self.c_oven
        self.temperature = self.t
        self.board.temp_sensor.simulated_temperature = self.t

    def apply_output(self, fraction):
        fraction = min(max(fraction, 0.0), 1.0)
        self.output_level = fraction
        self.heating_energy(fraction)
        self.temp_changes()
        heat_on = self.time_step * fraction
        self.heat = heat_on
        if heat_on:
            self.ct_sample(True, heat_on)
        if fraction < 1:
            self.ct_sample(False, self.time_step - heat_on)
        log.debug("simulation: -> %dW heater: %.0f -> %dW oven: %.0f -> %dW env" % (
            int(self.p_heat * fraction), self.t_h, int(self.p_ho), self.t, int(self.p_env)))
        self.sim_time += self.time_step
        # we don't actually spend time heating & cooling during
        # a simulation, so sleep.
        time.sleep(self.time_step / self.speedup_factor)
        return heat_on

    def idle_wait(self):
        # let the simulated kiln cool down while idle
        self.apply_output(0)
        self.heat = 0

    def relay_test(self, seconds):
        steps = max(1, int(round(seconds / self.time_step)))
        for _ in range(steps):
            self.apply_output(1)
        self.output_off()

    def pulse_and_measure(self, seconds):
        amps = self.ct.measure(True) if self.ct else None
        if amps is not None:
            self.ct_record(amps, True)
        return amps

    def output_off(self):
        self.output_level = 0.0


class RealOven(Oven):

    def __init__(self, **kw):
        self.output = Output()
        self.board = RealBoard()
        self.ct, self.ct_error = create_current_sensor()
        # call parent init
        Oven.__init__(self, **kw)

    def reset(self):
        super().reset()
        self.output_off()

    def output_off(self):
        self.output_level = 0.0
        if hasattr(self, "output"):
            self.output.force_off()
            self.output.open_contactor()

    def beat(self):
        super().beat()
        self.output.beat()

    def apply_output(self, fraction):
        fraction = min(max(fraction, 0.0), 1.0)
        heat_on = float(self.time_step * fraction)
        heat_off = float(self.time_step * (1 - fraction))
        self.output_level = fraction
        # self.heat is seconds the elements were on this cycle
        self.heat = heat_on
        if heat_on:
            self.output.heater_on()
            self.sleep_measuring(True, heat_on)
        if heat_off:
            self.output.heater_off()
            self.sleep_measuring(False, heat_off)
        return heat_on

    def sleep_measuring(self, heater_on, seconds):
        '''hold the relay state for seconds, reading the current sensor
        meanwhile if one is fitted'''
        spent = self.ct_sample(heater_on, seconds)
        if seconds > spent:
            time.sleep(seconds - spent)

    def idle_wait(self):
        self.sleep_measuring(False, 1.0)

    def pulse_and_measure(self, seconds):
        if self.ct is None:
            return None
        self.output_level = 1.0
        try:
            self.output.heater_on()
            t0 = time.monotonic()
            if self.ct.settle:
                time.sleep(self.ct.settle)
            amps = self.ct.measure(True)
            if amps is not None:
                self.ct_record(amps, True)
            left = seconds - (time.monotonic() - t0)
            if left > 0:
                time.sleep(left)
        finally:
            self.output.heater_off()
            self.output_level = 0.0
        return amps

    def relay_test(self, seconds):
        log.info("relay test: on for %.1f seconds" % seconds)
        try:
            self.output_level = 1.0
            self.output.heater_on()
            self.ct_last_sample[True] = -1e9   # always show a reading for the test
            self.sleep_measuring(True, seconds)
        finally:
            self.output_off()


class PID():

    def __init__(self, ki=1, kp=1, kd=1):
        self.ki = ki
        self.kp = kp
        self.kd = kd
        self.lastNow = datetime.datetime.now()
        self.iterm = 0
        self.lastErr = 0
        self.lastInput = None
        self.pidstats = {}

    # The PID works on a -100..100 scale (window_size) that is divided by
    # 100 at the end, outside config.pid_control_window it is plain
    # on/off control.
    def compute(self, setpoint, ispoint, now):
        timeDelta = (now - self.lastNow).total_seconds()

        window_size = 100

        error = float(setpoint - ispoint)

        output = 0
        out4logs = 0
        dErr = 0
        dInput = 0
        if error < (-1 * settings.pid_control_window):
            log.debug("kiln outside pid control window, max cooling")
            output = 0
        elif error > (1 * settings.pid_control_window):
            log.debug("kiln outside pid control window, max heating")
            output = 1
            if settings.throttle_below_temp and settings.throttle_percent:
                if setpoint <= settings.throttle_below_temp:
                    output = settings.throttle_percent / 100
                    log.debug("max heating throttled at %d percent below %d degrees to prevent overshoot" % (settings.throttle_percent, settings.throttle_below_temp))
        else:
            if self.lastInput is not None and timeDelta > 0:
                self.iterm += (error * timeDelta * (1 / self.ki))
                # anti-windup: the integral alone can never ask for more
                # than full power
                self.iterm = sorted([-1 * window_size, self.iterm, window_size])[1]
                # derivative on measurement, avoids a kick every time the
                # schedule changes slope or the kiln enters the window
                dInput = (ispoint - self.lastInput) / timeDelta
                dErr = -dInput
            output = self.kp * error + self.iterm - self.kd * dInput
            output = sorted([-1 * window_size, output, window_size])[1]
            out4logs = output
            output = float(output / window_size)

        self.lastErr = error
        self.lastInput = ispoint
        self.lastNow = now

        # no active cooling
        if output < 0:
            output = 0

        self.pidstats = {
            'time': time.mktime(now.timetuple()),
            'timeDelta': timeDelta,
            'setpoint': setpoint,
            'ispoint': ispoint,
            'err': error,
            'errDelta': dErr,
            'p': self.kp * error,
            'i': self.iterm,
            'd': -self.kd * dInput,
            'kp': self.kp,
            'ki': self.ki,
            'kd': self.kd,
            'pid': out4logs,
            'out': output,
        }

        return output
