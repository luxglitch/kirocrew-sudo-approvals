"""Sudo Approvals app backend.

Owns a generic request/response FIFO pair. A wrapper writes a request block to
``SUDO_APPROVAL_REQUEST_FIFO`` and waits for ``y``/``n`` on
``SUDO_APPROVAL_RESPONSE_FIFO``. The dashboard can resolve a request manually
or arm a bounded auto-approval window.

The backend binds only to loopback, but loopback is not an authentication
boundary: every ``/api/*`` request must carry the gateway's signed
``X-KiroCrew-Proxy`` header. ``/health`` is intentionally unsigned for the
KiroCrew health probe.
"""
import getpass
import hashlib
import hmac
import json
import math
import os
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 9100))
APP_NAME = os.environ.get("KIROCREW_APP_NAME", "sudo-approvals")
PROXY_SECRET = os.environ.get("KIROCREW_PROXY_SECRET", "")
MAX_SKEW = 60

USER = os.environ.get("USER") or getpass.getuser()
USER_KEY = str(os.getuid()) if hasattr(os, "getuid") else USER
APP_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))


def installation_key(app_root: str) -> str:
    """Return a stable, non-secret namespace for one canonical app root."""
    canonical_root = os.path.realpath(os.fspath(app_root))
    return hashlib.sha256(os.fsencode(canonical_root)).hexdigest()[:12]


def default_runtime_dir(app_root: str = APP_ROOT, user_key: str = USER_KEY) -> str:
    """Keep separate KiroCrew homes owned by one Unix user off shared FIFOs."""
    return f"/tmp/sudo-approvals-{user_key}-{installation_key(app_root)}"


RUNTIME_DIR = os.environ.get("SUDO_APPROVAL_RUNTIME_DIR") or default_runtime_dir()
REQ = os.environ.get(
    "SUDO_APPROVAL_REQUEST_FIFO", os.path.join(RUNTIME_DIR, "request.fifo")
)
RESP = os.environ.get(
    "SUDO_APPROVAL_RESPONSE_FIFO", os.path.join(RUNTIME_DIR, "response.fifo")
)

AUTO_APPROVE_DURATIONS = (300, 900, 1800, 3600, 7200)

_lock = threading.Lock()
_pending = None  # {id, text, ts, event, decision, automatic}
_log = []  # recent resolved decisions, newest first
_auto_deadline = 0.0  # monotonic clock; authoritative for enforcement
_auto_expires_at = 0.0  # wall clock; display only


