#!/usr/bin/env python3
# gevent must patch the standard library before anything else is
# imported so the oven threads, sleeps and live-status streams cooperate instead
# of blocking each other.
from gevent import monkey
monkey.patch_all()

import base64
import hmac
import ipaddress
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import bottle
import gevent
from gevent.pywsgi import WSGIServer
from gevent.queue import Queue, Empty

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(script_dir, 'lib'))

# try/except removed here on purpose so folks can see why things break
import config

logging.basicConfig(level=config.log_level, format=config.log_format)
log = logging.getLogger("kiln-controller")
log.info("Starting kiln controller")

from settings import settings, BOARD_TC_TYPES, SECRET_KEYS, HARDWARE_KEYS, atomic_write_json
from units import (to_display, state_to_display, convert_profile_data,
                   from_display, delta_from_display, pidstats_to_display)
from profiles import ProfileStore, ProfileError, Profile
from history import RunHistory
from oven import SimulatedOven, RealOven
from ovenWatcher import OvenWatcher
from notify import notifier
from updater import Updater, UpdateError
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
    # CSRF: a web page on another site must not be able to start your
    # kiln through your browser.
    origin = req.headers.get("Origin")
    if origin and req.method != "GET":
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
    return bottle.static_file("icon-192.png", root=os.path.join(PUBLIC_DIR, "assets", "images"))


@app.route('/state')
def state_page():
    return bottle.redirect('/picoreflow/state.html')


@app.route('/picoreflow/<filename:path>')
def send_static(filename):
    log.debug("serving %s" % filename)
    mimetype = "application/manifest+json" if filename.endswith(".webmanifest") else True
    resp = bottle.static_file(filename, root=PUBLIC_DIR, mimetype=mimetype)
    # html/js change between versions, make browsers re-check them
    if filename.endswith((".html", ".js", ".css")):
        resp.set_header("Cache-Control", "no-cache")
    return resp


# ---------------------------------------------------------------------
# helpers
def display_state():
    return state_to_display(oven.get_state(), settings.temp_scale)


def request_is_remote():
    '''True when the browser is not on a private/local network, i.e. the
    controller has been exposed to the internet'''
    addr = bottle.request.environ.get("REMOTE_ADDR", "")
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    # 100.64.0.0/10 is carrier-grade NAT, which Tailscale uses
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or
                ip in ipaddress.ip_network("100.64.0.0/10"))


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


updater = Updater(restart=lambda: gevent.spawn_later(3.0, restart_process))


def refuse_while_updating():
    if updater.busy and updater.status == "updating":
        raise ApiError("a software update is being installed, wait for it to finish")


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

    if cmd in ('run', 'schedule', 'autotune_start', 'relay_test'):
        refuse_while_updating()

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

    elif cmd == 'notify_test':
        notifier.test()
        return {"success": True, "message": "sent"}

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
        # with real PID values the controller can work in a wider band
        # around the target instead of plain on/off (default 5F/2.8C)
        if body.get("widen_window", True) and settings.pid_control_window < 10:
            settings.set("pid_control_window", 10.0, persist=False)
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


@app.get('/api/profiles/export')
def api_profiles_export():
    '''one schedule (or all with no name) as a JSON download, in Celsius
    so it can be shared'''
    name = bottle.request.query.getunicode("name")
    clean = lambda p: {k: v for k, v in p.items() if not k.startswith("_")}
    if name:
        p = store.find(name)
        if p is None:
            return bottle.HTTPError(404, "not found")
        data, fname = clean(p), name
    else:
        data, fname = [clean(p) for p in store.list()], "kiln-schedules"
    bottle.response.content_type = "application/json"
    bottle.response.set_header("Content-Disposition", 'attachment; filename="%s.json"' %
                               "".join(c if c.isalnum() or c in "-_ " else "_" for c in fname))
    return json.dumps(data, indent=1)


@app.post('/api/profiles/import')
@api
def api_profiles_import():
    '''body: {"profiles": [...] or one profile, "overwrite": bool}'''
    body = json_body()
    items = body.get("profiles")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        raise ApiError("no schedules in the file")
    imported, skipped = [], []
    for p in items[:500]:
        if not isinstance(p, dict):
            continue
        p = dict(p)
        p.setdefault("temp_units", "c")
        if store.find(p.get("name")) and not body.get("overwrite"):
            skipped.append(p.get("name"))
            continue
        store.save(p, overwrite=True)
        imported.append(p["name"])
    return {"success": True, "imported": imported, "skipped": skipped}


@app.get('/api/backup')
def api_backup():
    '''settings, schedules and firing history in one file. Passwords and
    tokens are left out.'''
    overrides = {k: v for k, v in settings._overrides.items() if k not in SECRET_KEYS}
    data = {
        "type": "kiln-controller-backup",
        "version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hostname": socket.gethostname(),
        "settings": overrides,
        "profiles": [{k: v for k, v in p.items() if not k.startswith("_")} for p in store.list()],
        "history": history.records,
    }
    bottle.response.content_type = "application/json"
    bottle.response.set_header("Content-Disposition", 'attachment; filename="kiln-backup-%s-%s.json"' %
                               (socket.gethostname(), time.strftime("%Y%m%d")))
    return json.dumps(data, indent=1)


