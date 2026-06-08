"""Platform authentication for the Tranzor Exporter.

Why this exists
---------------
The Tranzor platform API used to be open; it now enforces a Bearer-JWT
``Authorization`` header on every request (a server-side change — an
unauthenticated ``GET /api/v1/legacy/tasks`` returns ``401``). The desktop
app historically sent no credentials at all, so *every* data load broke
("Failed to load task data").

What this module does
---------------------
* Stores a JWT locally (``~/.tranzor_exporter_auth.json``) — **token only,
  never the password** (per the user's choice). Tokens are valid 7 days
  (server ``JWT_EXPIRE_HOURS=168``).
* Transparently attaches ``Authorization: Bearer <jwt>`` to every request
  aimed at a *platform* host by patching :meth:`requests.Session.request`
  **once**. This covers every module's own ``requests.Session`` *and*
  bare ``requests.get(...)`` calls without editing their call sites.
* Leaves non-platform hosts (e.g. GitLab) untouched via a host allowlist.
* Logs in against ``POST /api/v1/auth/login`` (LDAP email + password).

The injection decision lives in the pure :func:`apply_auth` so it is unit
testable without any real HTTP.
"""
from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlparse

try:
    import requests
except Exception:  # pragma: no cover - requests should always be present
    requests = None

# Token-only persistence. Separate file from gitlab_client's config so the
# two never clobber each other on save.
AUTH_CONFIG_PATH = os.path.expanduser("~/.tranzor_exporter_auth.json")

# Hosts that should receive the platform bearer token. Seeded with the known
# int host; ``configure_hosts`` lets the GUI add whatever TRANZOR_URL points
# at so this keeps working if the base URL ever changes.
PLATFORM_HOSTS = {"tranzor-platform.int.rclabenv.com"}

_token = None      # str | None — the raw JWT
_user = None       # dict | None — {email, name, is_language_lead}
_installed = False  # guard so the monkeypatch is applied at most once


# ---------------------------------------------------------------------------
# Host allowlist + token state
# ---------------------------------------------------------------------------
def configure_hosts(*hosts):
    """Add one or more hostnames to the platform allowlist (idempotent)."""
    for h in hosts:
        if h:
            PLATFORM_HOSTS.add(h.lower())


def _host_of(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def get_token():
    return _token


def get_user():
    return _user


def set_token(token, user=None, persist=True):
    global _token, _user
    _token = token or None
    if user is not None:
        _user = user
    if persist:
        _save()


def clear():
    """Forget the token in memory and on disk (used on explicit logout)."""
    global _token, _user
    _token = None
    _user = None
    try:
        if os.path.isfile(AUTH_CONFIG_PATH):
            os.remove(AUTH_CONFIG_PATH)
    except Exception:
        pass


def auth_header():
    """Headers dict carrying the bearer token (empty when not logged in)."""
    return {"Authorization": f"Bearer {_token}"} if _token else {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def load():
    """Restore a previously-saved token from disk into memory. Returns it."""
    global _token, _user
    try:
        if os.path.isfile(AUTH_CONFIG_PATH):
            with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _token = data.get("token") or None
            _user = data.get("user") or None
    except Exception:
        _token = None
        _user = None
    return _token


def _save():
    try:
        with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"token": _token, "user": _user}, f)
        # Best-effort tighten perms on POSIX; harmless/no-op on Windows.
        try:
            os.chmod(AUTH_CONFIG_PATH, 0o600)
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# JWT expiry — read-only decode, NO signature verification (the server is the
# real authority; we only want to know whether to prompt a re-login proactively
# instead of after a wasted 401).
# ---------------------------------------------------------------------------
def _decode_exp(token):
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def token_seconds_left(token=None):
    """Seconds until ``token`` (or the stored one) expires; None if unknown."""
    t = token if token is not None else _token
    if not t:
        return None
    exp = _decode_exp(t)
    if exp is None:
        return None
    return exp - time.time()


def has_valid_token(skew=60):
    """True if we hold a token that isn't provably expired.

    A token whose ``exp`` we cannot decode is treated as valid — let the
    server reject it with a 401 if it's actually bad. Only a token we can
    prove has expired (within ``skew`` seconds) returns False.
    """
    if not _token:
        return False
    left = token_seconds_left()
    if left is None:
        return True
    return left > skew


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def login(email, password, base_url, timeout=15):
    """POST /api/v1/auth/login and store the returned JWT.

    Returns ``(ok: bool, message: str)``. On success the token is persisted
    and ``message`` is "ok"; on failure nothing is stored and ``message``
    carries the server detail or a network error for display.
    """
    if requests is None:
        return False, "requests package not available"
    url = base_url.rstrip("/") + "/api/v1/auth/login"
    try:
        resp = requests.post(
            url, json={"email": email, "password": password}, timeout=timeout)
    except Exception as e:
        return False, f"Network error: {e}"

    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:
            return False, "Malformed login response"
        token = data.get("token")
        if not token:
            return False, "Login response contained no token"
        set_token(token, user=data.get("user"))
        return True, "ok"

    detail = ""
    try:
        detail = (resp.json() or {}).get("detail", "")
    except Exception:
        detail = (getattr(resp, "text", "") or "")[:200]
    return False, detail or f"HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# The global injection hook
# ---------------------------------------------------------------------------
def apply_auth(url, headers):
    """Return a headers dict for a request to ``url``.

    Adds ``Authorization: Bearer <token>`` when (a) we have a token and
    (b) the URL targets a platform host and (c) the caller hasn't already
    set an Authorization header. Pure function — the request patch and the
    tests both go through here.
    """
    headers = dict(headers or {})
    if _token and _host_of(url) in PLATFORM_HOSTS:
        if not any(str(k).lower() == "authorization" for k in headers):
            headers["Authorization"] = f"Bearer {_token}"
    return headers


def install():
    """Patch ``requests.Session.request`` once to inject the platform token.

    Idempotent. Patching the class method (not individual sessions) means
    every pre-existing module-level ``_session`` and every bare
    ``requests.get/post`` (which internally uses a Session) is covered.
    """
    global _installed
    if _installed or requests is None:
        return
    sessions = requests.sessions
    orig = sessions.Session.request

    def _request(self, method, url, **kwargs):
        try:
            kwargs["headers"] = apply_auth(url, kwargs.get("headers"))
        except Exception:
            pass  # never let auth plumbing break a request
        return orig(self, method, url, **kwargs)

    _request._tranzor_orig = orig  # keep a handle for debugging/tests
    sessions.Session.request = _request
    _installed = True
