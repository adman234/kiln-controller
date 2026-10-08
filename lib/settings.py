'''Runtime settings that can be changed from the web UI.

config.py holds the defaults (written in config.temp_scale units, for
backwards compatibility). Anything changed in the UI is stored in
storage/settings.json and overrides config.py.

Internally every temperature is degrees Celsius and the PID gains are
"per degree C". Conversion to the display scale happens in to_display()
and update_from_display().
'''
import json
import logging
import os
import threading

import config
from units import f_to_c, to_display, from_display, delta_to_display, delta_from_display

log = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
SETTINGS_FILE = os.path.join(STORAGE_DIR, "settings.json")

SENSOR_BOARDS = [
    ("max31855", "MAX31855 (K-type thermocouple, SPI)"),
    ("max31856", "MAX31856 (any thermocouple type, SPI)"),
    ("max6675", "MAX6675 (K-type thermocouple, SPI, legacy)"),
    ("mcp9600", "MCP9600 / MCP9601 (most thermocouple types, I2C)"),
    ("max31865", "MAX31865 (PT100/PT1000 RTD, SPI - max ~850C!)"),
]
TC_TYPES = ["B", "E", "J", "K", "N", "R", "S", "T"]
# which thermocouple types each board can linearize
BOARD_TC_TYPES = {
    "max31855": ["K"],
    "max31856": TC_TYPES,
    "max6675": ["K"],
    "mcp9600": ["B", "E", "J", "K", "N", "R", "S", "T"],
    "max31865": [],
}

