"""Named Project-filter presets for the MR Pipeline multi-select.

Saved in ``~/.tranzor_exporter_config.json`` under ``mr_project_presets``,
namespaced by env (``prod`` / ``stage``) so Stage test repos never leak into
production. Writes go through :func:`gitlab_client.update_config` and only
touch this one key — GitLab token / bridge port stay intact.

Pure list helpers below have no Tk / disk dependency and are unit-tested in
``test_project_presets.py``. The popup talks to disk only through
:func:`load_presets` / :func:`save_presets`.
"""
from __future__ import annotations

import time

PRESETS_KEY = "mr_project_presets"
MAX_PRESETS = 10
MAX_NAME_LEN = 24


def normalize_env(env_key) -> str:
    s = str(env_key or "prod").strip() or "prod"
    return s


def coerce_presets(raw) -> list[dict]:
    """Normalize a JSON blob into ``[{name, project_ids, updated_at}, ...]``.

    Drops nameless / empty entries, de-dupes ids, sorts most-recently-used
    first, and caps at :data:`MAX_PRESETS`.
    """
    if not isinstance(raw, list):
        return []
    out = []
    seen_names = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:MAX_NAME_LEN]
        if not name or name.lower() in seen_names:
            continue
        ids_raw = item.get("project_ids")
        if not isinstance(ids_raw, list):
            continue
        ids = []
        seen_ids = set()
        for x in ids_raw:
            s = str(x).strip()
            if not s or s in seen_ids:
                continue
            seen_ids.add(s)
            ids.append(s)
        if not ids:
            continue
        try:
            ts = float(item.get("updated_at") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        seen_names.add(name.lower())
        out.append({"name": name, "project_ids": ids, "updated_at": ts})
    out.sort(key=lambda p: p["updated_at"], reverse=True)
    return out[:MAX_PRESETS]


def matching_name(selected, presets) -> str | None:
    """Return the MRU preset whose id set equals ``selected``, else None.

    Empty selection (「全部」) never matches a preset.
    """
    sel = {str(s) for s in (selected or []) if str(s).strip()}
    if not sel:
        return None
    for preset in presets or []:
        ids = {str(x) for x in (preset.get("project_ids") or []) if str(x).strip()}
        if ids == sel:
            return str(preset.get("name") or "") or None
    return None


def apply_ids(preset_ids, all_options) -> list[str]:
    """Keep preset ids that still exist in ``all_options``, original order."""
    valid = {str(o) for o in (all_options or []) if str(o).strip()}
    out = []
    seen = set()
    for x in preset_ids or []:
        s = str(x).strip()
        if s in valid and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def find_preset(presets, name) -> dict | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for preset in presets or []:
        if str(preset.get("name") or "").strip().lower() == key:
            return preset
    return None


def upsert_preset(presets, name, project_ids, now=None):
    """Insert or replace a preset. Returns ``(presets, error)``.

    ``error`` is ``None`` on success, or ``empty_name`` / ``empty_ids`` /
    ``limit``. Replacing an existing name (case-insensitive) does not count
    toward the cap.
    """
    current = list(presets or [])
    cleaned = (name or "").strip()[:MAX_NAME_LEN]
    if not cleaned:
        return current, "empty_name"
    ids = []
    seen = set()
    for x in project_ids or []:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            ids.append(s)
    if not ids:
        return current, "empty_ids"
    rest = [p for p in current
            if str(p.get("name") or "").strip().lower() != cleaned.lower()]
    is_new = len(rest) == len(current)
    if is_new and len(current) >= MAX_PRESETS:
        return current, "limit"
    row = {
        "name": cleaned,
        "project_ids": ids,
        "updated_at": float(now if now is not None else time.time()),
    }
    return [row] + rest, None


def delete_preset(presets, name) -> list[dict]:
    key = (name or "").strip().lower()
    if not key:
        return list(presets or [])
    return [p for p in (presets or [])
            if str(p.get("name") or "").strip().lower() != key]


def rename_preset(presets, old_name, new_name, now=None):
    """Rename in place. Returns ``(presets, error)``.

    ``duplicate`` if the new name is already used by a *different* preset.
    """
    current = list(presets or [])
    old = (old_name or "").strip()
    new = (new_name or "").strip()[:MAX_NAME_LEN]
    if not new:
        return current, "empty_name"
    new_key = new.lower()
    old_key = old.lower()
    if new_key != old_key and find_preset(current, new) is not None:
        return current, "duplicate"
    out = []
    found = False
    ts = float(now if now is not None else time.time())
    for preset in current:
        if str(preset.get("name") or "").strip().lower() == old_key:
            row = dict(preset)
            row["name"] = new
            row["updated_at"] = ts
            out.append(row)
            found = True
        else:
            out.append(preset)
    if not found:
        return current, "missing"
    out.sort(key=lambda p: p.get("updated_at") or 0, reverse=True)
    return out, None


def touch_preset(presets, name, now=None) -> list[dict]:
    """Move ``name`` to the front (most recently used)."""
    current = list(presets or [])
    hit = find_preset(current, name)
    if hit is None:
        return current
    rest = delete_preset(current, name)
    row = dict(hit)
    row["updated_at"] = float(now if now is not None else time.time())
    return [row] + rest


def load_presets(env_key="prod") -> list[dict]:
    """Read presets for one env from the shared config file."""
    try:
        from gitlab_client import load_config
        cfg = load_config() or {}
    except Exception:
        return []
    blob = cfg.get(PRESETS_KEY)
    env = normalize_env(env_key)
    if isinstance(blob, list):
        raw = blob if env == "prod" else []
    elif isinstance(blob, dict):
        raw = blob.get(env) or []
    else:
        raw = []
    return coerce_presets(raw)


def save_presets(env_key, presets) -> None:
    """Merge-update only this env's preset list; other config keys stay."""
    from gitlab_client import load_config, update_config

    env = normalize_env(env_key)
    try:
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    blob = cfg.get(PRESETS_KEY)
    if isinstance(blob, list):
        stored = {"prod": blob}
    elif isinstance(blob, dict):
        stored = dict(blob)
    else:
        stored = {}
    stored[env] = coerce_presets(presets)
    update_config(**{PRESETS_KEY: stored})
