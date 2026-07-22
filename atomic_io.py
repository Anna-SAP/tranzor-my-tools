"""Cross-process-safe helpers for persistent text and JSON files.

Several Exporter features share user-level configuration between app
instances. A fixed ``file.json.tmp`` name is unsafe: two processes can open
and replace the same temporary path, causing corruption or a spurious
``FileNotFoundError``. ``mkstemp`` gives every write a unique sibling file;
``os.replace`` then publishes a complete file atomically.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Optional


_REPLACE_ATTEMPTS = 12
_REPLACE_BASE_DELAY_SEC = 0.002


def _replace_with_retry(source: str, target: str) -> None:
    """Publish a file despite brief Windows rename contention."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(
                min(
                    _REPLACE_BASE_DELAY_SEC * (2 ** attempt),
                    0.05,
                )
            )


def atomic_write_text(
    path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: Optional[int] = None,
) -> None:
    """Atomically replace *path* with *text* using a unique sibling temp."""
    target = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(target)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.",
        suffix=".tmp",
        dir=parent,
        text=True,
    )
    try:
        handle = os.fdopen(fd, "w", encoding=encoding)
        fd = None
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(temp_path, mode)
            except (OSError, NotImplementedError):
                pass
        _replace_with_retry(temp_path, target)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def atomic_write_json(
    path,
    payload: Any,
    *,
    ensure_ascii: bool = True,
    indent: Optional[int] = None,
    sort_keys: bool = False,
    mode: Optional[int] = None,
) -> None:
    """Serialize *payload* and atomically replace *path*."""
    text = json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
        sort_keys=sort_keys,
    )
    atomic_write_text(path, text, mode=mode)