# kind: how a value is stored/converted
#   temp  - absolute temperature (stored C)
#   delta - temperature difference (stored C)
#   bool, int, float, str, choice, password
SCHEMA = [
    # group, key, kind, label, extra
    ("Display", "temp_scale", "choice", "Temperature units",
        {"options": [["c", "Celsius"], ["f", "Fahrenheit"]]}),
    ("Display", "time_scale_profile", "choice", "Default editor time units",
        {"options": [["m", "Minutes"], ["h", "Hours"]]}),
    ("Display", "time_scale_slope", "choice", "Show heating rate per",
        {"options": [["h", "Hour"], ["m", "Minute"]]}),

    ("Cost", "kw_elements", "float", "Element power (kW)",
        {"help": "Total power of your elements when on. Volts x Amps / 1000, or check the kiln nameplate.", "min": 0.1, "max": 50}),
    ("Cost", "kwh_rate", "float", "Electricity price per kWh", {"min": 0, "max": 10}),
    ("Cost", "currency_type", "str", "Currency symbol", {}),

    ("Firing", "emergency_shutoff_temp", "temp", "Emergency shutoff temperature",
        {"help": "Abort any run if this temperature is reached."}),
    ("Firing", "seek_start", "bool", "Skip ahead if kiln is already hot", {}),
    ("Firing", "kiln_must_catch_up", "bool", "Pause schedule while kiln catches up", {}),
    ("Firing", "pid_control_window", "delta", "PID control window",
        {"help": "Outside this many degrees from target the elements are fully on or off.", "min": 0.5, "max": 200}),
    ("Firing", "throttle_below_temp", "temp", "Throttle elements below", {}),
    ("Firing", "throttle_percent", "int", "Throttle to percent", {"min": 1, "max": 100}),
    ("Firing", "automatic_restarts", "bool", "Resume run after power failure", {}),
    ("Firing", "automatic_restart_window", "int", "Max outage to resume (minutes)", {"min": 1, "max": 600}),

    ("PID", "pid_kp", "float", "Kp (proportional)", {"help": "Per degree C"}),
    ("PID", "pid_ki", "float", "Ki (integral time - bigger is less)", {"help": "Inverted: integral term = error x seconds / Ki", "min": 0.001}),
    ("PID", "pid_kd", "float", "Kd (derivative)", {"help": "Per degree C per second"}),

    ("Sensor", "sensor_board", "choice", "Sensor board",
        {"options": [list(b) for b in SENSOR_BOARDS], "restart": True}),
    ("Sensor", "thermocouple_type", "choice", "Thermocouple type",
        {"options": [[t, "Type %s" % t] for t in TC_TYPES], "restart": True,
         "help": "MAX31855 and MAX6675 chips are K-type only."}),
    ("Sensor", "thermocouple_offset", "delta", "Temperature offset",
        {"help": "Added to every reading. If it reads 2 degrees high in ice water, enter -2.", "min": -100, "max": 100}),
    ("Sensor", "ac_freq_50hz", "bool", "50Hz mains (noise filter)", {"restart": True}),
    ("Sensor", "temperature_average_samples", "int", "Samples per cycle (median)", {"min": 1, "max": 40, "restart": True}),
    ("Sensor", "mcp9600_address", "str", "MCP9600 I2C address", {"restart": True}),
    ("Sensor", "rtd_nominal", "float", "RTD nominal ohms (100 or 1000)", {"restart": True}),
    ("Sensor", "rtd_ref_resistor", "float", "RTD reference resistor ohms", {"restart": True}),
    ("Sensor", "rtd_wires", "choice", "RTD wires",
        {"options": [[2, "2"], [3, "3"], [4, "4"]], "restart": True}),

    ("Hardware", "spi_mode", "choice", "SPI mode",
        {"options": [["software", "Software (any pins)"], ["hardware", "Hardware (SPI0: CLK=11, MISO=9, MOSI=10)"]], "restart": True}),
    ("Hardware", "spi_sclk", "int", "SPI clock pin (BCM)", {"min": 0, "max": 27, "restart": True}),
    ("Hardware", "spi_miso", "int", "SPI MISO / DO pin (BCM)", {"min": 0, "max": 27, "restart": True}),
    ("Hardware", "spi_mosi", "int", "SPI MOSI pin (BCM)", {"min": 0, "max": 27, "restart": True}),
    ("Hardware", "spi_cs", "int", "SPI chip select pin (BCM)", {"min": 0, "max": 27, "restart": True}),
    ("Hardware", "gpio_heat", "int", "Relay output pin (BCM)", {"min": 0, "max": 27, "restart": True}),
    ("Hardware", "gpio_heat_invert", "bool", "Invert relay output", {"restart": True}),
    ("Hardware", "sensor_time_wait", "float", "Control cycle (seconds)",
        {"min": 1, "max": 30, "restart": True, "help": "Use 10-30s for mechanical relays/contactors."}),

    ("Safety", "runaway_detect", "bool", "Detect a relay stuck on",
        {"help": "Alarm and open the safety contactor if the kiln keeps heating while the elements are off."}),
    ("Safety", "runaway_minutes", "int", "Stuck relay: watch window (minutes)", {"min": 3, "max": 60}),
    ("Safety", "runaway_rise", "delta", "Stuck relay: rise that triggers the alarm", {"min": 2, "max": 200}),
    ("Safety", "stall_detect", "bool", "Detect no heating at full power",
        {"help": "Stop the firing if the kiln does not warm up with the elements fully on: thermocouple out of the kiln, failed element or relay, or the kiln at its limit."}),
    ("Safety", "stall_minutes", "int", "No heating: watch window (minutes)", {"min": 5, "max": 240}),
    ("Safety", "stall_rise", "delta", "No heating: minimum rise expected", {"min": 0.5, "max": 100}),
    ("Safety", "behind_schedule_minutes", "int", "Alert when behind schedule for (minutes)", {"min": 5, "max": 600}),
    ("Safety", "gpio_contactor", "int", "Safety contactor output pin (BCM, -1 = none)",
        {"min": -1, "max": 27, "restart": True,
         "help": "Drives a contactor in series with the SSR. Closed only while firing, opened on any emergency. See docs/safety.md."}),
    ("Safety", "gpio_contactor_invert", "bool", "Invert contactor output", {"restart": True}),
    ("Safety", "gpio_heartbeat", "int", "Heartbeat output pin for external watchdog (BCM, -1 = none)",
        {"min": -1, "max": 27, "restart": True,
         "help": "Toggles every control cycle. An external watchdog relay can drop power if it stops."}),

    ("Notifications", "notify_service", "choice", "Send alerts with",
        {"options": [["none", "Nothing"], ["ntfy", "ntfy (free phone app)"], ["pushover", "Pushover"],
                     ["webhook", "Webhook (Slack, Discord, Home Assistant...)"]]}),
    ("Notifications", "notify_url", "str", "ntfy topic URL or webhook URL",
        {"maxlen": 300, "help": "ntfy: https://ntfy.sh/<a long random topic name>, subscribe to the same topic in the ntfy app."}),
    ("Notifications", "pushover_user", "str", "Pushover user key", {"maxlen": 64}),
    ("Notifications", "pushover_token", "password", "Pushover app token", {}),
    ("Notifications", "notify_on_complete", "bool", "Also notify when a firing finishes or starts", {}),

    ("Errors", "ignore_temp_too_high", "bool", "Ignore emergency temperature", {}),
    ("Errors", "ignore_tc_lost_connection", "bool", "Ignore: thermocouple not connected", {}),
    ("Errors", "ignore_tc_short_errors", "bool", "Ignore: short circuit", {}),
    ("Errors", "ignore_tc_unknown_error", "bool", "Ignore: unknown error", {}),
    ("Errors", "ignore_tc_cold_junction_range_error", "bool", "Ignore: cold junction range", {}),
    ("Errors", "ignore_tc_range_error", "bool", "Ignore: thermocouple range", {}),
    ("Errors", "ignore_tc_cold_junction_temp_high", "bool", "Ignore: cold junction too high", {}),
    ("Errors", "ignore_tc_cold_junction_temp_low", "bool", "Ignore: cold junction too low", {}),
    ("Errors", "ignore_tc_temp_high", "bool", "Ignore: thermocouple too high", {}),
    ("Errors", "ignore_tc_temp_low", "bool", "Ignore: thermocouple too low", {}),
    ("Errors", "ignore_tc_voltage_error", "bool", "Ignore: voltage error", {}),
    ("Errors", "ignore_tc_too_many_errors", "bool", "Ignore: too many errors (dangerous)", {}),

    ("System", "simulate", "bool", "Simulation mode (no hardware)", {"restart": True}),
    ("System", "sim_speedup_factor", "int", "Simulation speed-up", {"min": 1, "max": 1000, "restart": True}),
    ("System", "web_password", "password", "Web UI password",
        {"help": "Leave blank for no password. Any username works. Forgot it? Delete web_password from storage/settings.json."}),
]