# --------------------------------------------------------------------------- #
# gateway proxy verification (CWE-306: loopback socket needs app-level auth)
# --------------------------------------------------------------------------- #
def verify_proxy(header_value: str, method: str, target: str, body: bytes) -> bool:
    """True iff ``header_value`` is a fresh valid gateway signature."""
    if not PROXY_SECRET or not header_value or ":" not in header_value:
        return False
    ts_str, _, sig = header_value.partition(":")
    if not ts_str.isdigit() or not sig:
        return False
    if abs(time.time() - int(ts_str)) > MAX_SKEW:
        return False
    body_hash = hashlib.sha256(body or b"").hexdigest()
    msg = f"{ts_str}:{method}:{target}:{body_hash}"
    expected = hmac.new(
        PROXY_SECRET.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# --------------------------------------------------------------------------- #
# bounded auto-approval window
# --------------------------------------------------------------------------- #
def _auto_state_unlocked() -> dict:
    """Return current window state. Caller must hold ``_lock``."""
    global _auto_deadline, _auto_expires_at
    remaining = _auto_deadline - time.monotonic()
    if remaining <= 0:
        _auto_deadline = 0.0
        _auto_expires_at = 0.0
        return {
            "active": False,
            "expiresAt": None,
            "remainingSeconds": 0,
            "allowedDurations": list(AUTO_APPROVE_DURATIONS),
        }
    return {
        "active": True,
        "expiresAt": int(_auto_expires_at * 1000),
        "remainingSeconds": int(math.ceil(remaining)),
        "allowedDurations": list(AUTO_APPROVE_DURATIONS),
    }


def auto_approve_state() -> dict:
    with _lock:
        return _auto_state_unlocked()


def arm_auto_approve(duration_seconds: int) -> dict:
    """Arm a fresh bounded window, rejecting every unapproved duration."""
    global _auto_deadline, _auto_expires_at
    if isinstance(duration_seconds, bool) or duration_seconds not in AUTO_APPROVE_DURATIONS:
        raise ValueError("unsupported duration")
    with _lock:
        _auto_deadline = time.monotonic() + duration_seconds
        _auto_expires_at = time.time() + duration_seconds
        return _auto_state_unlocked()


def disarm_auto_approve() -> dict:
    global _auto_deadline, _auto_expires_at
    with _lock:
        _auto_deadline = 0.0
        _auto_expires_at = 0.0
        return _auto_state_unlocked()


# --------------------------------------------------------------------------- #
# FIFO watcher
# --------------------------------------------------------------------------- #
def _ensure_fifo(path: str):
    """Create one owner-only FIFO or fail closed on an unsafe existing path."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if os.path.abspath(parent) == os.path.abspath(RUNTIME_DIR):
        os.chmod(parent, 0o700)
    try:
        os.mkfifo(path, 0o600)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISFIFO(info.st_mode):
        raise RuntimeError(f"refusing non-FIFO approval path: {path}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise RuntimeError(f"refusing FIFO not owned by current user: {path}")
    os.chmod(path, 0o600)


def ensure_fifos():
    _ensure_fifo(REQ)
    _ensure_fifo(RESP)


def reader_loop():
    """Publish each request and wait for a manual or automatic decision."""
    global _pending
    while True:
        try:
            with open(REQ, "r") as request_fifo:
                text = request_fifo.read()
        except OSError:
            time.sleep(0.5)
            continue
        if not text.strip():
            continue

        event = threading.Event()
        item = {
            "id": str(int(time.time() * 1000)),
            "text": text.strip(),
            "ts": time.strftime("%H:%M:%S"),
            "event": event,
            "decision": None,
            "automatic": False,
        }
        with _lock:
            _pending = item
            if _auto_state_unlocked()["active"]:
                item["decision"] = "y"
                item["automatic"] = True
                event.set()

        event.wait()
        reply = b"y\n" if item["decision"] == "y" else b"n\n"

        # A blocking FIFO open wedges forever when the requester has already
        # exited. Retry non-blocking for a bounded period, then continue.
        for _ in range(50):
            try:
                fd = os.open(RESP, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                time.sleep(0.1)
                continue
            try:
                os.write(fd, reply)
            finally:
                os.close(fd)
            break

        with _lock:
            _log.insert(
                0,
                {
                    "ts": item["ts"],
                    "text": item["text"],
                    "decision": item["decision"],
                    "automatic": item["automatic"],
                },
            )
            del _log[20:]
            _pending = None


def keepalive_loop():
    """Refresh an existing sudo timestamp; never prompts or elevates itself."""
    while True:
        os.system("sudo -n -v >/dev/null 2>&1")
        time.sleep(60)


def sudo_warm() -> bool:
    return os.system("sudo -n -v >/dev/null 2>&1") == 0


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _authed(self, body: bytes = b"") -> bool:
        return verify_proxy(
            self.headers.get("X-KiroCrew-Proxy", ""),
            self.command,
            self.path,
            body,
        )

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "app": APP_NAME})
            return
        if not self._authed():
            self._json(401, {"error": "unsigned request"})
            return
        if self.path == "/api/state":
            warm = sudo_warm()
            with _lock:
                pending = None
                if _pending and _pending["decision"] is None:
                    pending = {
                        "id": _pending["id"],
                        "text": _pending["text"],
                        "ts": _pending["ts"],
                    }
                out = {
                    "pending": pending,
                    "log": _log[:8],
                    "sudoWarm": warm,
                    "user": USER,
                    "autoApprove": _auto_state_unlocked(),
                }
            self._json(200, out)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        if not self._authed(body):
            self._json(401, {"error": "unsigned request"})
            return
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            self._json(400, {"error": "invalid JSON"})
            return

        if self.path == "/api/respond":
            request_id = str(data.get("id", ""))
            decision = "y" if data.get("decision") == "y" else "n"
            with _lock:
                if (
                    _pending
                    and _pending["id"] == request_id
                    and _pending["decision"] is None
                ):
                    _pending["decision"] = decision
                    _pending["automatic"] = False
                    _pending["event"].set()
                    ok = True
                else:
                    ok = False
            self._json(200, {"ok": ok})
            return

        if self.path == "/api/auto-approve":
            action = data.get("action")
            try:
                if action == "arm":
                    duration = data.get("durationSeconds")
                    if not isinstance(duration, int):
                        raise ValueError("durationSeconds must be an integer")
                    state = arm_auto_approve(duration)
                elif action == "disarm":
                    state = disarm_auto_approve()
                else:
                    raise ValueError("action must be arm or disarm")
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"ok": True, "autoApprove": state})
            return

        self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def main():
    ensure_fifos()
    threading.Thread(target=reader_loop, daemon=True).start()
    threading.Thread(target=keepalive_loop, daemon=True).start()
    print(
        f"{APP_NAME} backend on 127.0.0.1:{PORT} "
        f"(request fifo: {REQ}, response fifo: {RESP})",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
