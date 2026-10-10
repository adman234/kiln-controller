What's different from the original kiln-controller
==================================================

This fork is based on [jbruce12000/kiln-controller](https://github.com/jbruce12000/kiln-controller)
(as of its merge of PR #186). This page lists everything that changed, so
you can decide whether it suits you and what to check when moving over.

Install guide: [docs/install.md](docs/install.md).

---

## Moving from the original

- **Your schedules** in `storage/profiles` work as they are. Old ones
  without `temp_units` are read in your `config.py` `temp_scale`.
- **config.py** is now only the defaults. Most settings are changed in the
  web UI and saved to `storage/settings.json`, which overrides config.py.
  Delete that file to go back to the defaults.
- **Pins** in config.py are plain BCM numbers (`gpio_heat = 23`) instead of
  `board.D23`. Old-style values are still understood.
- **Temperatures are Celsius inside the controller.** Fahrenheit is only how
  they are shown, switchable with one click. A Fahrenheit config.py is
  converted automatically, including the PID gains (Kp and Kd &times;1.8,
  Ki &divide;1.8).
- **PID behaves slightly differently** (derivative on temperature instead
  of error, integral limit). Run the new autotune.
- **Websockets are gone.** Live status is a Server-Sent Events stream at
  `/api/events`; everything else is a JSON API (see [docs/api.md](docs/api.md)).
  The `/control`, `/storage`, `/config` and `/status` websockets no longer
  exist. `kiln-logger.py` was updated; your own scripts that used the
  websockets need to switch to the API.
- **The service** runs as your user instead of root and is installed by
  `install.sh` with the correct paths (the old one assumed `/home/pi`, which
  no longer exists on current Raspberry Pi OS).
- The `/state` page no longer needs internet access.

---

## Safety

The fork added a set of safety features (stuck relay and no-heat
detection, safety contactor and heartbeat outputs, watchdogs, current
sensor checks, thermocouple-health handling and more). **They have all been
removed again**, pending a one-by-one review. The controller keeps the two
protections the original kiln-controller already had: the emergency
shutoff temperature, and stopping after too many thermocouple errors (both
can be switched off under *Settings &rarr; Advanced &rarr; Errors*).

What remains in this area:

- Software errors in the control loop are logged and the loop carries on
  with the next cycle (the original stopped controlling the kiln silently).
- Phone alerts (ntfy, Pushover or a webhook) for firing finished/started,
  autotune results and power-failure resume, with a test button.
- When the sensor has not produced any reading yet (just after start-up),
  the elements stay off for that cycle instead of the program crashing.

## Bugs fixed

- Profile at exactly its end time crashed the control loop; a profile whose
  first point is after time 0 used the *last* point; duplicate times divided
  by zero; profiles with fewer than two points crashed.
- A corrupt `state.json` (power cut while writing) stopped the controller
  from starting. All state, settings, schedules and history are now written
  atomically.
- Automatic restart after a power cut ran Celsius schedules as Fahrenheit,
  and looked in a hard-coded directory.
- Cost on real kilns counted one second of power per cycle whatever the duty
  cycle; now it uses the real on-time.
- The UI cost estimate used a hard-coded 3850 W instead of your elements'
  power.
- PID: division by zero when two readings had the same timestamp; no
  limit on the integral; derivative "kick" whenever the schedule changed
  slope or the kiln entered the PID window.
- The temperature median started from a window of zeros; readings of
  exactly 0 were thrown away.
- Pause/resume when nothing was running put the oven in an impossible state.
- The live-status sender skipped clients when removing a dead one; its log
  grew without limit (memory on a 512MB Pi Zero).
- The web server and the control threads blocked each other (gevent was
  used without monkey patching).
- Countdown and run times were wrong past 24 hours.
- Non-JSON files in the profiles folder crashed the schedule list.
- `abort()` was undefined in the websocket handler; static files were served
  relative to the current directory.
- The automatic restart state was written every 2 s (SD card wear); now
  every 30 s.

## Security

- Schedule names could contain `../` and write or delete any `.json` file the
  controller could reach. Names are now validated and file names are made
  safe.
- Any web page you visited could start your kiln through your browser
  (cross-site requests and websocket hijacking). Cross-origin requests are
  now rejected.
- Schedule names were inserted into the page unescaped (stored XSS).
- Optional web UI password (HTTP basic auth, compared in constant time).
- Warning banner when the controller is reachable from the internet
  without a password.
- Old jQuery 1.10 and Bootstrap 3.0 (known XSS issues) replaced with
  jQuery 3.7.1 and Bootstrap 3.4.1; select2, bootstrap-growl and the old
  drag plugins removed.
- Backups never include passwords or tokens.

## Features

**Settings in the web UI.** Units, cost, firing behaviour, PID, sensor
board and type, pins, error handling, alerts, simulation, password.
Settings that need it show "needs restart" and there is a restart button.

**One-click &deg;C / &deg;F.**

**PID autotune in the web UI.** Relay (&Aring;str&ouml;m&ndash;H&auml;gglund) method at a
temperature you choose. Shows the measured oscillation, four tuning rules
(conservative recommended), and applies with one click. Can widen the PID
window so the PID does more than on/off control. The command line
`kiln-tuner.py` still works.

**Sensors.** MAX31855, MAX31856 (all types, 50/60 Hz filter), MAX6675,
MCP9600/9601 (I2C) and MAX31865 RTDs. Diagnostics page: live and raw
reading, cold junction temperature, error rate, recent errors, Pi CPU
temperature, memory, disk, under-voltage, a relay test button.

**Cost estimates that learn.** One short record per firing (last 100). The
estimate averages earlier firings of the same schedule, or learns your
kiln's kWh per degree-hour from other firings; with no history it gives a
rough guess and a worst case.

**Schedule library.** Created and modified dates, notes, duration, peak, last
fired, estimated cost. Search and sort. Edit, duplicate, rename, delete,
download as a file, import files, export all.

**Easier editing.** Time in minutes, hours or h:mm. Editable ramp rate.
Insert holds. "Shift later points" keeps segment lengths. A "ramp at X/hr
to Y then hold Z" builder. Drag points with mouse or finger.

**Delayed start.** "Start in 6 h" or "start at 5:00 am", kept by the
controller across reboots, with countdown and cancel.

**Hold / resume buttons** for the existing pause feature.

**Backup and restore** of settings, schedules and history in one file.

**Phone friendly.** Readouts stack on small screens, full-screen dialogs,
touch dragging, "Add to Home Screen" icon.

**Offline /state page** for PID tuning (charts, last 20 cycles, CSV
download) without the three CDN libraries it used to load.

## Installation

- **Zero-touch install:** flash with Raspberry Pi Imager, copy one file
  (`vendor-data`) to the SD card, power on. It installs itself on first boot
  with a live progress page. Optional `kiln-install.conf` and
  `kiln-settings.json` pre-configure it.
- **`install.sh`** for manual installs and updates: apt gevent (no
  compiling on a Pi Zero), virtual environment, SPI/I2C, groups, systemd
  service, log cap, Wi-Fi power-save off. Options for SD card
  protection, Tailscale and pre-set settings.
- **SD card protection** option: logs and `/tmp` in RAM, no swap file, no
  nightly apt jobs.
- Simulations run on any computer without the Raspberry Pi libraries.

## Development

- Test suite (`python -m pytest`) covering units, settings, schedules,
  history, autotune, PID, simulated firings, scheduled
  starts, power-failure resume, notifications and the live stream.
- GitHub Actions CI: tests on Python 3.11 and 3.13, lint, shellcheck,
  server smoke test.
- Code split into `lib/settings.py`, `sensors.py`, `profiles.py`,
  `history.py`, `autotune.py`, `notify.py`, `units.py`, `sdnotify.py`.
