'''Public, read-only kiln status page.

The controller can start a small second web server (tools/status-server.py)
as a separate, low-priority child process. That server:

  - fetches a sanitized status snapshot from the controller over loopback
    (/api/public-status) every few seconds, whatever the number of visitors
  - serves GET / (the page), /status.css, /status.js and /status.json
    from memory, and nothing else: there is no code in it that can change
    the kiln, so a reverse proxy pointing at it can only ever show status
  - limits connections and times out slow clients, so a flood can take
    the status page down but not the control loop

See docs/public-status.md for putting it behind Nginx Proxy Manager.
'''
import json
import os
import threading
import time

from settings import settings
from units import to_display

STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "public", "status"))
GRAPH_POINTS = 200
OFFLINE_AFTER = 30          # seconds without a snapshot before "controller offline"


def _r(v, nd=0):
    if v is None:
        return None
    v = round(v, nd)
    return int(v) if nd == 0 else v


def _thin(points, n=GRAPH_POINTS):
    if len(points) <= n:
        return points
    step = len(points) / float(n - 1)
    out = [points[int(i * step)] for i in range(n - 1)]
    out.append(points[-1])
    return out


def snapshot(oven, watcher=None, now=None):
    '''what the public may see, in display units. Never settings, pins,
    passwords, alert URLs, costs or anything that could be used to act.'''
    s = oven.get_state()
    scale = settings.temp_scale
    state = s.get("state")
    active = state in ("RUNNING", "PAUSED", "TUNING")
    firing = state in ("RUNNING", "PAUSED")
    temp = s.get("temperature") if s.get("sensor_ok") else None
    out = {
        "title": settings.get("public_status_title") or "Kiln",
        "time": now if now is not None else time.time(),
        "unit": scale.upper(),
        "state": state,
        "temperature": _r(to_display(temp, scale)),
        "sensor_ok": bool(s.get("sensor_ok")),
        "target": _r(to_display(s.get("target"), scale)) if active else None,
        "heating_percent": _r((s.get("output") or 0) * 100) if active else 0,
        "simulate": bool(s.get("simulate")),
    }
    show_profile = settings.get("public_status_show_profile", True)
    if firing:
        total = s.get("totaltime") or 0
        runtime = s.get("runtime") or 0
        out["progress_percent"] = _r(min(100.0, 100.0 * runtime / total), 1) if total else None
        out["remaining_s"] = _r(max(0, total - runtime)) if total else None
        out["profile"] = s.get("profile") if show_profile else None
    sched = s.get("scheduled")
    if state == "SCHEDULED" and sched:
        out["scheduled_start"] = sched.get("start_at")
        out["profile"] = sched.get("profile") if show_profile else None
    if settings.get("public_status_show_errors", True):
        out["error"] = s.get("error")
        out["issues"] = [{"title": i.get("title"), "message": i.get("message")} for i in (s.get("issues") or [])]
    else:
        out["error"] = None
        out["issues"] = []
        # still say that something needs attention, without details
        out["attention"] = bool(s.get("error") or s.get("issues"))
    if firing and watcher is not None:
        log = _thin(list(watcher.last_log))
        out["graph"] = {"actual": [[_r(t), _r(to_display(c, scale))] for (t, c, _) in log]}
        prof = getattr(watcher, "last_profile", None)
        if prof is not None:
            out["graph"]["profile"] = [[_r(t), _r(to_display(c, scale))] for (t, c) in prof.data]
    return out


# ---------------------------------------------------------------------
# the child process' web app (plain WSGI, testable without gevent)

SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
]

FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/status.css": ("status.css", "text/css; charset=utf-8"),
    "/status.js": ("status.js", "application/javascript; charset=utf-8"),
}


class StatusApp(object):
    '''serves the page and the latest snapshot from memory'''

    def __init__(self, static_dir=STATIC_DIR, clock=time.time):
        self.clock = clock
        self.files = {}
        for path, (name, ctype) in FILES.items():
            with open(os.path.join(static_dir, name), "rb") as f:
                self.files[path] = (f.read(), ctype)
        self.lock = threading.Lock()
        self.body = None            # latest snapshot, JSON bytes
        self.last_ok = None         # when it was fetched
        self.last_seen = None       # the snapshot's own time

    def update(self, raw):
        '''store a snapshot (bytes or str of JSON) from the controller'''
        data = json.loads(raw)
        with self.lock:
            self.body = json.dumps(dict(data, online=True), separators=(",", ":")).encode()
            self.last_ok = self.clock()
            self.last_seen = data.get("time")

    def status_body(self):
        with self.lock:
            fresh = self.last_ok is not None and self.clock() - self.last_ok <= OFFLINE_AFTER
            if fresh:
                return self.body
            return json.dumps({"online": False, "last_seen": self.last_seen}).encode()

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        if method not in ("GET", "HEAD"):
            start_response("405 Method Not Allowed", [("Allow", "GET, HEAD"), ("Content-Length", "0")] + SECURITY_HEADERS)
            return [b""]
        if path == "/favicon.ico":
            start_response("204 No Content", [("Cache-Control", "public, max-age=86400")] + SECURITY_HEADERS)
            return [b""]
        if path == "/status.json":
            body, ctype, cache = self.status_body(), "application/json", "public, max-age=5"
        elif path in self.files:
            (body, ctype), cache = self.files[path], "public, max-age=300"
        else:
            body = b"not found"
            start_response("404 Not Found", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))] +
                           SECURITY_HEADERS)
            return [b"" if method == "HEAD" else body]
        start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(body))),
                                  ("Cache-Control", cache)] + SECURITY_HEADERS)
        return [b"" if method == "HEAD" else body]