@app.post('/api/restore')
@api
def api_restore():
    body = json_body()
    if body.get("type") != "kiln-controller-backup":
        raise ApiError("this is not a kiln-controller backup file")
    if oven.state in ("RUNNING", "PAUSED", "TUNING"):
        raise ApiError("stop the kiln before restoring")
    restored = {"settings": 0, "profiles": 0, "history": 0}
    vals = body.get("settings") or {}
    if isinstance(vals, dict):
        for k, v in vals.items():
            if k in settings._defaults and k not in SECRET_KEYS:
                settings.set(k, v, persist=False)
                restored["settings"] += 1
        settings.save()
    for p in body.get("profiles") or []:
        try:
            store.save(dict(p, temp_units=p.get("temp_units", "c")), overwrite=True)
            restored["profiles"] += 1
        except (ProfileError, TypeError, AttributeError) as e:
            log.error("restore: skipped a schedule: %s" % e)
    runs = body.get("history")
    if isinstance(runs, list):
        with history.lock:
            history.records = [r for r in runs if isinstance(r, dict)][-100:]
        restored["history"] = len(history.records)
        atomic_write_json(history.path, history.records, indent=1)
    return {"success": True, "restored": restored,
            "message": "Restored. Restart the controller if sensor or pin settings changed."}


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
            "remote": request_is_remote(),
            "notifications": notifier.recent[-10:],
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


@app.get('/api/update')
@api
def api_update_info():
    info = updater.info()
    info["repo_url"] = settings.update_repo_url
    info["branch"] = settings.update_branch
    return info


@app.post('/api/update')
@api
def api_update():
    '''body: {"action": "check" | "install" | "rollback", "repo_url", "branch", "force"}'''
    body = json_body()
    action = body.get("action")
    # installing code is as powerful as SSH access
    if request_is_remote() and not settings.web_password:
        raise ApiError("set a web UI password before updating over the internet", 403)
    repo_url = (body.get("repo_url") or settings.update_repo_url or "").strip()
    branch = (body.get("branch") or settings.update_branch or "main").strip()
    try:
        if action == "check":
            updater.check(repo_url, branch)
        elif action in ("install", "rollback"):
            if oven.state != "IDLE":
                raise ApiError("stop the kiln (and cancel any delayed start) before updating")
            if action == "install":
                updater.install(repo_url, branch, force=bool(body.get("force")))
                if (repo_url, branch) != (settings.update_repo_url, settings.update_branch):
                    settings.update_from_display({"update_repo_url": repo_url, "update_branch": branch})
            else:
                updater.rollback()
        else:
            raise ApiError("unknown action %r" % (action,))
    except UpdateError as e:
        raise ApiError(str(e))
    return {"success": True, "status": updater.status}


@app.post('/api/settings/reset')
@api
def api_settings_reset():
    '''body: {"keep_hardware": true}. Back to the defaults that came with
    this version of the software.'''
    body = json_body()
    if oven.state != "IDLE":
        raise ApiError("stop the kiln (and cancel any delayed start) before resetting settings")
    keep = HARDWARE_KEYS if body.get("keep_hardware", True) else ()
    dropped = settings.reset_to_defaults(keep=keep)
    return {"success": True, "reset": dropped, "restart_required": True, "values": settings.to_display()}


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
            "current": oven.ct_status(),
            "settings": {"sensor_board": settings.sensor_board, "thermocouple_type": settings.thermocouple_type,
                         "spi_mode": settings.spi_mode, "gpio_heat": settings.gpio_heat,
                         "simulate": settings.simulate}}


# ---------------------------------------------------------------------
# live status: Server-Sent Events. One way (server -> browser) is all the
# UI needs, it works through any proxy and needs no extra library.
@app.get('/api/events')
def api_events():
    q = Queue(maxsize=50)
    ovenWatcher.add_listener(q)
    bottle.response.content_type = "text/event-stream"
    bottle.response.set_header("Cache-Control", "no-cache")
    bottle.response.set_header("X-Accel-Buffering", "no")
    log.info("status stream opened (%d listeners)" % len(ovenWatcher.listeners))

    def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                if msg is None:
                    break
                yield "data: %s\n\n" % msg
        finally:
            ovenWatcher.remove_listener(q)
            log.info("status stream closed")
    return stream()


def get_config():
    return {"temp_scale": settings.temp_scale,
            "time_scale_slope": settings.time_scale_slope,
            "time_scale_profile": settings.time_scale_profile,
            "kwh_rate": settings.kwh_rate,
            "kw_elements": settings.kw_elements,
            "currency_type": settings.currency_type}


@app.get('/api/config')
@api
def api_config():
    return get_config()


def systemd_compat():
    '''Installs made before the safety features were removed have a service
    unit with Type=notify and WatchdogSec. Keep such a unit happy until
    install.sh is run again (it installs a plain unit). Does nothing
    otherwise, and is not tied to the control loop.'''
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]

    def send(msg):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
                s.connect(addr)
                s.sendall(msg)
        except OSError:
            pass
    send(b"READY=1")
    usec = int(os.environ.get("WATCHDOG_USEC", "0") or 0)
    if usec:
        def ping():
            while True:
                send(b"WATCHDOG=1")
                gevent.sleep(usec / 2e6)
        gevent.spawn(ping)


def main():
    ip = "0.0.0.0"
    port = config.listening_port
    oven.start()
    ovenWatcher.start()
    log.info("listening on %s:%d" % (ip, port))
    server = WSGIServer((ip, port), app, log=None)
    server.start()
    systemd_compat()
    server.serve_forever()


if __name__ == "__main__":
    main()
