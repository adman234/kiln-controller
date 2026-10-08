import datetime
import json
import logging
import threading
import time

from settings import settings
from units import state_to_display, to_display, convert_profile_data

log = logging.getLogger(__name__)

# a 3 day firing at one point every 2s is 130k points, keep it bounded
# for a 512MB pi zero by thinning the log when it gets big
MAX_LOG_POINTS = 4000


class OvenWatcher(threading.Thread):
    def __init__(self, oven):
        self.last_profile = None
        self.last_log = []   # [runtime, temperature C, target C]
        self.log_stride = 1
        self.log_counter = 0
        self.started = None
        self.recording = False
        self.listeners = []
        self.lock = threading.Lock()
        threading.Thread.__init__(self)
        self.daemon = True
        self.oven = oven

    def run(self):
        while True:
            try:
                oven_state = self.oven.get_state()
                if oven_state.get("state") in ("RUNNING", "PAUSED", "TUNING"):
                    self.append_log(oven_state)
                else:
                    self.recording = False
                self.notify_all(oven_state)
            except Exception:
                log.exception("ovenwatcher error")
            time.sleep(self.oven.time_step)

    def append_log(self, s):
        self.log_counter += 1
        if self.log_counter % self.log_stride:
            return
        x = s["runtime"] if s["state"] != "TUNING" else (s.get("autotune") or {}).get("elapsed", 0)
        self.last_log.append([x, s["temperature"], s["target"]])
        if len(self.last_log) > MAX_LOG_POINTS:
            self.last_log = self.last_log[::2]
            self.log_stride *= 2

    def lastlog_subset(self, maxpts=500):
        '''send about maxpts from lastlog by skipping unwanted data'''
        totalpts = len(self.last_log)
        if totalpts <= maxpts:
            return self.last_log
        every_nth = int(totalpts / (maxpts - 1))
        return self.last_log[::every_nth]

    def record(self, profile):
        self.last_profile = profile
        self.last_log = []
        self.log_stride = 1
        self.log_counter = 0
        self.started = datetime.datetime.now()
        self.recording = True
        # we just turned on, add first state for nice graph
        self.append_log(self.oven.get_state())

    def backlog(self):
        scale = settings.temp_scale
        if self.last_profile:
            p = {
                "name": self.last_profile.name,
                "data": convert_profile_data(self.last_profile.data, "c", scale),
                "type": "profile",
            }
        else:
            p = None
        return {
            'type': "backlog",
            'profile': p,
            'log': [{"runtime": r, "temperature": to_display(t, scale), "target": to_display(tg, scale)}
                    for (r, t, tg) in self.lastlog_subset()],
        }

    def add_listener(self, q):
        '''q: a queue that receives JSON strings (Server-Sent Events)'''
        q.put_nowait(json.dumps(self.backlog()))
        with self.lock:
            self.listeners.append(q)

    def remove_listener(self, q):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def notify_all(self, message):
        message_json = json.dumps(state_to_display(message, settings.temp_scale))
        with self.lock:
            listeners = list(self.listeners)
        for q in listeners:
            try:
                q.put_nowait(message_json)
            except Exception:
                # a client that stopped reading; drop it
                self.remove_listener(q)
