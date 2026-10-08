Installing on a Raspberry Pi (including the original Pi Zero W)
==============================================================

This takes about 30 minutes on a Pi Zero W, most of it waiting.

## 1. Flash the SD card with Raspberry Pi Imager

Get [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your
computer and insert an SD card (8GB or more, a name-brand "A1" card is
worth it).

1. **Device:** pick your board. For the original Pi Zero W choose
   *Raspberry Pi Zero*.
2. **Operating system:** *Raspberry Pi OS (other)* &rarr;
   **Raspberry Pi OS Lite (32-bit)**.
   - The Pi Zero W (v1) has an ARMv6 CPU. It can only run the **32-bit**
     image. 64-bit images will not boot.
   - Lite = no desktop. The kiln controller is used from your phone or
     computer's browser, so a desktop only wastes the Zero's 512MB of memory.
3. **Storage:** your SD card.
4. When Imager offers to **customise the OS** (Imager 1.8: *Edit settings*,
   Imager 2.x: the *Customisation* step), fill in:
   - **Hostname:** `kiln` &rarr; you will reach it as `http://kiln.local:8081`
   - **Username and password:** anything you like. There is no default `pi`
     user any more; the installer works with whatever user you create.
   - **Wi-Fi:** your network name and password, and your wireless country.
     The Pi Zero W only does **2.4 GHz** Wi-Fi.
   - **Locale / time zone:** set your real time zone. Delayed starts
     ("start at 5:00 am") use the Pi's clock.
   - **SSH:** enable it (password or public key).
5. Write the card, put it in the Pi and power it up. The first boot takes a
   few minutes on a Zero.

> **Power supply:** use a proper 5V 2.5A supply. Under-voltage is the most
> common cause of a Pi Zero randomly rebooting or corrupting its SD card.
> The controller shows under-voltage warnings in *Settings &rarr; Diagnostics*.

## 2. Run the installer

From your computer:

    ssh <your-user>@kiln.local

then on the Pi:

    curl -sSL https://raw.githubusercontent.com/adman234/kiln-controller/main/install.sh | bash

or, if you prefer to look before you run:

    sudo apt-get install -y git
    git clone https://github.com/adman234/kiln-controller
    cd kiln-controller
    ./install.sh

The installer:

- installs the system packages (gevent comes from apt so nothing heavy is
  compiled on the Zero, the rest comes as pre-built wheels from piwheels)
- enables SPI and I2C and adds your user to the `gpio`, `spi`, `i2c` groups
- creates a Python virtual environment in `kiln-controller/venv`
- installs a systemd service that starts on boot, restarts on crashes and
  forces the relay **off** before starting and after stopping
- caps the system log at 50MB and turns off Wi-Fi power saving (which makes
  a Zero's web page very sluggish)

Reboot once if it tells you SPI was just enabled: `sudo reboot`.

To update later, run `./install.sh` again from the `kiln-controller`
directory. Your settings and schedules (in `storage/`) are kept.

## 3. Set it up in the browser

Open `http://kiln.local:8081` (or the Pi's IP address, port 8081).

The controller starts in **simulation mode** so nothing heats up until you
say so. You can try everything (start a firing, autotune, ...) safely.

When the hardware is wired:

1. **Settings &rarr; Sensor:** choose your board (MAX31855, MAX31856,
   MAX6675, MCP9600 or MAX31865) and thermocouple type.
2. **Settings &rarr; Hardware:** check the pins (BCM numbers) and SPI mode.
3. **Settings &rarr; Advanced:** untick *Simulation mode*, save, and click
   *Restart controller*.
4. **Settings &rarr; Diagnostics:** the temperature should read room
   temperature and change when you warm the thermocouple. *Test relay*
   clicks the relay for a couple of seconds.
5. **Settings &rarr; General:** set your element power (kW) and electricity
   price for cost estimates, and &deg;C or &deg;F (also the toggle at the top).
6. **Settings &rarr; PID &amp; Autotune:** with an empty kiln, run autotune
   and click *Use* on the recommended values.

## Handy commands

    journalctl -u kiln-controller -f        # live log
    sudo systemctl restart kiln-controller  # restart
    sudo systemctl stop kiln-controller     # stop (relay is forced off)
    ./test-thermocouple.py                  # read the sensor from the shell (stop the service first)
    ./test-output.py                        # toggle the relay output (stop the service first)

## Notes for the Pi Zero W

- Everything runs fine on the Zero: the controller uses a few percent of CPU
  and well under 50MB of memory.
- Use the 32-bit Lite image (see above).
- If the page is slow to respond, check the Wi-Fi signal and the power supply
  first.
- Keep the Pi and the thermocouple board away from the kiln's heat and the
  relay's mains wiring.
