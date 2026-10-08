'''Firing profiles (schedules).

A profile on disk looks like:

    {"type": "profile", "name": "cone-6-long-glaze", "temp_units": "c",
     "data": [[0, 20], [3600, 100], ...],      # [seconds, temperature]
     "created": "2026-01-02T10:00:00", "modified": "...", "notes": ""}

Profiles are always stored in Celsius so they can be shared. Profiles
without temp_units were written by old versions and are in
config.temp_scale.
'''
import datetime
import json
import logging
import os
import re
import threading

import config
from units import convert_profile_data
from settings import atomic_write_json

log = logging.getLogger(__name__)

NAME_RE = re.compile(r'^[\w][\w .()+#,&\'-]{0,63}$', re.UNICODE)
AMBIENT_C = 20.0


class ProfileError(Exception):
    pass


class Profile():
    '''a schedule in internal units (seconds, Celsius)'''
    def __init__(self, json_data):
        obj = json.loads(json_data) if isinstance(json_data, str) else json_data
        self.name = obj["name"]
        data = [[float(t), float(temp)] for (t, temp) in obj["data"]]
        self.data = sorted(data)
        if len(self.data) < 2:
            raise ProfileError("a profile needs at least two points")
        # time must strictly increase or the slope math divides by zero
        for i in range(1, len(self.data)):
            if self.data[i][0] <= self.data[i - 1][0]:
                raise ProfileError("profile times must increase (point %d)" % (i + 1))

    def get_duration(self):
        return max([t for (t, x) in self.data])

    #  x = (y-y1)(x2-x1)/(y2-y1) + x1
    @staticmethod
    def find_x_given_y_on_line_from_two_points(y, point1, point2):
        if point1[0] > point2[0]: return 0  # time2 before time1 makes no sense in kiln segment
        if point1[1] >= point2[1]: return 0  # Zero will crash. Negative temperature slope, we don't want to seek a time.
        x = (y - point1[1]) * (point2[0] - point1[0]) / (point2[1] - point1[1]) + point1[0]
        return x

    def find_next_time_from_temperature(self, temperature):
        time = 0  # The seek function will not do anything if this returns zero, no useful intersection was found
        for index, point2 in enumerate(self.data):
            if point2[1] >= temperature:
                if index > 0:  # Zero here would be before the first segment
                    if self.data[index - 1][1] <= temperature:  # We have an intersection
                        time = self.find_x_given_y_on_line_from_two_points(temperature, self.data[index - 1], point2)
                        if time == 0:
                            if self.data[index - 1][1] == point2[1]:  # It's a flat segment that matches the temperature
                                time = self.data[index - 1][0]
                                break
        return time

    def get_surrounding_points(self, time):
        if time > self.get_duration():
            return (None, None)
        if time <= self.data[0][0]:
            return (self.data[0], self.data[1])
        for i in range(1, len(self.data)):
            if time <= self.data[i][0]:
                return (self.data[i - 1], self.data[i])
        return (self.data[-2], self.data[-1])

    def get_target_temperature(self, time):
        if time > self.get_duration():
            return 0
        if time <= self.data[0][0]:
            return self.data[0][1]
        (prev_point, next_point) = self.get_surrounding_points(time)
        incl = float(next_point[1] - prev_point[1]) / float(next_point[0] - prev_point[0])
        temp = prev_point[1] + (time - prev_point[0]) * incl
        return temp

    def degree_hours(self, start=0, end=None, ambient=AMBIENT_C):
        '''integral of (target - ambient) over time, in degree-hours.
        Heat lost by a kiln is roughly proportional to how hot it is for
        how long, so this is the basis of the cost estimate.'''
        if end is None:
            end = self.get_duration()
        total = 0.0
        for (t1, y1), (t2, y2) in zip(self.data, self.data[1:]):
            a, b = max(t1, start), min(t2, end)
            if b <= a:
                continue
            slope = (y2 - y1) / (t2 - t1)
            ya = y1 + (a - t1) * slope - ambient
            yb = y1 + (b - t1) * slope - ambient
            total += _positive_area(ya, yb, b - a)
        return total / 3600.0

    def peak(self):
        return max(temp for (_, temp) in self.data)


def _positive_area(ya, yb, width):
    '''area under a line segment, counting only the part above zero'''
    if ya >= 0 and yb >= 0:
        return (ya + yb) / 2 * width
    if ya <= 0 and yb <= 0:
        return 0.0
    # crosses zero
    frac = abs(ya) / (abs(ya) + abs(yb))
    if ya > 0:
        return ya / 2 * width * frac
    return yb / 2 * width * (1 - frac)


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def validate_name(name):
    if not isinstance(name, str):
        raise ProfileError("name must be text")
    name = name.strip()
    if not NAME_RE.match(name) or ".." in name:
        raise ProfileError("name may only contain letters, numbers, spaces and - _ . ( ) + # , & ' (max 64)")
    return name


