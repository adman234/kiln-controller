#!/usr/bin/env python3
# gevent must patch the standard library before anything else is
# imported so the oven threads, sleeps and websockets cooperate instead
# of blocking each other.
from gevent import monkey
monkey.patch_all()

import base64
import hmac
import json
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import bottle
import gevent
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from geventwebsocket import WebSocketError

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(script_dir, 'lib'))

# try/except removed here on purpose so folks can see why things break
import config

logging.basicConfig(level=config.log_level, format=config.log_format)
log = logging.getLogger("kiln-controller")
log.info("Starting kiln controller")

from settings import settings, BOARD_TC_TYPES
from units import (to_display, state_to_display, convert_profile_data,
                   from_display, delta_from_display, pidstats_to_display)
from profiles import ProfileStore, ProfileError, Profile
from history import RunHistory
from oven import SimulatedOven, RealOven
from ovenWatcher import OvenWatcher
import autotune

app = bottle.Bottle()
store = ProfileStore()
history = RunHistory()

if settings.simulate:
    log.info("this is a simulation")
    oven = SimulatedOven(store=store, history=history)
else:
    log.info("this is a real kiln")
    oven = RealOven(store=store, history=history)
ovenWatcher = OvenWatcher(oven)
# this ovenwatcher is used in the oven class for restarts
oven.set_ovenwatcher(ovenWatcher)

PUBLIC_DIR = os.path.join(script_dir, "public")


class ApiError(Exception):
    def __init__(self, msg, status=400):
        self.msg = msg
        self.status = status


# ---------------------------------------------------------------------
# security: optional password and cross-site request protection
@app.hook('before_request')
def check_request():
    req = bottle.request
    # Cross-site websocket hijacking / CSRF: a web page on another site
    # must not be able to start your kiln through your browser.
    origin = req.headers.get("Origin")
    is_ws = req.environ.get("wsgi.websocket") is not None or \
        req.headers.get("Upgrade", "").lower() == "websocket"
    if origin and (is_ws or req.method != "GET"):
        if urlparse(origin).netloc != req.headers.get("Host"):
            log.warning("rejected cross-origin request from %s" % origin)
            raise bottle.HTTPError(403, "cross-origin request rejected")

    password = settings.web_password
    if password:
        ok = False
        auth = req.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = hmac.compare_digest(given.encode(), password.encode())
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            raise bottle.HTTPError(401, "login required",
                                   **{"WWW-Authenticate": 'Basic realm="kiln-controller"'})


def json_body():
    try:
        body = bottle.request.json
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ApiError("expected a JSON object body")
    return body


def api(fn):
    '''wrap an API handler: JSON out, ApiError -> 400'''
    def wrapper(*a, **kw):
        bottle.response.content_type = "application/json"
        try:
            result = fn(*a, **kw)
            if result is None:
                result = {"success": True}
            return json.dumps(result)
        except ApiError as e:
            bottle.response.status = e.status
            return json.dumps({"success": False, "error": e.msg})
        except (ProfileError, ValueError, RuntimeError) as e:
            bottle.response.status = 400
            return json.dumps({"success": False, "error": str(e)})
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------
# pages
@app.route('/')
def index():
    return bottle.redirect('/picoreflow/index.html')


@app.route('/favicon.ico')
def favicon():
    return bottle.HTTPResponse(status=204)


@app.route('/state')
def state_page():
    return bottle.redirect('/picoreflow/state.html')


@app.route('/picoreflow/<filename:path>')
def send_static(filename):
    log.debug("serving %s" % filename)
    resp = bottle.static_file(filename, root=PUBLIC_DIR)
    # html/js change between versions, make browsers re-check them
    if filename.endswith((".html", ".js", ".css")):
        resp.set_header("Cache-Control", "no-cache")
    return resp


# ---------------------------------------------------------------------
# helpers
def display_state():
    return state_to_display(oven.get_state(), settings.temp_scale)


def profile_for_display(p, summary=None):
    scale = settings.temp_scale
    prof = Profile(p)
    est = history.estimate(prof)
    out = {
        "type": "profile",
        "name": p["name"],
        "data": [[t, round(temp, 1)] for (t, temp) in convert_profile_data(p["data"], "c", scale)],
        "temp_units": scale,
        "created": p.get("created"),
        "modified": p.get("modified"),
        "notes": p.get("notes", ""),
        "duration": prof.get_duration(),
        "peak": round(to_display(prof.peak(), scale), 1),
        "estimate": est,
    }
    if summary is not None:
        out["history"] = summary.get(p["name"])
    return out


