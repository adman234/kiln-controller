# Current sensor (CT clamp)

A clamp-on current transformer (CT) around one element wire measures the
current the elements draw. It is optional.

- **Live reading:** shown under the power LED on the dashboard and under
  **Settings → Diagnostics**. **Test relay** there shows the current measured
  during the test.
- **Measured energy** (off by default): kWh and cost use mains voltage ×
  measured amps instead of the element power setting.

- **Pre-fire check** (on): when a firing or autotune starts (and again at the
  start time of a delayed start) the elements are switched on for 1 second.
  If no current flows the screen flashes, you get an alert and the firing
  waits: switch on the kiln's power switch and press **Try again**, or
  **Ignore the current sensor** for this firing.
- **No current while firing** (on, alert only): after 1 minute of on-time
  with no current. Can be set to stop the firing.
- **Stuck SSR** (on, alert only): current flowing for 1 minute with the
  elements off. Can be set to stop the firing.
- **Low current** (off): warns when the current while on drops below a value
  you set, e.g. when one element of several burns out.

See also [safety.md](safety.md).

## Parts

* **CT:** a YHDC **SCT-013** (blue, with a 3.5 mm plug), or similar. Read the
  model on the clamp:
  * **SCT-013-030 / -050 / ...** ("30A/1V") have a built-in burden resistor and
    output a voltage. Calibration = amps per volt: 30 for the -030, 50 for the -050.
    Pick one rated above your kiln current (a 9.5 kW kiln on 240 V draws about 40 A,
    so use the -050 or larger).
  * **SCT-013-000** ("100A:50mA") outputs a current. Put a **33 Ω** resistor
    across its two leads. Calibration = 2000 / 33 = **60.6**.
* **ADS1115** 16-bit ADC breakout (I2C).
* 2 × 10 kΩ resistors and a 10 µF capacitor for the bias voltage.
* A 3.5 mm socket, or cut the plug off. Tip and sleeve are the two leads.

## Wiring

```
ADS1115            Pi Zero W
VDD   ──────────── pin 1  (3.3V)
GND   ──────────── pin 6  (GND)
SCL   ──────────── pin 5  (BCM3 / SCL)
SDA   ──────────── pin 3  (BCM2 / SDA)
ADDR  ──────────── GND    (I2C address 0x48)

CT 3.5mm plug
  tip    ───────── A0
  sleeve ───────── A1 ──┬── 10kΩ ── 3.3V
                        ├── 10kΩ ── GND        (holds A1 at 1.65 V)
                        └── 10µF ── GND
  SCT-013-000 only: 33Ω across tip and sleeve
```

The ADS1115 can't measure below 0 V, and the CT output swings both ways.
Holding the other CT lead at 1.65 V keeps the signal inside the range, and
the controller measures A0 − A1. The installer already enables I2C. These
pins don't clash with the thermocouple or SSR pins.

**Clamp the CT around one wire only**, on the element side of the SSR. A whole
power cord reads zero because its two wires carry equal and opposite current.

## Setting up

1. **Settings → Current:** set *Current sensor* to *CT on an ADS1115*, enter
   the calibration for your CT, Save, then Restart.
2. **Settings → Diagnostics:** with the elements off the reading should be
   close to 0 A. Run **Test relay** for 5 s: the reading with the elements on
   should be close to your kiln's current (kW × 1000 / volts). If you have a
   clamp meter, adjust *CT calibration* until the two agree.
3. Set *Elements count as on above* to well above the off reading and well
   below the on reading. 2 A suits most kilns.
4. For a kiln with several elements on one wire, set *Warn if current while
   on is below* a little under your normal reading. You'll then get a warning
   if an element burns out.

If the ADS1115 isn't found at startup, the controller keeps running without
the current checks and Diagnostics shows the error. Check the wiring and the
I2C address (`i2cdetect -y 1` over SSH should show `48`).
