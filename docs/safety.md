# Safety features

Most problems a kiln can have are better handled by **alerting you first**
than by stopping a firing that might have been fine. So unless noted below,
a problem sends an alert straight away, and the firing is only stopped if
the problem is still there after the **safety wait** (5 minutes by default,
*Settings → Safety → Minutes between an alert and stopping the firing*).
While a problem is active the dashboard shows a red banner with a countdown
to when the firing would be stopped. If the problem clears up, you get an
"OK again" alert and the firing carries on.

Set up phone alerts (*Settings → Alerts*, or the setup wizard) or you won't
see any of this unless you are looking at the screen.

| Problem | What happens | Default |
| --- | --- | --- |
| Kiln reaches the **emergency shutoff temperature** | Firing stopped at once | on, 1288°C / 2350°F |
| Schedule comes within 50°C (90°F) of the emergency temperature | Warning when you start or schedule it, and in the editor | always |
| **MAX6675** schedule hotter than the chip can read (1023°C / 1873°F) | Can not be started or scheduled | always |
| MAX6675 schedule within 50°C (90°F) of 1023°C | Warning when you start it | always |
| **Stuck relay** (kiln heats with the elements off) | Alert, stop after the wait | on: 20°C rise in 15 min |
| **No heat** (elements fully on, kiln not warming) | Alert, stop after the wait | on: under 3°C in 45 min |
| **Thermocouple dropout** (no trustworthy reading) | Elements held off and schedule waits; alert after 30 s; stop after the wait | on |
| **Behind schedule** | Alert only | 60 minutes behind |
| **Software error** in the control loop | Alert only, the firing carries on | on |
| **Controller freezes** (software watchdog) or the Pi locks up (hardware watchdog) | Restarted automatically, the firing resumes, alert. A second restart within the wait stops the firing | on |

With a [current sensor](current-sensor.md):

| Problem | What happens | Default |
| --- | --- | --- |
| **Pre-fire check:** 1 second test pulse when a firing (or autotune) starts, and at the start time of a delayed start | No current: the screen flashes, you get an alert, and the firing waits. Turn on the kiln's power switch and press **Try again**, or **Ignore the current sensor** for this firing, or cancel | on |
| **No current while firing** | Alert after 1 minute of on-time; optional stop | alert only |
| **Stuck SSR** (current with the elements off) | Alert after 1 minute; optional stop | alert only |
| **Low current** (a burnt-out element in a multi-element kiln) | Alert | off |

## Emergency shutoff temperature

*Settings → Firing → Emergency shutoff temperature*, or the setup wizard.
Any firing is stopped as soon as the kiln reaches it. Set it a little above
your hottest firing: when you start a schedule whose peak is within 50°C
(90°F) of it, you get a warning, because a normal overshoot could stop the
firing near the end.

## MAX6675

The MAX6675 can't read above 1023°C (1873°F). The controller would be blind
above that, so schedules that go hotter are refused, in the start dialog
(the Start button is disabled), for delayed starts, and through the API.
The library marks them *too hot*. For stoneware and porcelain fit a MAX31855
or MAX31856.

## Thermocouple dropout

When the sensor gives no trustworthy reading (disconnected, too many
errors, or no reading for 10 seconds), the elements are kept **off** and the
schedule waits: the controller never heats blind. After 30 seconds without
a reading you get an alert. If readings come back the firing carries on
from where it waited; if they are still missing after the safety wait the
firing is stopped.

## Watchdogs

- **Software:** the service tells systemd every control cycle that it is
  alive. If it stops for 90 seconds, systemd restarts it and the firing
  resumes (like after a power cut, see *Resume run after power failure*).
- **Hardware:** `install.sh` turns on the Pi's hardware watchdog
  (`/etc/systemd/system.conf.d/kiln-watchdog.conf`). If the whole Pi locks
  up, it reboots within about 15 seconds and the firing resumes.

Each restart sends an alert saying what happened (froze, crashed, or power
cut / reboot). If the controller has to restart **again** within the safety
wait, it doesn't resume: it stops the firing and tells you.

Switch it off with *Settings → Safety → Restart the controller if it
freezes* (needs a restart).

## Safety contactor

SSRs usually fail *stuck on*. A **contactor** (a heavy mechanical relay)
wired in series with the SSR is only closed while a firing (or autotune or
relay test) runs, and opened when it ends or is stopped. A stuck SSR then
can't keep heating an idle kiln.

```
mains L ──[contactor contacts]──[SSR]── elements ── mains N
                │coil
     Pi GPIO ──[relay module / transistor + diode]
```

- Use a contactor rated for your kiln's current, with a coil you can drive
  from a relay module or a transistor (never straight from a GPIO pin).
- Pick its pin in the setup wizard or *Settings → Safety* (the pin map shows
  which pins are free). `-1` means no contactor.
- The contactor closes 0.3 s before the SSR switches on, so its contacts
  don't switch under load.

## Setup wizard

The first time you open the web UI, a setup wizard asks about units, the
sensor board, the relay and its pin, the safety contactor, the current
sensor, the emergency temperature and alerts. Pins are picked on a picture
of the Pi's GPIO header (the controller detects the Pi model). **Skip – use
defaults** leaves everything as it is: simulation mode, so nothing heats.

The **SETUP** button (top right) runs the wizard again, or resets all
settings to their defaults first. Before resetting it lists every setting
that would change, with its current and default value, and asks you to
confirm. Schedules and firing history are kept.