def list_profiles_for_display():
    summary = history.summary_by_profile()
    out = []
    for p in store.list():
        try:
            out.append(profile_for_display(p, summary))
        except ProfileError as e:
            log.error("profile %s is invalid: %s" % (p.get("name"), e))
    return out


def start_run(name, startat=0):
    profile = store.get_profile(name)
    if profile is None:
        raise ApiError("profile %s not found" % name, 404)
    try:
        startat = float(startat or 0)
    except (TypeError, ValueError):
        raise ApiError("startat must be a number of minutes")
    if startat < 0:
        raise ApiError("startat must be >= 0")
    # skip the seek if a start time has been set
    oven.run_profile(profile, startat=startat, allow_seek=startat == 0)
    ovenWatcher.record(profile)


def restart_process():
    '''exit so systemd (Restart=always) starts us again with new settings'''
    log.warning("restart requested from web UI")
    oven.output_off()
    os._exit(0)


# ---------------------------------------------------------------------
# REST API
@app.get('/api/stats')
@api
def handle_api_stats():
    log.info("/api/stats command received")
    if hasattr(oven, 'pid') and oven.pid.pidstats:
        return pidstats_to_display(oven.pid.pidstats, settings.temp_scale)
    return {}


@app.get('/api/state')
@api
def handle_api_state():
    return display_state()


