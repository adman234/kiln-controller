Safety
======

A kiln controller switches several kilowatts at 1000&deg;C+. Software can do
a lot to catch problems, but it cannot switch off a relay that has failed
stuck **on**, and solid state relays (SSRs) usually fail exactly that way.
This page explains what the software does, and the wiring that covers what
software can't.

**Never leave a firing kiln unattended because the controller says it is
fine.** Follow your local electrical codes. If you are not comfortable
wiring mains voltage, have an electrician do it.

## What the software does

| Protection | What it catches | What happens |
| --- | --- | --- |
| Emergency temperature | Kiln reaches *Emergency shutoff temperature* | Firing stopped, elements off, contactor opened, alert |
| Sensor errors | Too many failed thermocouple readings | Firing stopped, alert (also alerts while idle) |
| **Stuck relay detection** | Kiln keeps heating for 10 min with the elements commanded off (works while idle too) | Firing stopped, **contactor opened**, urgent alert |
| **No-heat detection** | Elements fully on for 30 min but the kiln does not warm up: thermocouple fell out of the kiln, broken element, failed relay, or a kiln at its limit | Firing stopped, urgent alert |
| Current sensor (optional) | With a [CT clamp](current-sensor.md): no current at the pre-fire check or while the elements are on, or current with them off (stuck SSR within seconds) | Firing not started / stopped, contactor opened, alert. Each check can be switched off in *Settings &rarr; Current* |
| Behind schedule | The kiln can't keep up with the schedule for 60 min | Alert (often worn elements) |
| Crash protection | A bug in the control loop | Elements off, firing stopped, error shown |
| Service watchdog | The controller hangs | systemd restarts it after 90 s; the relay is forced off in between |
| Hardware watchdog | The whole Pi locks up | The Pi reboots after 15 s |
| Relay-off on start/stop | Service stopped, crashed or restarted | The relay output is driven off before start and after stop |
| Power-failure resume | Power returns within 15 min (configurable) | The firing continues where it was |

All thresholds are in *Settings &rarr; Safety &amp; Alerts*. Alerts go to your
phone through ntfy, Pushover or a webhook (Slack, Discord, Home Assistant
...). Send a test alert after setting it up.

## The safety contactor (strongly recommended)

Wire a **contactor** (a big mechanical relay) in series with the SSR, and
let the controller drive its coil from a spare GPIO pin through a small relay
module or transistor driver, the same way it drives the SSR.

```
 mains L ──[ breaker / fuse ]──[ contactor contacts ]──[ SSR ]──[ elements ]── N
                                     ▲
                       coil driven by GPIO (Settings → Safety: contactor pin)
```

The controller:

- keeps the contactor **open whenever it is not firing**, so a stuck SSR
  cannot heat the kiln while it sits idle,
- closes it only when a firing (or autotune, or relay test) first needs heat,
  waits 0.3 s, then switches the SSR,
- opens it at the end of every firing and immediately on any emergency,
  including stuck relay detection.

Choose a contactor rated for your kiln's full current (AC-1 rating) with a
coil your driver can switch. Set the pin in *Settings &rarr; Safety &amp; Alerts
&rarr; Safety contactor output pin* and restart the controller. *Settings
&rarr; Diagnostics &rarr; Test relay* should now click both.

## Independent of the Pi

The contactor protects against a failed SSR, but it is still controlled by
the Pi. For protection that does not depend on the Pi at all, add one or
both of:

- **A high-limit cut-out** that opens the contactor coil circuit by itself:
  an independent over-temperature controller with its own thermocouple and
  an alarm relay, or the kiln's original **kiln sitter / limit timer** if it
  has one. Set it a little above your hottest firing.
- **A watchdog relay on the heartbeat output.** Set *Heartbeat output pin*;
  the controller toggles it every control cycle (every 2 s). A retriggerable
  watchdog timer module wired into the contactor's coil circuit drops the
  contactor if the pulses stop for longer than its timeout (set it to 10&ndash;30 s).
  This catches a frozen Pi, a crashed program and a disconnected cable.

## Thermocouple

- Use a heavy duty, ceramic-sheathed thermocouple made for kilns, and the
  right type for your board (MAX31855/MAX6675 are K-type only).
- Check it in ice water occasionally and set the offset in *Settings &rarr;
  Sensor*.
- Witness cones tell you the truth about heat work; compare them with the
  controller now and then.

## Before every firing

- Kiln clear of anything flammable, lid and peepholes as your schedule needs.
- The alert test notification arrives on your phone.
- You know how to switch the kiln off at the breaker.