def filename_for(name):
    '''safe filename for a profile name - never escapes the profile dir'''
    slug = re.sub(r'[^\w.+-]+', '-', name, flags=re.UNICODE).strip('-.') or "profile"
    return slug + ".json"


class ProfileStore(object):
    def __init__(self, directory=None):
        self.directory = directory or config.kiln_profiles_directory
        self.lock = threading.Lock()
        os.makedirs(self.directory, exist_ok=True)

    def _files(self):
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        return sorted(n for n in names if n.endswith(".json") and not n.startswith("."))

    def _read(self, filename):
        path = os.path.join(self.directory, filename)
        try:
            with open(path) as f:
                p = json.load(f)
        except (OSError, ValueError) as e:
            log.error("skipping unreadable profile %s: %s" % (path, e))
            return None
        if not isinstance(p, dict) or "name" not in p or "data" not in p:
            log.error("skipping %s, not a profile" % path)
            return None
        p["_file"] = filename
        if "created" not in p:
            p["created"] = datetime.datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0).isoformat()
        if "modified" not in p:
            p["modified"] = p["created"]
        return p

    @staticmethod
    def to_celsius(p):
        '''raw profile dict -> dict with data in C'''
        units = p.get("temp_units") or getattr(config, "temp_scale", "c")
        q = dict(p)
        q["data"] = convert_profile_data(p["data"], units.lower(), "c")
        q["temp_units"] = "c"
        return q

    def list(self):
        '''all profiles, data in Celsius'''
        out = []
        for fn in self._files():
            p = self._read(fn)
            if p:
                out.append(self.to_celsius(p))
        return out

    def find(self, name):
        for fn in self._files():
            p = self._read(fn)
            if p and p["name"] == name:
                return self.to_celsius(p)
        return None

    def get_profile(self, name):
        '''Profile object for running, or None'''
        p = self.find(name)
        if p is None:
            return None
        return Profile(p)

    def save(self, profile, original_name=None, overwrite=True):
        '''profile: dict with name, data (in temp_units), optional notes.
        original_name: set when editing so a rename moves the file.'''
        name = validate_name(profile.get("name"))
        units = (profile.get("temp_units") or "c").lower()
        if units not in ("c", "f"):
            raise ProfileError("temp_units must be c or f")
        try:
            data = [[float(t), float(temp)] for (t, temp) in profile["data"]]
        except (KeyError, TypeError, ValueError):
            raise ProfileError("data must be a list of [seconds, temperature]")
        data = convert_profile_data(data, units, "c")
        data = [[round(t), round(temp, 2)] for (t, temp) in data]
        Profile({"name": name, "data": data})  # validates points

        with self.lock:
            existing = self.find(name)
            if existing and not overwrite and name != original_name:
                raise ProfileError("a profile named %s already exists" % name)
            old = self.find(original_name) if original_name else None
            base = old or existing
            created = (base or {}).get("created") or now_iso()
            out = {
                "type": "profile",
                "name": name,
                "temp_units": "c",
                "data": data,
                "created": created,
                "modified": now_iso(),
                "notes": str(profile.get("notes", (base or {}).get("notes", "")))[:2000],
            }
            target = existing["_file"] if existing else filename_for(name)
            # avoid clobbering a different profile that slugs to the same file
            if not existing and os.path.exists(os.path.join(self.directory, target)):
                i = 2
                while os.path.exists(os.path.join(self.directory, filename_for("%s-%d" % (name, i)))):
                    i += 1
                target = filename_for("%s-%d" % (name, i))
            atomic_write_json(os.path.join(self.directory, target), out)
            log.info("wrote profile %s to %s" % (name, target))
            if old and old["name"] != name:
                self._remove(old["_file"])
        return out

    def _remove(self, filename):
        path = os.path.join(self.directory, filename)
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(self.directory):
            raise ProfileError("bad path")
        os.remove(path)
        log.info("deleted %s" % path)

    def delete(self, name):
        with self.lock:
            p = self.find(name)
            if p is None:
                raise ProfileError("profile %s not found" % name)
            self._remove(p["_file"])
        return True

    def copy(self, name, new_name=None):
        p = self.find(name)
        if p is None:
            raise ProfileError("profile %s not found" % name)
        if not new_name:
            n = 1
            existing = set(x["name"] for x in self.list())
            new_name = "%s copy" % name
            while new_name in existing:
                n += 1
                new_name = "%s copy %d" % (name, n)
        copy = {"name": new_name, "data": p["data"], "temp_units": "c", "notes": p.get("notes", "")}
        # a copy is a new profile, so it gets a fresh created date
        with self.lock:
            if self.find(new_name):
                raise ProfileError("a profile named %s already exists" % new_name)
        return self.save(copy, overwrite=False)
