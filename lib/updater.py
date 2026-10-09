'''Software updates from the web UI.

Fetches a branch of a git repository (any fork), checks it out, installs
new Python requirements if requirements.txt changed, checks the new code
at least compiles and then restarts the service (systemd starts it again).
The commit that was running before is remembered so it can be rolled back.

Everything runs as the controller's own user, so system-level changes
(the systemd unit, apt packages) still need ./install.sh over SSH. The
update reports when those files changed.
'''
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time

from settings import BASE_DIR, STORAGE_DIR, atomic_write_json

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(STORAGE_DIR, "update.json")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9._~/-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
SYSTEM_FILES = ("install.sh", "lib/init/kiln-controller.service", "provision/vendor-data")


class UpdateError(Exception):
    pass


def validate(repo_url, branch):
    repo_url = (repo_url or "").strip()
    branch = (branch or "").strip()
    if not URL_RE.match(repo_url):
        raise UpdateError("repository must be an https:// git URL, e.g. https://github.com/adman234/kiln-controller")
    if not BRANCH_RE.match(branch) or ".." in branch or branch.startswith(("-", "/")) or branch.endswith((".lock", "/")):
        raise UpdateError("invalid branch name")
    return repo_url, branch


def file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


class Updater(object):
    def __init__(self, repo_dir=BASE_DIR, state_file=STATE_FILE, python=sys.executable, restart=None):
        self.repo_dir = repo_dir
        self.state_file = state_file
        self.python = python
        self.restart = restart          # called after a successful update
        self.lock = threading.Lock()
        self.busy = False
        self.status = "idle"            # idle, checking, checked, updating, done, error
        self.message = ""
        self.log_lines = []
        self.check_result = None

    # ------------------------------------------------------------------
    def git(self, *args, check=True, timeout=300):
        cmd = ["git", "-C", self.repo_dir] + list(args)
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except FileNotFoundError:
            raise UpdateError("git is not installed")
        except subprocess.TimeoutExpired:
            raise UpdateError("git %s timed out" % args[0])
        if check and p.returncode != 0:
            raise UpdateError("git %s failed: %s" % (args[0], (p.stderr or p.stdout).strip()[-500:]))
        return p.stdout.rstrip()

    def say(self, line):
        log.info("update: %s" % line)
        self.log_lines.append("%s  %s" % (time.strftime("%H:%M:%S"), line))
        self.log_lines = self.log_lines[-200:]

    def run_logged(self, cmd, timeout=3600):
        '''run a long command, streaming its output into the log'''
        self.say("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        p = subprocess.Popen(cmd, cwd=self.repo_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        start = time.time()
        for line in p.stdout:
            line = line.rstrip()
            if line:
                self.say(line[-300:])
            if time.time() - start > timeout:
                p.kill()
                raise UpdateError("%s took too long" % os.path.basename(cmd[0]))
        if p.wait() != 0:
            raise UpdateError("%s failed (exit %d)" % (" ".join(cmd[:3]), p.returncode))

    def read_state(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def version(self):
        '''what is running now'''
        try:
            sha = self.git("rev-parse", "HEAD")
            info = self.git("log", "-1", "--format=%h%x00%cs%x00%s").split("\x00")
            branch = self.git("rev-parse", "--abbrev-ref", "HEAD", check=False)
            remote = self.git("remote", "get-url", "origin", check=False)
            dirty = [l[3:] for l in self.git("status", "--porcelain", "--untracked-files=no").splitlines()]
        except UpdateError as e:
            return {"error": str(e)}
        return {"commit": sha, "short": info[0], "date": info[1] if len(info) > 1 else "",
                "subject": info[2] if len(info) > 2 else "", "branch": branch, "remote": remote,
                "local_changes": dirty}

    def info(self):
        state = self.read_state()
        return {"version": self.version(), "status": self.status, "message": self.message,
                "busy": self.busy, "log": self.log_lines[-60:], "check": self.check_result,
                "previous": state.get("previous"), "last_update": state.get("last_update")}

    # ------------------------------------------------------------------
    def _start(self, status, fn, *args):
        with self.lock:
            if self.busy:
                raise UpdateError("an update is already running")
            self.busy = True
            self.status = status
            self.message = ""
            self.log_lines = []
        t = threading.Thread(target=self._run, args=(fn,) + args)
        t.daemon = True
        t.start()

    def _run(self, fn, *args):
        try:
            fn(*args)
        except UpdateError as e:
            self.status, self.message = "error", str(e)
            self.say("ERROR: %s" % e)
        except Exception as e:
            log.exception("update failed")
            self.status, self.message = "error", "unexpected error: %r" % (e,)
            self.say("ERROR: %r" % (e,))
        finally:
            self.busy = False

    def check(self, repo_url, branch, wait=False):
        repo_url, branch = validate(repo_url, branch)
        if wait:
            self._check(repo_url, branch)
            return self.check_result
        self._start("checking", self._check, repo_url, branch)

    def _check(self, repo_url, branch):
        self.check_result = None
        self.say("fetching %s %s" % (repo_url, branch))
        self.git("fetch", "--quiet", repo_url, "+refs/heads/%s" % branch, timeout=600)
        new = self.git("rev-parse", "FETCH_HEAD")
        head = self.git("rev-parse", "HEAD")
        commits = []
        if new != head:
            out = self.git("log", "--format=%h%x00%cs%x00%s", "-n", "30", "HEAD..FETCH_HEAD", check=False)
            commits = [dict(zip(("short", "date", "subject"), l.split("\x00"))) for l in out.splitlines() if l]
        try:
            self.git("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
            fast_forward = True
        except UpdateError:
            fast_forward = False
        self.check_result = {"repo_url": repo_url, "branch": branch, "commit": new, "short": new[:7],
                             "up_to_date": new == head, "commits": commits, "fast_forward": fast_forward,
                             "time": time.time()}
        try:
            self.git("merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD")
            behind = new != head
        except UpdateError:
            behind = False
        self.check_result["older"] = behind
        if new == head:
            self.message = "Already running the latest %s." % branch
        elif behind:
            self.message = "What is running is newer than %s. Installing would go back to an older version." % branch
        elif fast_forward:
            self.message = "%d new change(s) available." % len(commits)
        else:
            self.message = "This branch is not a newer version of what is running (different history). Installing switches to it."
        self.status = "checked"
        self.say(self.message)

    def install(self, repo_url, branch, force=False, wait=False):
        repo_url, branch = validate(repo_url, branch)
        if wait:
            self._install(repo_url, branch, force)
            return
        self._start("updating", self._install, repo_url, branch, force)

    def rollback(self, wait=False):
        prev = self.read_state().get("previous")
        if not prev:
            raise UpdateError("nothing to roll back to")
        if wait:
            self._rollback(prev)
            return
        self._start("updating", self._rollback, prev)

    # ------------------------------------------------------------------
    def _snapshot(self):
        return {f: file_hash(os.path.join(self.repo_dir, f)) for f in ("requirements.txt",) + SYSTEM_FILES}

    def _install(self, repo_url, branch, force):
        dirty = self.version().get("local_changes") or []
        if dirty and not force:
            raise UpdateError("these files were changed on the kiln and would be overwritten: %s. "
                              "Tick 'overwrite local changes' to update anyway." % ", ".join(dirty))
        old_sha = self.git("rev-parse", "HEAD")
        old_branch = self.git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        old_remote = self.git("remote", "get-url", "origin", check=False)
        before = self._snapshot()

        self.say("fetching %s %s" % (repo_url, branch))
        if old_remote:
            self.git("remote", "set-url", "origin", repo_url)
        else:
            self.git("remote", "add", "origin", repo_url)
        try:
            self.git("fetch", "--quiet", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (branch, branch), timeout=600)
        except UpdateError:
            if old_remote:
                self.git("remote", "set-url", "origin", old_remote, check=False)
            raise
        new_sha = self.git("rev-parse", "refs/remotes/origin/%s" % branch)
        if dirty:
            self.say("discarding local changes: %s" % ", ".join(dirty))
            self.git("reset", "--hard", "--quiet")
        self.say("switching to %s (%s)" % (branch, new_sha[:7]))
        self.git("checkout", "--quiet", "-B", branch, "refs/remotes/origin/%s" % branch)
        self.git("branch", "--quiet", "--set-upstream-to=origin/%s" % branch, branch, check=False)

        try:
            self._finish(before, old_sha)
        except UpdateError as e:
            self.say("update failed, going back to %s" % old_sha[:7])
            self.git("checkout", "--quiet", "-B", old_branch if old_branch not in ("", "HEAD") else branch, old_sha, check=False)
            if old_remote:
                self.git("remote", "set-url", "origin", old_remote, check=False)
            raise UpdateError("%s. Nothing was changed." % e)

        atomic_write_json(self.state_file, {"previous": {"commit": old_sha, "branch": old_branch, "remote": old_remote},
                                            "last_update": {"commit": new_sha, "branch": branch, "remote": repo_url,
                                                            "time": time.time()}})
        self._done(before, new_sha)

    def _rollback(self, prev):
        before = self._snapshot()
        cur = self.git("rev-parse", "HEAD")
        branch = prev.get("branch") or "main"
        if branch == "HEAD":
            branch = "main"
        self.say("rolling back to %s" % prev["commit"][:7])
        if prev.get("remote"):
            self.git("remote", "set-url", "origin", prev["remote"], check=False)
        self.git("reset", "--hard", "--quiet")
        self.git("checkout", "--quiet", "-B", branch, prev["commit"])
        self._finish(before, cur)
        atomic_write_json(self.state_file, {"previous": None,
                                            "last_update": {"commit": prev["commit"], "branch": branch,
                                                            "remote": prev.get("remote"), "time": time.time(),
                                                            "rollback": True}})
        self._done(before, prev["commit"])

    def _finish(self, before, old_sha):
        '''install requirements if they changed and make sure the code compiles'''
        after = self._snapshot()
        if after["requirements.txt"] != before["requirements.txt"]:
            self.say("requirements.txt changed, installing Python packages (slow on a Pi Zero)")
            self.run_logged([self.python, "-m", "pip", "install", "-r", "requirements.txt"])
        self.say("checking the new code")
        files = ["kiln-controller.py"] + [os.path.join("lib", f) for f in sorted(os.listdir(os.path.join(self.repo_dir, "lib")))
                                          if f.endswith(".py")]
        p = subprocess.run([self.python, "-m", "py_compile"] + files, cwd=self.repo_dir,
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise UpdateError("the new code does not compile: %s" % (p.stderr or p.stdout).strip()[-400:])

    def _done(self, before, sha):
        after = self._snapshot()
        changed = [f for f in SYSTEM_FILES if after.get(f) != before.get(f)]
        self.status = "done"
        self.message = "Updated to %s. Restarting the controller." % sha[:7]
        if changed:
            self.message += (" System files changed (%s): run ./install.sh over SSH once to apply those." %
                             ", ".join(changed))
        self.say(self.message)
        if self.restart:
            self.restart()
