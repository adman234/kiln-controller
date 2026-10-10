Checking on your kiln from away
===============================

**Never forward port 8081 on your router to the internet.** Anyone who
finds it could start your kiln. If the controller ever sees a visitor from
outside your home network while no password is set, it shows a red warning
on the main page.

Two safe options:

## Tailscale (recommended for the web page)

[Tailscale](https://tailscale.com) makes a private network between your own
devices. Your phone can open the kiln page from anywhere, and nobody else
can reach it. The personal plan is free.

1. Install with the controller: `./install.sh --tailscale` (or set
   `TAILSCALE=1` in `kiln-install.conf` for the zero-touch install; add an
   auth key there to join automatically).
2. On the Pi: `sudo tailscale up` and open the link it prints to log in.
3. Install the Tailscale app on your phone and log in with the same account.
4. Open `http://kiln:8081` (the Pi's Tailscale name) from anywhere.

## Raspberry Pi Connect (for a remote shell)

[Raspberry Pi Connect](https://www.raspberrypi.com/software/connect/) gives
you a remote terminal in your browser through Raspberry Pi's servers. On
Lite images install it with `sudo apt install rpi-connect-lite` and run
`rpi-connect signin`. Use it for maintenance (logs, updates); use Tailscale
for the kiln page itself.

## Also

- Set a web password: *Settings &rarr; Advanced &rarr; Web UI password*.
- Set up phone alerts (*Settings &rarr; Alerts*). Alerts work
  without any remote access: the controller sends them out.
