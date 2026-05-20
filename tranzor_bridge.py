"""
Tranzor Bridge — local HTTP service that hands off Exporter selections to the
Tranzor Platform browser tab via a Tampermonkey userscript.

Design summary
--------------
- Loopback-only (127.0.0.1), token-gated, per-route Origin allowlist.
- Single-slot inbox: every /handoff replaces the previous envelope.
- Three endpoints:
    GET  /health              -> {ok, version, port, instance_id}   (public, no token)
    POST /handoff             <- {envelope}                         (token + file:// origin)
    GET  /pull?since=<seq>    -> {seq, envelope} | 204              (token + Tranzor origin)
- Port discovery file: ~/.tranzor_bridge/port.json (atomic write, chmod 600).

This module is intentionally stdlib-only so PyInstaller picks it up without
extra hooks. See userscript/tranzor_bridge.user.js for the client side.
"""

from __future__ import annotations

import atexit
import errno
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

BRIDGE_VERSION = "0.1.0"

BIND_HOST = "127.0.0.1"
PORT_RANGE = range(48217, 48227)  # 10 candidate ports

TRANZOR_ORIGIN = "http://tranzor-platform.int.rclabenv.com"
# file:// HTML reports send Origin: "null" on POST; some browsers send "file://".
REPORT_ORIGINS = frozenset({"null", "file://"})

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB envelope cap


def _state_dir() -> Path:
    return Path.home() / ".tranzor_bridge"


def _port_file() -> Path:
    return _state_dir() / "port.json"


class BridgePortBusy(RuntimeError):
    """All candidate ports already bound; bridge cannot start."""


class _RateLimiter:
    """Token-bucket per source token. Default 5 req/s, burst 10."""

    def __init__(self, rate_per_sec: float = 5.0, burst: int = 10):
        self.rate = rate_per_sec
        self.burst = burst
        self._buckets: dict = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True


class BridgeServer:
    """
    Bridge HTTP server lifecycle wrapper.

    Usage:
        bridge = BridgeServer()
        bridge.start()                # binds first free port in PORT_RANGE
        print(bridge.port, bridge.token, bridge.instance_id)
        # ...
        bridge.stop()                 # also called by atexit
    """

    def __init__(self, port_range=PORT_RANGE):
        self.port_range = list(port_range)
        self.token = secrets.token_urlsafe(32)
        self.instance_id = uuid.uuid4().hex[:12]
        self.port: Optional[int] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._inbox_lock = threading.Lock()
        self._inbox: Optional[dict] = None
        self._inbox_seq: int = 0
        self._rate = _RateLimiter()
        self._stopped = False
        self._atexit_registered = False

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> int:
        handler_cls = _make_handler(self)
        last_err: Optional[OSError] = None
        for port in self.port_range:
            try:
                srv = ThreadingHTTPServer((BIND_HOST, port), handler_cls)
            except OSError as e:
                last_err = e
                if e.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                    continue
                raise
            else:
                self._server = srv
                self.port = port
                break
        else:
            raise BridgePortBusy(
                f"all ports busy in {self.port_range[0]}..{self.port_range[-1]}: "
                f"{last_err!r}"
            )

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"tranzor-bridge-{self.port}",
            daemon=True,
        )
        self._thread.start()
        self._write_port_file()
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True
        return self.port

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        except Exception:
            pass
        self._delete_port_file()

    # ---- inbox -----------------------------------------------------------

    def push(self, envelope: dict) -> int:
        with self._inbox_lock:
            self._inbox = envelope
            self._inbox_seq += 1
            return self._inbox_seq

    def pull(self, since: int) -> Tuple[int, Optional[dict]]:
        with self._inbox_lock:
            if self._inbox is None or self._inbox_seq <= since:
                return self._inbox_seq, None
            return self._inbox_seq, self._inbox

    # ---- discovery file --------------------------------------------------

    def _write_port_file(self):
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except (OSError, NotImplementedError):
            pass

        path = _port_file()
        tmp = path.with_suffix(".json.tmp")
        payload = {
            "version": BRIDGE_VERSION,
            "port": self.port,
            "token": self.token,
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "started_at": time.time(),
        }
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except (OSError, NotImplementedError):
            pass
        os.replace(tmp, path)

    def _delete_port_file(self):
        try:
            _port_file().unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # ---- snapshot for HTML injection ------------------------------------

    def html_info(self) -> dict:
        """Subset of state safe to embed in generated HTML reports."""
        return {
            "port": self.port,
            "token": self.token,
            "instance_id": self.instance_id,
            "version": BRIDGE_VERSION,
        }


# ---- HTTP handler --------------------------------------------------------


