'''Small summary of past firings used for cost estimates.

Only one short record per firing is kept (last MAX_RECORDS), never the
temperature log, so this stays a few KB.

Estimate model: the energy a kiln uses is mostly heat lost through the
walls, which is roughly proportional to (temperature - ambient) x time.
So from past firings we learn

    k = kWh used / degree-hours of the schedule that was fired

and estimate a new schedule as k x its degree-hours. If the same
schedule has been fired before we just average those firings.
'''
import json
import logging
import os
import threading

from settings import settings, STORAGE_DIR, atomic_write_json

log = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(STORAGE_DIR, "run_history.json")
MAX_RECORDS = 100
# runs with less than this many degree-hours say little about the kiln
MIN_DEGREE_HOURS = 20.0
# with no history at all assume the elements are on this fraction of the time
DEFAULT_DUTY = 0.5


class RunHistory(object):
    def __init__(self, path=HISTORY_FILE):
        self.path = path
        self.lock = threading.Lock()
        self.records = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
        return []

    def add(self, record):
        with self.lock:
            self.records.append(record)
            self.records = self.records[-MAX_RECORDS:]
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                atomic_write_json(self.path, self.records, indent=1)
            except OSError as e:
                log.error("could not save run history: %s" % e)
        log.info("recorded run: %s" % record)

    def clear(self):
        with self.lock:
            self.records = []
            atomic_write_json(self.path, self.records)

    def relevant(self):
        '''real runs when controlling a kiln, simulated runs in simulation'''
        sim = bool(settings.simulate)
        return [r for r in self.records if bool(r.get("simulated")) == sim]

    def for_profile(self, name):
        return [r for r in self.relevant() if r.get("profile") == name]

    def estimate(self, profile):
        '''profile: profiles.Profile (C). returns dict with kwh, cost, how'''
        kw = float(settings.kw_elements)
        rate = float(settings.kwh_rate)
        dh = profile.degree_hours()
        duration_h = profile.get_duration() / 3600.0
        max_kwh = kw * duration_h
        result = {"degree_hours": round(dh, 1), "duration_h": duration_h,
                  "max_kwh": round(max_kwh, 2), "max_cost": round(max_kwh * rate, 2),
                  "currency_type": settings.currency_type, "runs_used": 0}

        # 1) same schedule fired to (nearly) completion before
        same = [r for r in self.for_profile(profile.name)
                if r.get("completed") and r.get("degree_hours")
                and abs(r["degree_hours"] - dh) <= 0.1 * max(dh, 1)][-5:]
        if same:
            kwh = sum(r["kwh"] for r in same) / len(same)
            result.update(kwh=kwh, method="same_profile", runs_used=len(same))
        else:
            # 2) learn kWh per degree-hour from any useful past run
            useful = [r for r in self.relevant()
                      if r.get("degree_hours", 0) >= MIN_DEGREE_HOURS and r.get("kwh", 0) > 0][-20:]
            if useful:
                k = sum(r["kwh"] for r in useful) / sum(r["degree_hours"] for r in useful)
                kwh = k * dh
                result.update(kwh=kwh, method="history_model", runs_used=len(useful),
                              kwh_per_degree_hour=k)
            else:
                # 3) nothing learned yet
                kwh = max_kwh * DEFAULT_DUTY
                result.update(kwh=kwh, method="no_history")
        kwh = min(result["kwh"], max_kwh)
        result["kwh"] = round(kwh, 2)
        result["cost"] = round(kwh * rate, 2)
        return result

    def summary_by_profile(self):
        '''{name: {"last_fired": iso, "runs": n, "avg_kwh": x}}'''
        out = {}
        for r in self.relevant():
            name = r.get("profile")
            if not name:
                continue
            s = out.setdefault(name, {"runs": 0, "last_fired": None, "kwh": []})
            s["runs"] += 1
            s["last_fired"] = r.get("started")
            if r.get("completed"):
                s["kwh"].append(r.get("kwh", 0))
        for s in out.values():
            k = s.pop("kwh")
            s["avg_kwh"] = round(sum(k) / len(k), 2) if k else None
        return out
