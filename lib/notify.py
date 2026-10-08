'''Push notifications: ntfy, Pushover or a generic webhook (Slack,
Discord, Home Assistant, ...).

Messages are sent from a background thread so a slow or dead network
never delays the control loop. Repeats of the same alert are suppressed
for a while so a stuck condition does not flood your phone.
'''
import json
import logging
import threading
import time
import urllib.parse
import urllib.request

from settings import settings

log = logging.getLogger(__name__)

REPEAT_SECONDS = 15 * 60


class Notifier(object):
    def __init__(self):
        self.last_sent = {}
        self.recent = []  # last few messages, shown in the UI
        self.lock = threading.Lock()

    def send(self, title, message, urgent=False, key=None, force=False):
        '''queue a notification. key de-duplicates repeats.'''
        key = key or title
        now = time.time()
        with self.lock:
            if not force and now - self.last_sent.get(key, 0) < REPEAT_SECONDS:
                return False
            self.last_sent[key] = now
            self.recent.append({"time": now, "title": title, "message": message, "urgent": urgent})
            self.recent = self.recent[-20:]
        log.warning("notify: %s - %s" % (title, message)) if urgent else log.info("notify: %s - %s" % (title, message))
        service = settings.get("notify_service", "none")
        if service in (None, "", "none"):
            return False
        t = threading.Thread(target=self._deliver, args=(service, title, message, urgent))
        t.daemon = True
        t.start()
        return True

    def _deliver(self, service, title, message, urgent):
        try:
            if service == "ntfy":
                self.ntfy(title, message, urgent)
            elif service == "pushover":
                self.pushover(title, message, urgent)
            elif service == "webhook":
                self.webhook(title, message, urgent)
        except Exception as e:
            log.error("notification via %s failed: %s" % (service, e))

    @staticmethod
    def _post(url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    def ntfy(self, title, message, urgent):
        url = settings.get("notify_url", "").strip()
        if not url:
            raise ValueError("no ntfy topic URL set")
        headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"),
                   "Priority": "urgent" if urgent else "default",
                   "Tags": "fire" if urgent else "kiln"}
        self._post(url, message.encode("utf-8"), headers)

    def pushover(self, title, message, urgent):
        data = urllib.parse.urlencode({
            "token": settings.get("pushover_token", ""),
            "user": settings.get("pushover_user", ""),
            "title": title, "message": message,
            "priority": 1 if urgent else 0,
        }).encode()
        self._post("https://api.pushover.net/1/messages.json", data,
                   {"Content-Type": "application/x-www-form-urlencoded"})

    def webhook(self, title, message, urgent):
        url = settings.get("notify_url", "").strip()
        if not url:
            raise ValueError("no webhook URL set")
        text = "%s%s: %s" % ("⚠️ " if urgent else "", title, message)
        # "text" for Slack/Mattermost, "content" for Discord, the rest for anything else
        body = json.dumps({"text": text, "content": text, "title": title,
                           "message": message, "urgent": urgent}).encode()
        self._post(url, body, {"Content-Type": "application/json"})

    def test(self):
        '''send synchronously so the UI can report errors'''
        service = settings.get("notify_service", "none")
        if service in (None, "", "none"):
            raise ValueError("choose a notification service first")
        title, message = "Kiln controller", "Test notification. If you can read this, alerts work."
        if service == "ntfy":
            self.ntfy(title, message, False)
        elif service == "pushover":
            self.pushover(title, message, False)
        else:
            self.webhook(title, message, False)


notifier = Notifier()
