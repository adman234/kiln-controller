'''Temperature unit helpers.

The controller works in degrees Celsius internally. Fahrenheit is only a
display preference, so values are converted when they cross the boundary
to the web UI / API.
'''


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def to_display(c, scale):
    '''absolute temperature in C -> display scale'''
    if c is None:
        return None
    return c_to_f(c) if scale == "f" else c


def from_display(t, scale):
    '''absolute temperature in display scale -> C'''
    if t is None:
        return None
    return f_to_c(t) if scale == "f" else t


def delta_to_display(dc, scale):
    '''temperature difference (or rate) in C -> display scale'''
    if dc is None:
        return None
    return dc * 9.0 / 5.0 if scale == "f" else dc


def delta_from_display(d, scale):
    if d is None:
        return None
    return d * 5.0 / 9.0 if scale == "f" else d


def convert_profile_data(data, from_scale, to_scale):
    '''convert [[secs, temp], ...] between scales'''
    if from_scale == to_scale:
        return [[t, temp] for (t, temp) in data]
    if to_scale == "c":
        return [[t, f_to_c(temp)] for (t, temp) in data]
    return [[t, c_to_f(temp)] for (t, temp) in data]


def pidstats_to_display(pidstats, scale):
    '''pidstats are computed in C, convert the temperature fields'''
    if not pidstats:
        return pidstats
    p = dict(pidstats)
    for k in ("setpoint", "ispoint"):
        if k in p:
            p[k] = to_display(p[k], scale)
    for k in ("err", "errDelta"):
        if k in p:
            p[k] = delta_to_display(p[k], scale)
    return p


def state_to_display(state, scale):
    '''convert an oven state dict (internal C) for the UI / API'''
    s = dict(state)
    for k in ("temperature", "target"):
        if k in s:
            s[k] = to_display(s[k], scale)
    if "heat_rate" in s:
        s["heat_rate"] = delta_to_display(s["heat_rate"], scale)
    if "pidstats" in s:
        s["pidstats"] = pidstats_to_display(s["pidstats"], scale)
    if s.get("autotune"):
        a = dict(s["autotune"])
        for k in ("setpoint", "peak", "trough"):
            if a.get(k) is not None:
                a[k] = to_display(a[k], scale)
        if a.get("amplitude") is not None:
            a["amplitude"] = delta_to_display(a["amplitude"], scale)
        s["autotune"] = a
    s["temp_scale"] = scale
    return s
