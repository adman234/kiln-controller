# Public status page

A read-only web page you can share outside your network, for example at
`https://kiln.example.com`. It shows the temperature, target, schedule
progress and time left, a graph of the firing, and any errors or alerts.
It has **no buttons and no settings**: it can't change anything on the kiln.

```
Internet ── (optional Cloudflare) ── router :443 ── Nginx Proxy Manager ──► Pi :8082  status page (read-only)
LAN only ────────────────────────────────────────────────────────────────► Pi :8081  kiln controller (full UI)
```

## How it stays safe and up

* **A separate program.** The controller starts it as a low-priority child
  process (`tools/status-server.py`). If a flood of visitors overloads it
  or it crashes, the control loop is unaffected and the controller restarts
  it 10 seconds later. The kiln never depends on it.
* **Read-only by construction.** It answers `GET` and `HEAD` for four
  files: `/`, `/status.css`, `/status.js` and `/status.json`. Everything
  else gets 404, and other methods get 405. It contains no code that can
  change the kiln, so even a misconfigured proxy can't reach a button.
* **The controller sees one request every 5 seconds, however many people
  are watching.** The status page fetches a snapshot over loopback
  (`/api/public-status`, which answers only to the Pi itself) and serves
  it from memory.
* **The snapshot has nothing private in it.** It holds the temperature,
  target, state, progress, schedule name (optional), alerts (optional) and
  a graph. It never includes settings, pins, passwords, alert URLs, IP
  addresses or costs.
* **Limits on visitors.** It allows 50 connections at once and drops
  clients that take more than 10 s to send a request. Browsers poll every
  10 s.
* **Offline detection.** If the controller stops answering, the page says
  so and keeps showing the last reading.

## 1. Turn it on

Go to **Settings → Advanced → Public status page** and tick *Public status
page*. You can also set:

* **Port:** default 8082.
* **Title:** shown at the top of the page.
* **Schedule name:** whether to show it.
* **Error details:** whether to show them. With this off, the page only
  says that the kiln needs attention.

Save, then restart the controller when asked. Check it from your own
network at `http://<pi-ip>:8082/`. **Settings → Diagnostics** shows
whether it's running.

## 2. Nginx Proxy Manager

1. Give the Pi a static DHCP lease on your router, if it doesn't have one
   already.
2. Add a DNS record for `kiln.yourdomain` that points at your public IP,
   the same way your other NPM hosts are set up.
3. On the NPM host, create the file `/data/nginx/custom/http_top.conf`
   (inside NPM's `data` folder). NPM includes it in its nginx `http {}`
   block:

   ```nginx
   proxy_cache_path /var/lib/nginx/cache/kiln keys_zone=kiln:1m max_size=10m inactive=10m use_temp_path=off;
   limit_req_zone  $binary_remote_addr zone=kiln_req:1m rate=2r/s;
   limit_conn_zone $binary_remote_addr zone=kiln_conn:1m;
   ```

4. **Hosts → Proxy Hosts → Add Proxy Host:**
   * **Details:**
     * Domain: `kiln.yourdomain`
     * Scheme: `http`
     * Forward host: the Pi's IP
     * Forward port: **8082**. Never use 8081.
     * Turn on *Block Common Exploits*.
     * Leave *Websockets Support* off.
   * **SSL:** request a Let's Encrypt certificate, and turn on *Force SSL*
     and *HTTP/2*.
   * **Advanced:** paste this:

     ```nginx
     # read-only: anything but GET/HEAD is refused before it reaches the Pi
     if ($request_method !~ ^(GET|HEAD)$) { return 405; }
     # per-visitor limits
     limit_req  zone=kiln_req burst=20 nodelay;
     limit_conn kiln_conn 10;
     # everyone shares one copy for 5 s: the Pi sees about 1 request per 5 s per file
     proxy_cache kiln;
     proxy_cache_valid 200 5s;
     proxy_cache_lock on;
     proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
     proxy_ignore_headers Cache-Control Expires Set-Cookie;
     ```

5. Open `https://kiln.yourdomain` from a phone on mobile data to test it.

If NPM refuses to save, the `http_top.conf` file is missing or has a typo.
Check NPM's logs.

## 3. Optional: Cloudflare in front (real DDoS protection)

NPM's limits protect the Pi, but a large flood can still fill your
internet connection. Cloudflare's free proxy absorbs that and hides your
home IP address:

1. Move the domain's DNS to Cloudflare, or use it there already, and turn
   the orange cloud (proxied) on for the `kiln` record.
2. **SSL/TLS mode:** *Full (strict)*. NPM already has a valid certificate.
3. **Caching → Cache Rules:** for `kiln.yourdomain`, cache everything with
   an edge TTL of 10 seconds.
4. **Real visitor IPs in NPM.** Behind Cloudflare, every request comes from
   a Cloudflare address, so the per-visitor limits above would treat all
   visitors as one. Add Cloudflare's address ranges to NPM with
   `set_real_ip_from` and `real_ip_header CF-Connecting-IP;`, or remove the
   two `limit_` lines and rely on Cloudflare's rate limiting.

Without Cloudflare, the worst a flood can do is make the status page (and
your internet connection) unreachable for a while. **The kiln keeps firing
either way.**

## Don'ts

* **Don't forward port 8081** (the full controller) to the internet, through
  NPM or your router.
* **Don't turn on websockets or long-lived connections** for this host. The
  page only polls.