def _make_handler(bridge: BridgeServer):
    class Handler(BaseHTTPRequestHandler):
        # Silence default access log; the GUI surfaces status itself.
        def log_message(self, fmt, *args):
            return

        # ---- header helpers ----
        def _cors_headers(self, allow_origin: str):
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, X-Bridge-Token"
            )
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")

        def _json(self, data, status=200, allow_origin: Optional[str] = None):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if allow_origin is not None:
                self._cors_headers(allow_origin)
            self.end_headers()
            self.wfile.write(body)

        def _no_content(self, allow_origin: str):
            self.send_response(204)
            self._cors_headers(allow_origin)
            self.end_headers()

        def _reject(self, status: int, reason: str):
            self._json({"error": reason}, status=status, allow_origin="*")

        def _origin(self) -> str:
            return self.headers.get("Origin", "")

        def _check_token(self) -> bool:
            got = self.headers.get("X-Bridge-Token", "")
            return secrets.compare_digest(got, bridge.token)

        def _read_body(self) -> Tuple[Optional[bytes], Optional[str]]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, "bad_content_length"
            if length <= 0:
                return None, "empty_body"
            if length > MAX_BODY_BYTES:
                return None, "body_too_large"
            try:
                return self.rfile.read(length), None
            except Exception:
                return None, "read_failed"

        # ---- preflight ----
        def do_OPTIONS(self):
            origin = self._origin() or "*"
            self.send_response(204)
            self._cors_headers(origin)
            self.end_headers()

        # ---- GET ----
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(
                    {
                        "ok": True,
                        "version": BRIDGE_VERSION,
                        "port": bridge.port,
                        "instance_id": bridge.instance_id,
                    },
                    allow_origin="*",
                )
                return

            if path == "/pull":
                origin = self._origin()
                if origin != TRANZOR_ORIGIN:
                    self._reject(403, "origin_not_allowed")
                    return
                if not self._check_token():
                    self._reject(401, "bad_token")
                    return
                if not bridge._rate.allow(bridge.token):
                    self._reject(429, "rate_limited")
                    return
                since = 0
                if "?" in self.path:
                    qs = self.path.split("?", 1)[1]
                    for kv in qs.split("&"):
                        if kv.startswith("since="):
                            try:
                                since = int(kv[len("since="):])
                            except ValueError:
                                since = 0
                seq, env = bridge.pull(since)
                if env is None:
                    self._no_content(origin)
                    return
                self._json({"seq": seq, "envelope": env}, allow_origin=origin)
                return

            self._reject(404, "not_found")

        # ---- POST ----
        def do_POST(self):
            path = self.path.split("?", 1)[0]
            origin = self._origin()

            if path == "/handoff":
                if origin not in REPORT_ORIGINS:
                    self._reject(403, "origin_not_allowed")
                    return
                if not self._check_token():
                    self._reject(401, "bad_token")
                    return
                if not bridge._rate.allow(bridge.token):
                    self._reject(429, "rate_limited")
                    return
                body, err = self._read_body()
                if err is not None:
                    status = 413 if err == "body_too_large" else 400
                    self._reject(status, err)
                    return
                try:
                    envelope = json.loads(body.decode("utf-8"))
                except Exception:
                    self._reject(400, "invalid_json")
                    return
                if not isinstance(envelope, dict) or "items" not in envelope:
                    self._reject(400, "invalid_envelope")
                    return
                seq = bridge.push(envelope)
                self._json({"ok": True, "seq": seq}, allow_origin=origin)
                return

            self._reject(404, "not_found")

    return Handler


# ---- module-level helpers -----------------------------------------------


def try_start_bridge() -> Tuple[Optional[BridgeServer], Optional[str]]:
    """
    Convenience helper used by the GUI. Returns (bridge, None) on success or
    (None, error_message) on a graceful failure (e.g. ports busy). Never
    raises BridgePortBusy.
    """
    bridge = BridgeServer()
    try:
        bridge.start()
    except BridgePortBusy as e:
        return None, str(e)
    except OSError as e:
        return None, f"bind failed: {e!r}"
    return bridge, None


if __name__ == "__main__":
    # CLI smoke harness — `python tranzor_bridge.py` blocks until Ctrl+C.
    b, err = try_start_bridge()
    if err:
        print(f"[bridge] startup failed: {err}")
        raise SystemExit(1)
    assert b is not None
    print(
        f"[bridge] listening on http://{BIND_HOST}:{b.port}  "
        f"instance_id={b.instance_id}"
    )
    print(f"[bridge] token (do not log in production): {b.token}")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        b.stop()
        print("\n[bridge] stopped")
