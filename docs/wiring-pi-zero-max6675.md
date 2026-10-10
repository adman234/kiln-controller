# Wiring: Pi Zero W + MAX6675 + SSRs

This uses the repo's default pins. `config.py` already sets
`sensor_board = "max6675"`. If you switch to a MAX31855, change it under
**Settings → Sensor → Sensor board**.

> **Read this first:** the MAX6675 only reads up to **1023.75 °C (1875 °F)**.
> Above that it keeps reporting 1023.75, so the controller thinks the kiln is
> still cold and keeps heating. Bisque (cone 04, about 1060 °C) and glaze
> (cone 6, about 1220 °C) both go past that limit. For pottery firings, use a
> **MAX31855** or **MAX31856**. Their pins are the same, so the wiring below
> doesn't change. The MAX6675 is fine only for glass, annealing and other
> firings that stay under about 1000 °C.

## Header pins used

```
            Pi Zero W header (pin 1 = square pad, end nearest the SD card)
                        3V3  (1) (2)  5V  ──────────────► SSR(s) +
                             (3) (4)
                             (5) (6)  GND
                             (7) (8)
  MAX6675 GND  ◄──────  GND  (9) (10)
  MAX6675 SCK  ◄──── BCM17 (11) (12)
  MAX6675 SO   ────► BCM27 (13) (14)  GND ───────────► transistor E / MOSFET GND
  MAX6675 CS   ◄──── BCM22 (15) (16)  BCM23 ──[1kΩ]──► transistor B / MOSFET SIG
  MAX6675 VCC  ◄──────  3V3 (17) (18)
```

| Function        | config.py      | BCM | Physical pin | WiringPi name |
|-----------------|----------------|-----|--------------|---------------|
| MAX6675 SCK     | `spi_sclk`     | 17  | 11           | GPIO.0        |
| MAX6675 SO / DO | `spi_miso`     | 27  | 13           | GPIO.2        |
| MAX6675 CS      | `spi_cs`       | 22  | 15           | GPIO.3        |
| SSR drive       | `gpio_heat`    | 23  | 16           | GPIO.4        |
| MAX6675 VCC     | n/a            | 3V3 | 1 or 17      |               |
| SSR +           | n/a            | 5V  | 2 or 4       |               |
| Grounds         | n/a            | GND | 6, 9, 14...  |               |

The pin numbers in config.py and in the web UI are **BCM** numbers. They are
not physical pin numbers and not WiringPi numbers. `gpio readall` and some
pinout cards print WiringPi names ("GPIO.0", "GPIO.2"...), and those are
easy to mix up with BCM numbers.

## SSR driver (2N2222A, NPN low-side switch)

```
 Pi 5V (pin 2) ─────────────┬──────────────┐
                            │              │
                         SSR1 +         SSR2 +     (SSR inputs in parallel)
                         SSR1 −         SSR2 −
                            │              │
                            └──────┬───────┘
                                   │
                                   C
 BCM23 (pin 16) ──[ 1 kΩ ]──┬──── B   2N2222A
                            │      E
                        [ 10 kΩ ]  │
                            │      │
 Pi GND (pin 14) ───────────┴──────┘
```

* The **1 kΩ base resistor is required**. Without it, the GPIO pin pushes
  current straight into the base-emitter diode, which can damage the pin.
* The 10 kΩ from base to GND is optional. It keeps the SSRs off while the Pi
  boots, before the GPIO pin is set up.
* Check the pinout printed on the part. **PN2222A / 2N2222A** (TO-92) is
  **E-B-C** with the flat face toward you. **P2N2222A** (onsemi) is
  **C-B-E**, the other way round. Base is in the middle on both, but if you
  swap E and C the transistor barely switches.
* Leave `gpio_heat_invert = False`: a high output turns the SSRs on.

## Using a MOSFET module instead

This works if the MOSFET is **logic-level at 3.3 V**: its Rds(on) is
specified at Vgs = 2.5 V or 4.5 V, and its Vgs(th) is under about 2 V.
AO3400 and AOD4184 boards are suitable.

**IRF520 / IRF520N modules** (pins SIG, VCC, GND plus VIN/GND and V+/V−
screw terminals) are **not** suitable. The IRF520N's gate threshold is
2 to 4 V, and its on-resistance is only specified at 10 V. From a 3.3 V pin,
some boards switch the SSRs and others don't, and the result can change with
temperature. The 2N2222A circuit above does the job reliably.

### Dual-D4184 "15A 400W" trigger board

This board has two AOD4184 MOSFETs, two screw terminals, and a 4-pin
GND / TRIG-PWM header. The AOD4184 switches fully at 3.3 V, so the Pi can
drive it directly. It replaces the 2N2222A and both resistors, because the
board already has its own gate resistor and pull-down.

```
            Pi Zero W                         D4184 board
   pin 16 (BCM23) ─────────────────────► TRIG/PWM
   pin 14 (GND)   ─────────────────────► GND (header)

   pin 2  (5V)    ─────────────────────► VIN+ / DC+  ┐ input
   pin 6  (GND)   ─────────────────────► VIN− / DC−  ┘ terminal

                    SSR1 +  SSR2 +  ◄─── OUT+        ┐ output
                    SSR1 −  SSR2 −  ◄─── OUT−        ┘ terminal
```

* The terminal labels are usually printed on the underside of the board.
  Check with a meter: **OUT+ is connected straight to VIN+**, and **OUT− is
  the switched side** (the MOSFET drain).
* The header has two GND pins and two TRIG/PWM pins; each pair is joined
  on the board, so you only need one of each.
* The onboard LED lights when the output is on. That makes it easy to test
  with `./test-output.py` before you connect the SSRs.
* Leave `gpio_heat_invert = False`.

## Other checks

* **Thermocouple polarity:** a K-type has a yellow (+) lead and a red (−)
  lead in US colours. If the reading drops as the kiln heats, the leads are
  swapped.
* Keep the thermocouple and SPI wires short, and run them away from the
  mains and element wiring.
* SSRs need a heatsink at kiln currents.
* Test before firing: `./test-thermocouple.py` and `./test-output.py`.