SCHEMA_BY_KEY = {s[1]: s for s in SCHEMA}

# values that are not shown in the UI but still live in settings
HIDDEN_DEFAULTS = {
    "sim_t_env": 18.0,
    "sim_c_heat": 500.0,
    "sim_c_oven": 5000.0,
    "sim_p_heat": 5450.0,
    "sim_R_o_nocool": 0.5,
    "sim_R_ho_noair": 0.1,
    "pid_tuned_at": None,
}

TC_TYPE_BY_INT = {0: "B", 1: "E", 2: "J", 3: "K", 4: "N", 5: "R", 6: "S", 7: "T"}


def _pin_number(value, default):
    '''config.py used to hold blinka pin objects (board.D17), now ints'''
    if value is None:
        return default
    if isinstance(value, int):
        return value
    pin_id = getattr(value, "id", None)
    if isinstance(pin_id, int):
        return pin_id
    try:
        return int(str(value).lstrip("D"))
    except ValueError:
        return default


def defaults_from_config(cfg=config):
    '''read config.py and convert everything to internal (C) units'''
    scale = str(getattr(cfg, "temp_scale", "f")).lower()

    def temp(name, default_c):
        if not hasattr(cfg, name):
            return default_c
        v = float(getattr(cfg, name))
        return f_to_c(v) if scale == "f" else v

    def delta(name, default_c):
        if not hasattr(cfg, name):
            return default_c
        v = float(getattr(cfg, name))
        return v * 5.0 / 9.0 if scale == "f" else v

    g = lambda name, default: getattr(cfg, name, default)

    # PID gains: error was measured in F. kp,kd scale with the error,
    # ki is inverted (iterm += err*dt/ki)
    kp, ki, kd = float(g("pid_kp", 10)), float(g("pid_ki", 80)), float(g("pid_kd", 200))
    if scale == "f":
        kp, ki, kd = kp * 1.8, ki / 1.8, kd * 1.8

    if hasattr(cfg, "sensor_board"):
        board = cfg.sensor_board
    elif getattr(cfg, "max31856", 0):
        board = "max31856"
    else:
        board = "max31855"

    tc = g("thermocouple_type", "K")
    if isinstance(tc, int):
        tc = TC_TYPE_BY_INT.get(tc, "K")
    tc = str(tc).upper()

    pins_given = all(hasattr(cfg, p) for p in ("spi_sclk", "spi_miso", "spi_mosi"))
    spi_mode = g("spi_mode", "software" if pins_given else "hardware")

    d = {
        "temp_scale": scale if scale in ("c", "f") else "f",
        "time_scale_profile": g("time_scale_profile", "m") if g("time_scale_profile", "m") in ("m", "h") else "m",
        "time_scale_slope": g("time_scale_slope", "h") if g("time_scale_slope", "h") in ("m", "h") else "h",
        "kw_elements": float(g("kw_elements", 9.46)),
        "kwh_rate": float(g("kwh_rate", 0.15)),
        "currency_type": str(g("currency_type", "$")),

        "emergency_shutoff_temp": temp("emergency_shutoff_temp", 1260.0),
        "seek_start": bool(g("seek_start", True)),
        "kiln_must_catch_up": bool(g("kiln_must_catch_up", True)),
        "pid_control_window": delta("pid_control_window", 3.0),
        "throttle_below_temp": temp("throttle_below_temp", 150.0),
        "throttle_percent": int(g("throttle_percent", 20)),
        "automatic_restarts": bool(g("automatic_restarts", True)),
        "automatic_restart_window": int(g("automatic_restart_window", 15)),

        "pid_kp": kp, "pid_ki": ki, "pid_kd": kd,

        "sensor_board": board,
        "thermocouple_type": tc if tc in TC_TYPES else "K",
        "thermocouple_offset": delta("thermocouple_offset", 0.0),
        "ac_freq_50hz": bool(g("ac_freq_50hz", False)),
        "temperature_average_samples": int(g("temperature_average_samples", 10)),
        "mcp9600_address": str(g("mcp9600_address", "0x67")),
        "rtd_nominal": float(g("rtd_nominal", 100.0)),
        "rtd_ref_resistor": float(g("rtd_ref_resistor", 430.0)),
        "rtd_wires": int(g("rtd_wires", 2)),

        "spi_mode": spi_mode if spi_mode in ("software", "hardware") else "software",
        "spi_sclk": _pin_number(g("spi_sclk", None), 17),
        "spi_miso": _pin_number(g("spi_miso", None), 27),
        "spi_mosi": _pin_number(g("spi_mosi", None), 10),
        "spi_cs": _pin_number(g("spi_cs", None), 22),
        "gpio_heat": _pin_number(g("gpio_heat", None), 23),
        "gpio_heat_invert": bool(g("gpio_heat_invert", False)),
        "sensor_time_wait": float(g("sensor_time_wait", 2)),

        "simulate": bool(g("simulate", True)),
        "sim_speedup_factor": int(g("sim_speedup_factor", 1)),
        "web_password": str(g("web_password", "")),

        "runaway_detect": bool(g("runaway_detect", True)),
        "runaway_minutes": int(g("runaway_minutes", 10)),
        "runaway_rise": delta("runaway_rise", 15.0),
        "stall_detect": bool(g("stall_detect", True)),
        "stall_minutes": int(g("stall_minutes", 30)),
        "stall_rise": delta("stall_rise", 5.0),
        "behind_schedule_minutes": int(g("behind_schedule_minutes", 60)),
        "gpio_contactor": _pin_number(g("gpio_contactor", None), -1),
        "gpio_contactor_invert": bool(g("gpio_contactor_invert", False)),
        "gpio_heartbeat": _pin_number(g("gpio_heartbeat", None), -1),

        "notify_service": str(g("notify_service", "none")),
        "notify_url": str(g("notify_url", "")),
        "pushover_user": str(g("pushover_user", "")),
        "pushover_token": str(g("pushover_token", "")),
        "notify_on_complete": bool(g("notify_on_complete", True)),
    }
    for (_, key, kind, _, _) in SCHEMA:
        if key.startswith("ignore_"):
            d[key] = bool(g(key, False))
    for k, v in HIDDEN_DEFAULTS.items():
        d[k] = v
    if hasattr(cfg, "sim_t_env"):
        d["sim_t_env"] = temp("sim_t_env", 18.0)
    for k in ("sim_c_heat", "sim_c_oven", "sim_p_heat", "sim_R_o_nocool", "sim_R_ho_noair"):
        if hasattr(cfg, k):
            d[k] = float(getattr(cfg, k))
    return d


