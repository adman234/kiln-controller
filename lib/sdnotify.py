'''Minimal systemd notify protocol (no dependency).

The service file uses Type=notify and WatchdogSec. The oven loop calls
watchdog() every cycle; if the loop ever hangs, systemd kills and
restarts the service and the ExecStopPost step forces the relay off.
Does nothing when not started by systemd.
'''
import os
import socket


def _send(msg):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
        return True
    except OSError:
        return False


def ready():
    return _send("READY=1")


def watchdog():
    return _send("WATCHDOG=1")


def status(text):
    return _send("STATUS=%s" % text)
