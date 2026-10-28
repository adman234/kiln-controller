#!/usr/bin/env python3
'''Public read-only status page server. The controller starts this as a
low-priority child process when Settings -> Advanced -> Public status page
is on. It can not change anything on the kiln: it only fetches a status
snapshot from the controller over loopback and serves it.

    tools/status-server.py --port 8082 --source http://127.0.0.1:8081/api/public-status
'''
from gevent import monkey
monkey.patch_all()

import argparse
import ctypes
import logging
import os
import signal
import sys
import urllib.request

import gevent
from gevent.pool import Pool
from gevent.pywsgi import WSGIServer, WSGIHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

from publicstatus import StatusApp  # noqa: E402

log = logging.getLogger("kiln-status")

MAX_CONNECTIONS = 50     # beyond this new connections wait; the controller is not affected
CLIENT_TIMEOUT = 10      # seconds a client may take to send its request


class TimeoutHandler(WSGIHandler):
    '''drop clients that open a connection and then dawdle (slowloris)'''
    def handle(self):
        self.socket.settimeout(CLIENT_TIMEOUT)
        return WSGIHandler.handle(self)


def die_with_parent():
    '''exit when the controller exits, so a stale server never holds the port'''
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def fetcher(app, source, interval):
    '''one request to the controller every interval, however many visitors'''
    while True:
        try:
            with urllib.request.urlopen(source, timeout=5) as r:
                app.update(r.read(256 * 1024))
        except Exception as e:
            log.warning("could not fetch status from the controller: %s" % e)
        gevent.sleep(interval)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=8082)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--source", default="http://127.0.0.1:8081/api/public-status")
    p.add_argument("--interval", type=float, default=5.0)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    die_with_parent()
    try:
        os.nice(10)   # the control loop always comes first
    except OSError:
        pass
    # no proxy settings from the environment for the loopback fetch
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    app = StatusApp()
    gevent.spawn(fetcher, app, a.source, a.interval)
    server = WSGIServer((a.host, a.port), app, spawn=Pool(MAX_CONNECTIONS), handler_class=TimeoutHandler, log=None)
    log.info("public status page on port %d" % a.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