class Settings(object):
    def __init__(self, path=SETTINGS_FILE, cfg=config):
        self._path = path
        self._lock = threading.Lock()
        self._defaults = defaults_from_config(cfg)
        self._values = dict(self._defaults)
        self._overrides = {}
        self.load()

    # attribute style access: settings.pid_kp
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        values = self.__dict__.get("_values", {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def get(self, key, default=None):
        return self._values.get(key, default)

    def load(self):
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            log.error("could not read %s, using config.py defaults: %s" % (self._path, e))
            return
        for k, v in data.items():
            if k in self._defaults:
                self._overrides[k] = v
                self._values[k] = v
        log.info("loaded %d settings overrides from %s" % (len(self._overrides), self._path))

    def save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        atomic_write_json(self._path, self._overrides)

    def set(self, key, value, persist=True):
        with self._lock:
            self._values[key] = value
            self._overrides[key] = value
            if persist:
                self.save()

    def to_display(self):
        '''values for the settings UI in the current display scale'''
        scale = self.temp_scale
        out = {}
        for (_, key, kind, _, _) in SCHEMA:
            v = self._values.get(key)
            if kind == "temp":
                v = round(to_display(v, scale), 1)
            elif kind == "delta":
                v = round(delta_to_display(v, scale), 2)
            elif kind == "password":
                out[key + "_set"] = bool(v)
                v = ""
            out[key] = v
        out["pid_tuned_at"] = self._values.get("pid_tuned_at")
        return out

    def schema(self):
        out = []
        for (group, key, kind, label, extra) in SCHEMA:
            item = {"group": group, "key": key, "kind": kind, "label": label}
            item.update(extra)
            out.append(item)
        return out

    def update_from_display(self, values):
        '''validate and apply values coming from the UI. Temperatures are
        in the display scale that was active when the form was loaded.
        returns (changed_keys, restart_needed, errors)'''
        scale = self.temp_scale
        errors = {}
        new = {}
        for key, raw in values.items():
            if key not in SCHEMA_BY_KEY:
                continue
            (_, _, kind, label, extra) = SCHEMA_BY_KEY[key]
            try:
                v = coerce(kind, raw, extra)
            except (TypeError, ValueError) as e:
                errors[key] = "%s: %s" % (label, e)
                continue
            if kind == "password" and v is None:
                continue  # unchanged
            if kind == "temp":
                v = from_display(v, scale)
            elif kind == "delta":
                v = delta_from_display(v, scale)
            # range checks in display units were done in coerce
            new[key] = v

        if "pid_ki" in new and new["pid_ki"] <= 0:
            errors["pid_ki"] = "Ki must be greater than 0"
            del new["pid_ki"]

        changed = []
        restart = []
        with self._lock:
            for key, v in new.items():
                if self._values.get(key) != v:
                    changed.append(key)
                    if SCHEMA_BY_KEY[key][4].get("restart"):
                        restart.append(key)
                self._values[key] = v
                self._overrides[key] = v
            if changed:
                self.save()
        return changed, restart, errors


SECRET_KEYS = [s[1] for s in SCHEMA if s[2] == "password"]


def coerce(kind, raw, extra):
    if kind == "bool":
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if kind == "password":
        if raw is None or raw == "__unchanged__":
            return None
        return str(raw)
    if kind == "str":
        v = str(raw).strip()
        if len(v) > extra.get("maxlen", 32):
            raise ValueError("too long")
        return v
    if kind == "choice":
        options = [o[0] for o in extra["options"]]
        for o in options:
            if str(o) == str(raw):
                return o
        raise ValueError("must be one of %s" % options)
    if kind == "int":
        v = int(float(raw))
    else:  # float, temp, delta
        v = float(raw)
        if v != v:  # NaN
            raise ValueError("not a number")
    if "min" in extra and v < extra["min"]:
        raise ValueError("must be >= %s" % extra["min"])
    if "max" in extra and v > extra["max"]:
        raise ValueError("must be <= %s" % extra["max"])
    return v


def atomic_write_json(path, data, **kw):
    '''write to a temp file and rename so a power cut never leaves a
    half-written (corrupt) file behind'''
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, **kw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


settings = Settings()
