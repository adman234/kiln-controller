'''Temperature limits a schedule is checked against before it runs.

- emergency shutoff: a firing is stopped when the kiln reaches it, so a
  schedule that comes close is warned about
- MAX6675: the chip can not read above 1023C. A schedule that goes higher
  can not be controlled and is refused; one that comes close is warned
  about
'''
from settings import settings, MAX6675_MAX_C, LIMIT_MARGIN_C
from units import to_display, delta_to_display


def _fmt(c):
    scale = settings.temp_scale
    return "%d°%s" % (round(to_display(c, scale)), scale.upper())


def _fmt_delta(c):
    scale = settings.temp_scale
    return "%d°%s" % (round(delta_to_display(c, scale)), scale.upper())


def uses_max6675():
    return settings.sensor_board == "max6675"


def block_reason(peak_c):
    '''why a schedule (or autotune) reaching peak_c may not run, or None'''
    if uses_max6675() and peak_c > MAX6675_MAX_C:
        return ("This schedule goes to %s, but the MAX6675 temperature sensor can only read up to %s, "
                "so the controller could not see or control the kiln above that. Lower the schedule, "
                "or fit a MAX31855 or MAX31856 board (Settings -> Sensor)." % (_fmt(peak_c), _fmt(MAX6675_MAX_C)))
    return None


def warnings(peak_c):
    '''list of {"level", "text"} for a schedule reaching peak_c'''
    out = []
    reason = block_reason(peak_c)
    if reason:
        out.append({"level": "danger", "blocked": True, "text": reason})
    elif uses_max6675() and peak_c >= MAX6675_MAX_C - LIMIT_MARGIN_C:
        out.append({"level": "warning", "text":
                    "This schedule goes to %s, within %s of the most the MAX6675 sensor can read (%s)." % (
                        _fmt(peak_c), _fmt_delta(LIMIT_MARGIN_C), _fmt(MAX6675_MAX_C))})
    emergency = settings.emergency_shutoff_temp
    if peak_c >= emergency:
        out.append({"level": "danger", "text":
                    "This schedule goes to %s, at or above the emergency shutoff temperature (%s). "
                    "The firing will be stopped when the kiln gets there." % (_fmt(peak_c), _fmt(emergency))})
    elif peak_c >= emergency - LIMIT_MARGIN_C:
        out.append({"level": "warning", "text":
                    "This schedule goes to %s, within %s of the emergency shutoff temperature (%s). "
                    "A small overshoot could stop the firing." % (_fmt(peak_c), _fmt_delta(LIMIT_MARGIN_C), _fmt(emergency))})
    return out