@app.post('/api')
@api
def handle_api():
    body = json_body()
    cmd = body.get("cmd")
    log.info("/api command %s" % cmd)

    if cmd == 'run':
        wanted = body.get('profile')
        log.info('api requested run of profile = %s' % wanted)
        start_run(wanted, body.get("startat", 0))

    elif cmd == 'schedule':
        name = body.get("profile")
        if "start_at" in body:
            start_at = float(body["start_at"])
        else:
            delay = float(body.get("delay_seconds", 0))
            if delay < 0 or delay > 14 * 24 * 3600:
                raise ApiError("delay must be between 0 and 14 days")
            start_at = time.time() + delay
        oven.schedule_profile(name, start_at, startat=float(body.get("startat", 0) or 0))
        return {"success": True, "start_at": start_at}

    elif cmd in ('cancel_schedule',):
        if oven.state == "SCHEDULED":
            oven.abort_run("schedule cancelled")

    elif cmd == 'pause':
        log.info("api pause command received")
        if not oven.pause():
            raise ApiError("nothing running to pause")

    elif cmd == 'resume':
        log.info("api resume command received")
        if not oven.resume():
            raise ApiError("not paused")

    elif cmd == 'stop':
        log.info("api stop command received")
        oven.abort_run()

    elif cmd == 'memo':
        log.info("memo=%s" % (body.get('memo'),))

    elif cmd == 'stats':
        # get stats during a run
        if oven.pid.pidstats:
            return pidstats_to_display(oven.pid.pidstats, settings.temp_scale)
        return {}

    elif cmd == 'autotune_start':
        scale = settings.temp_scale
        setpoint = from_display(float(body["setpoint"]), scale)
        hysteresis = delta_from_display(float(body.get("hysteresis", 5.4 if scale == "f" else 3)), scale)
        max_overshoot = delta_from_display(float(body.get("max_overshoot", 144 if scale == "f" else 80)), scale)
        output_high = float(body.get("output_percent", 100)) / 100.0
        cycles = int(body.get("cycles", 3))
        oven.start_autotune(setpoint, output_high=output_high, hysteresis=hysteresis,
                            cycles=cycles, max_overshoot=max_overshoot)

    elif cmd == 'autotune_apply':
        res = (oven.last_autotune or {}).get("result")
        if "kp" in body:
            gains = {k: float(body[k]) for k in ("kp", "ki", "kd")}
        elif res:
            gains = res["rules"][body.get("rule", autotune.DEFAULT_RULE)]
        else:
            raise ApiError("no autotune result to apply")
        if gains["ki"] <= 0:
            raise ApiError("ki must be > 0")
        for k in ("kp", "ki", "kd"):
            settings.set("pid_" + k, round(gains[k], 4), persist=False)
        settings.set("pid_tuned_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        # takes effect on the next run (PID is created when a run starts)
        log.info("applied PID gains %s" % gains)
        return {"success": True, "gains": gains}

    elif cmd == 'autotune_dismiss':
        oven.last_autotune = None

    elif cmd == 'relay_test':
        oven.request_relay_test(body.get("seconds", 2))

    elif cmd == 'restart':
        if oven.state in ("RUNNING", "PAUSED", "TUNING"):
            raise ApiError("stop the kiln before restarting")
        gevent.spawn_later(1.0, restart_process)
        return {"success": True, "message": "restarting"}

    else:
        raise ApiError("unknown cmd %r" % (cmd,))
    return {"success": True}


@app.get('/api/profiles')
@api
def api_profiles():
    return {"profiles": list_profiles_for_display(), "temp_scale": settings.temp_scale}


@app.post('/api/profiles')
@api
def api_profiles_save():
    body = json_body()
    profile = body.get("profile") or {}
    profile.setdefault("temp_units", settings.temp_scale)
    saved = store.save(profile, original_name=body.get("original_name"),
                       overwrite=bool(body.get("overwrite", False)))
    return {"success": True, "profile": profile_for_display(saved)}


@app.post('/api/profiles/copy')
@api
def api_profiles_copy():
    body = json_body()
    saved = store.copy(body.get("name"), body.get("new_name"))
    return {"success": True, "profile": profile_for_display(saved)}


@app.post('/api/profiles/delete')
@api
def api_profiles_delete():
    body = json_body()
    store.delete(body.get("name"))


@app.get('/api/estimate')
@api
def api_estimate():
    name = bottle.request.query.getunicode("profile")
    profile = store.get_profile(name)
    if profile is None:
        raise ApiError("profile not found", 404)
    return history.estimate(profile)


@app.get('/api/history')
@api
def api_history():
    return {"runs": history.records[::-1]}


@app.post('/api/history/clear')
@api
def api_history_clear():
    history.clear()


@app.get('/api/settings')
@api
def api_settings():
    return {"values": settings.to_display(), "schema": settings.schema(),
            "board_tc_types": BOARD_TC_TYPES,
            "autotune_rules": {k: v["label"] for k, v in autotune.RULES.items()}}


@app.post('/api/settings')
@api
def api_settings_save():
    body = json_body()
    values = body.get("values") or {}
    if "simulate" in values and oven.state in ("RUNNING", "PAUSED", "TUNING"):
        raise ApiError("stop the kiln before changing simulation mode")
    changed, restart, errors = settings.update_from_display(values)
    # PID gains are picked up when the next run starts; while a run is
    # going update them live so tuning changes can be watched
    if any(k.startswith("pid_k") for k in changed):
        oven.pid.kp, oven.pid.ki, oven.pid.kd = settings.pid_kp, settings.pid_ki, settings.pid_kd
    return {"success": not errors, "changed": changed, "restart_required": restart,
            "errors": errors, "values": settings.to_display()}


def read_first_line(path):
    try:
        with open(path) as f:
            return f.readline().strip()
    except OSError:
        return None


def system_info():
    info = {"hostname": socket.gethostname(), "python": platform.python_version(),
            "platform": platform.platform(), "board": getattr(oven.board, "name", None),
            "time": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    t = read_first_line("/sys/class/thermal/thermal_zone0/temp")
    if t:
        info["cpu_temp_c"] = int(t) / 1000.0
    try:
        info["load"] = os.getloadavg()
    except OSError:
        pass
    up = read_first_line("/proc/uptime")
    if up:
        info["uptime_s"] = float(up.split()[0])
    try:
        with open("/proc/meminfo") as f:
            mem = dict(line.split(":", 1) for line in f)
        info["mem_available_mb"] = int(mem["MemAvailable"].split()[0]) // 1024
        info["mem_total_mb"] = int(mem["MemTotal"].split()[0]) // 1024
    except (OSError, KeyError, ValueError):
        pass
    try:
        du = shutil.disk_usage(script_dir)
        info["disk_free_mb"] = du.free // (1024 * 1024)
    except OSError:
        pass
    # under-voltage is the #1 cause of flaky pi zeros
    if shutil.which("vcgencmd"):
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
            val = int(out.strip().split("=")[1], 16)
            info["throttled"] = hex(val)
            info["undervoltage_now"] = bool(val & 0x1)
            info["undervoltage_since_boot"] = bool(val & 0x10000)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
    return info


@app.get('/api/diagnostics')
@api
def api_diagnostics():
    scale = settings.temp_scale
    d = oven.board.temp_sensor.diagnostics()
    for k in ("temperature_c", "last_raw_c", "cold_junction_c"):
        d[k[:-2]] = to_display(d.get(k), scale) if d.get(k) is not None else None
    return {"sensor": d, "system": system_info(), "state": oven.state, "temp_scale": scale,
            "settings": {"sensor_board": settings.sensor_board, "thermocouple_type": settings.thermocouple_type,
                         "spi_mode": settings.spi_mode, "gpio_heat": settings.gpio_heat,
                         "simulate": settings.simulate}}


# ---------------------------------------------------------------------
# websockets (kept for the UI's live status and older scripts)
def get_websocket_from_request():
    env = bottle.request.environ
    wsock = env.get('wsgi.websocket')
    if not wsock:
        bottle.abort(400, 'Expected WebSocket request.')
    return wsock


@app.route('/control')
def handle_control():
    wsock = get_websocket_from_request()
    log.info("websocket (control) opened")
    while True:
        try:
            message = wsock.receive()
            if message is None:
                break
            log.info("Received (control): %s" % message)
            try:
                msgdict = json.loads(message)
            except ValueError:
                continue
            if msgdict.get("cmd") == "RUN":
                log.info("RUN command received")
                profile_obj = msgdict.get('profile') or {}
                name = profile_obj.get("name") if isinstance(profile_obj, dict) else profile_obj
                try:
                    start_run(name, msgdict.get("startat", 0))
                except (ApiError, ProfileError, RuntimeError) as e:
                    wsock.send(json.dumps({"error": getattr(e, "msg", str(e))}))
            elif msgdict.get("cmd") == "STOP":
                log.info("Stop command received")
                oven.abort_run()
        except WebSocketError as e:
            log.error(e)
            break
    log.info("websocket (control) closed")


@app.route('/storage')
def handle_storage():
    '''legacy profile storage socket, the UI now uses /api/profiles'''
    wsock = get_websocket_from_request()
    log.info("websocket (storage) opened")
    while True:
        try:
            message = wsock.receive()
            if not message:
                break
            try:
                msgdict = json.loads(message)
            except ValueError:
                msgdict = {}
            if message == "GET":
                wsock.send(json.dumps(list_profiles_for_display()))
            elif msgdict.get("cmd") == "DELETE":
                try:
                    store.delete(msgdict.get('profile', {}).get("name"))
                    msgdict["resp"] = "OK"
                except ProfileError as e:
                    msgdict["resp"] = "FAIL"
                    msgdict["error"] = str(e)
                wsock.send(json.dumps(msgdict))
            elif msgdict.get("cmd") == "PUT":
                profile_obj = msgdict.get('profile') or {}
                profile_obj.setdefault("temp_units", settings.temp_scale)
                try:
                    store.save(profile_obj)
                    msgdict["resp"] = "OK"
                except ProfileError as e:
                    msgdict["resp"] = "FAIL"
                    msgdict["error"] = str(e)
                wsock.send(json.dumps(msgdict))
                wsock.send(json.dumps(list_profiles_for_display()))
        except WebSocketError:
            break
    log.info("websocket (storage) closed")


def get_config():
    return json.dumps({"temp_scale": settings.temp_scale,
                       "time_scale_slope": settings.time_scale_slope,
                       "time_scale_profile": settings.time_scale_profile,
                       "kwh_rate": settings.kwh_rate,
                       "kw_elements": settings.kw_elements,
                       "currency_type": settings.currency_type})


@app.route('/config')
def handle_config():
    wsock = get_websocket_from_request()
    log.info("websocket (config) opened")
    while True:
        try:
            message = wsock.receive()
            if message is None:
                break
            wsock.send(get_config())
        except WebSocketError:
            break
    log.info("websocket (config) closed")


@app.route('/status')
def handle_status():
    wsock = get_websocket_from_request()
    ovenWatcher.add_observer(wsock)
    log.info("websocket (status) opened")
    while True:
        try:
            message = wsock.receive()
            if message is None:
                break
        except WebSocketError:
            break
    ovenWatcher.remove_observer(wsock)
    log.info("websocket (status) closed")


def shutdown(*_):
    '''make sure the elements are off whenever we exit'''
    log.warning("shutting down, turning elements off")
    try:
        oven.output_off()
    finally:
        os._exit(0)


def main():
    ip = "0.0.0.0"
    port = config.listening_port
    oven.start()
    ovenWatcher.start()
    gevent.signal_handler(signal.SIGTERM, shutdown)
    gevent.signal_handler(signal.SIGINT, shutdown)
    log.info("listening on %s:%d" % (ip, port))
    server = WSGIServer((ip, port), app, handler_class=WebSocketHandler, log=None)
    server.serve_forever()


if __name__ == "__main__":
    main()
