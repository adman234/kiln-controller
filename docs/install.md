Installing on a Raspberry Pi (including the original Pi Zero W)
==============================================================

There are two ways to install. Both start the same way, with Raspberry Pi
Imager.

| | Zero-touch (recommended) | Manual |
| --- | --- | --- |
| You need | a computer with an SD card reader | the same, plus SSH |
| Keyboard / screen on the Pi | no | no |
| SSH | no | yes, to run one command |
| Works with | Raspberry Pi OS **Trixie** (the current release, which uses cloud-init) | Trixie or Bookworm |
| Time on a Pi Zero W | 20&ndash;45 minutes, unattended | about the same |

---

## Step 1: Flash the SD card with Raspberry Pi Imager

Get [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (version 2 or
later) and insert an SD card (8GB or more; a name-brand "A1" card is worth it).

1. **Device:** your board. For the original Pi Zero W choose *Raspberry Pi Zero*.
2. **Operating system:** *Raspberry Pi OS (other)* &rarr;
   **Raspberry Pi OS Lite (32-bit)**.
   - The Pi Zero W (v1) has an ARMv6 CPU and can only run the **32-bit** image.
   - Lite = no desktop. You use the kiln controller from a phone or computer
     browser, and a desktop only wastes the Zero's 512MB of memory.
3. **Storage:** your SD card.
4. **Customisation** (do not skip this):
   - **Hostname:** `kiln` &rarr; you will reach it as `http://kiln.local:8081`
   - **User name and password:** anything you like. The installer uses this user.
   - **Wi-Fi:** network name, password and your country. The Pi Zero W only
     does **2.4 GHz** Wi-Fi.
   - **Time zone:** your real time zone. Delayed starts ("start at 5:00 am")
     use the Pi's clock.
   - **SSH:** turn it on even for the zero-touch install. You won't need it,
     but it is handy if anything ever goes wrong.
5. Write the card.

> **Power supply:** use a proper 5V 2.5A supply. Under-voltage is the most
> common cause of a Pi Zero rebooting at random or corrupting its SD card.
> The controller shows under-voltage warnings in *Settings &rarr; Diagnostics*.

---

## Step 2a: Zero-touch install

When Imager has finished, take the SD card out and put it back in your
computer. A drive called **bootfs** appears.

1. Download these from the repository's `provision` folder:
   - [`vendor-data`](../provision/vendor-data) (required)
   - [`kiln-install.conf`](../provision/kiln-install.conf) (optional: install options)
   - [`kiln-settings.example.json`](../provision/kiln-settings.example.json)
     (optional: sensor, pins, units, alerts)

   On GitHub open each file and use *Download raw file*.

2. Copy `vendor-data` onto the **bootfs** drive, next to the `user-data` and
   `network-config` files Imager wrote. The name must be exactly
   `vendor-data`: no `.txt` on the end. (On Windows, turn on *File name
   extensions* in Explorer's View menu to check.)

3. Optional: edit `kiln-install.conf` and copy it to **bootfs** too. The
   defaults are fine for most people. It sets which repository and branch to
   install, SD card protection (on by default) and Tailscale remote access.

4. Optional: rename `kiln-settings.example.json` to `kiln-settings.json`,
   edit it (delete the lines you don't need) and copy it to **bootfs**. This
   pre-sets anything you can also set later in the web UI, for example your
   sensor board, pins, &deg;F, and turning simulation off.

   > Use a plain text editor and keep the file's Unix line endings for
   > `vendor-data`. Don't open `vendor-data` in Notepad and save it.

5. Eject the card, put it in the Pi and power it up.

6. After a few minutes open **http://kiln.local:8081** on your phone or
   computer (same Wi-Fi). You will see an *Installing the kiln
   controller...* page with live progress. When it is done the Pi reboots
   and, a couple of minutes later, the kiln controller appears at the same
   address.

If anything goes wrong, the progress page says so and the full log is in
`kiln-install.log` on the **bootfs** drive: put the card back in your
computer to read it. If the install could not finish (usually no network),
it tries again every time the Pi boots, so after fixing the problem just
power-cycle the Pi.

### Wi-Fi troubleshooting

If the Pi never shows up on your network (no progress page, `kiln.local`
not found, no SSH):

- **Is there a `kiln-install.log` on bootfs?** If yes, the Pi booted and ran
  the installer: the log ends with network diagnostics showing what the
  Wi-Fi did. If there is no log, the Pi never got that far: check the image
  is *Raspberry Pi OS Lite (32-bit)* (current, not "Legacy"), and that the
  file is named exactly `vendor-data`.
- **2.4 GHz only.** The Pi Zero W cannot see 5 GHz networks. On mesh routers
  with one combined name this is usually fine; if not, enable a 2.4 GHz-only
  network.
- **WPA2, not WPA3-only.** The Pi Zero W's Wi-Fi chip does not support WPA3.
  "WPA2/WPA3" mixed mode works; "WPA3 only" does not.
- **Wi-Fi country** must be set in Imager's customisation, or the radio stays
  switched off.
- **Name and password are case sensitive.** Re-type them in Imager rather than
  pasting, and flash again.
- **`kiln.local` doesn't resolve** on some networks and older Windows: find the
  Pi's IP address in your router's device list and use `http://<ip>:8081`.
- Give it time: the very first boot of a Pi Zero takes a few minutes before
  Wi-Fi comes up.

### How it works

Raspberry Pi OS (Trixie) uses **cloud-init** to apply Imager's settings on
first boot. cloud-init reads its files from the boot partition: Imager writes
`user-data`, `network-config` and `meta-data`, and cloud-init also reads an
optional `vendor-data` file. Ours is a shell script, so it never clashes with
Imager's settings.

cloud-init runs vendor scripts *before* Imager's own first-boot commands
(turning on SSH, setting up sudo, unblocking Wi-Fi in some setups), so our
script only installs a one-shot `kiln-firstboot` service and exits at once.
That service starts after cloud-init has finished: it waits for the network
and the clock, clones the repository, runs `install.sh` as root on behalf of
your user, and reboots. It retries on every boot until it succeeds.

Older Raspberry Pi OS **Bookworm** images do not use cloud-init, so
`vendor-data` is ignored there. Use the manual install.

---

## Step 2b: Manual install

Boot the card, then from your computer:

    ssh <your-user>@kiln.local

and on the Pi:

    curl -sSL https://raw.githubusercontent.com/adman234/kiln-controller/main/install.sh | bash -s -- --protect-sd

or, if you prefer to look before you run:

    sudo apt-get install -y git
    git clone https://github.com/adman234/kiln-controller
    cd kiln-controller
    ./install.sh --protect-sd

Options:

| Option | What it does |
| --- | --- |
| `--protect-sd` | Fewer SD card writes: system log and `/tmp` in RAM, no swap file, no nightly apt jobs. Logs are lost on reboot. Recommended on a Pi Zero. |
| `--tailscale` | Installs [Tailscale](remote-access.md) for safe remote access. Run `sudo tailscale up` afterwards. |
| `--settings FILE` | Applies a `kiln-settings.json` (same format as above). |
| `--no-start` | Install but don't start the service yet. |
| `--branch NAME` | Install a different branch. |

Reboot once if SPI was just enabled: `sudo reboot`.

---

## What the installer sets up

- System packages. `gevent` comes from apt so nothing heavy is compiled on the
  Zero; the rest are pre-built wheels from piwheels.
- SPI and I2C enabled, your user added to the `gpio`, `spi`, `i2c` and
  `video` groups.
- A Python virtual environment in `kiln-controller/venv`.
- The `kiln-controller` systemd service, running as your user (not root). It:
  - starts on boot and restarts after a crash,
  - forces the relay **off** before starting and after stopping or crashing,
  - has a software watchdog: if the control loop hangs for 90 seconds,
    systemd restarts it (relay forced off in between).
- The Pi's **hardware watchdog**: if the whole Pi locks up it reboots by
  itself after 15 seconds.
- System log capped at 50MB (or kept in RAM with `--protect-sd`).
- Wi-Fi power saving off (it makes a Zero's web page very sluggish).

To update later, run `./install.sh` again from the `kiln-controller`
directory, with the same options. Settings and schedules (in `storage/`)
are kept. Use *Settings &rarr; Advanced &rarr; Download backup* before big
changes.

---

## Step 3: Set it up in the browser

Open `http://kiln.local:8081` (or the Pi's IP address, port 8081). On a
phone, use *Add to Home Screen* for an app-like icon.

Unless your `kiln-settings.json` said otherwise, the controller starts in
**simulation mode** so nothing heats up until you say so. You can try
everything safely.

When the hardware is wired:

1. **Settings &rarr; Sensor:** your board (MAX31855, MAX31856, MAX6675,
   MCP9600 or MAX31865) and thermocouple type.
2. **Settings &rarr; Hardware:** check the pins (BCM numbers) and SPI mode.
3. **Settings &rarr; Advanced:** untick *Simulation mode*, save, and click
   *Restart controller*.
4. **Settings &rarr; Diagnostics:** the temperature should read room
   temperature and change when you warm the thermocouple. *Test relay*
   clicks the relay for a couple of seconds.
5. **Settings &rarr; Safety &amp; Alerts:** set up phone alerts and send a test.
   Read [safety.md](safety.md) about the safety contactor.
6. **Settings &rarr; General:** element power (kW) and electricity price.
7. **Settings &rarr; PID &amp; Autotune:** with an empty kiln, run autotune and
   click *Use* on the recommended values.
8. **Settings &rarr; Advanced &rarr; Download backup** once it's all set.

## Handy commands

    journalctl -u kiln-controller -f        # live log
    sudo systemctl restart kiln-controller  # restart
    sudo systemctl stop kiln-controller     # stop (relay is forced off)
    ./test-thermocouple.py                  # read the sensor from the shell (stop the service first)
    ./test-output.py                        # toggle the relay output (stop the service first)
    ./ziplogs                               # bundle logs to send to someone

## SD card protection, in more detail

`--protect-sd` (on by default in the zero-touch install) removes the
everyday writes: the system log and `/tmp` go to RAM, the swap file on the
card is turned off, and the nightly apt and man-db jobs are disabled. The
controller itself writes very little: the power-failure restart point every
30 seconds during a firing, one short history line per firing, and settings
or schedules when you change them. All of those are written to a temporary
file and renamed, so a power cut leaves either the old or the new version,
never a half-written file.

For a fully read-only system you can additionally enable Raspberry Pi OS's
overlay file system (`sudo raspi-config` &rarr; *Performance* &rarr;
*Overlay file system*). **Don't** do this unless you understand the
trade-off: every change after that, including your settings, schedules,
firing history and the power-failure restart point, is lost at the next
reboot. Turn the overlay off, make changes, and turn it back on.

## Notes for the Pi Zero W

- Everything runs fine on the Zero: the controller uses a few percent of CPU
  and well under 50MB of memory.
- Use the 32-bit Lite image.
- If the page is slow, check the Wi-Fi signal and the power supply first.
- Keep the Pi and the thermocouple board away from the kiln's heat and from
  the relay's mains wiring.
