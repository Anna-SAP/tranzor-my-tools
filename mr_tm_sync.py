"""MR / Git patch → Tranzor TM sync.

Locale-only Git fixes (LOC-24849, LOC-24914, LOC-25246) never pass through
Tranzor's MR pipeline, so Shared TM / ICE TM keep the stale pair. This
module:

1. Parses a GitLab MR or unified diff into translation pairs
2. Optionally probes ICE TM for the currently stored target
3. Submits corrections through the official Bug Fix write path
   (blob-based apply) — the Language-Lead HTTP API that upserts TM

Default is dry-run. Apply is opt-in: it writes TM **and** queues a
Tranzor Bug Fix MR (same contract as Quality → Bug Fix). Close that MR
without merge when Git already has the fix.

Pure logic; no tkinter. GUI lives in :mod:`gui_tab_mr_tm_sync`.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse

import gitlab_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SOURCE_LOCALE = "en-US"
DEFAULT_PLATFORM_URL = "http://tranzor-platform.int.rclabenv.com"
DEFAULT_ICE_HOST = (
    os.environ.get("TRANZOR_CONTEXT_SERVICE_HOST")
    or "http://l10n-context-service.int.rclabenv.com"
).rstrip("/")
ICE_MATCH_PATH = "/api/v1/translation-memory/match"
BLOB_LOOKUP_PATH = "/api/v1/blob-based-fix/lookup"
BLOB_APPLY_PATH = "/api/v1/blob-based-fix/apply"
BUG_FIX_LOOKUP_PATH = "/api/v1/bug-fix/lookup"
BUG_FIX_CREATE_MR_PATH = "/api/v1/bug-fix/create-mr"
ICE_DUMMY_OPUS_ID = (
    "RingCentral.probe.00000000000000000000000000000000.content"
)
BUG_FIX_TEXT_LIMIT = 20_000
ICE_CONTENT_ONLY_MIN_WORDS = 4
# Legacy /bug-fix/lookup rejects this project; blob-based is the write path.
LEGACY_BUG_FIX_UNSUPPORTED = frozenset({"common/uns"})

# Longest-first so es-419 wins over es, zh-CN over zh, etc.
_KNOWN_LOCALES: tuple[str, ...] = (
    "es-419", "zh-CN", "zh-TW", "zh-HK", "pt-BR", "pt-PT",
    "en-US", "en-GB", "en-AU", "fr-CA", "fr-FR", "de-DE",
    "es-ES", "it-IT", "nl-NL", "fi-FI", "ja-JP", "ko-KR", "bg-BG",
)
_LOCALE_TOKEN_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (loc, loc.replace("-", "_")) for loc in _KNOWN_LOCALES
)
_MR_URL_RE = re.compile(
    r"""
    ^https?://[^/]+/
    (?P<project>.+?)
    /-/merge_requests/
    (?P<iid>\d+)
    (?:/.*)?$
    """,
    re.VERBOSE,
)
_GIT_DIFF_PATH_RE = re.compile(
    r"^(?:diff --git a/(?P<a>.+?) b/(?P<b>.+)|"
    r"\+\+\+ b/(?P<plus>.+)|"
    r"--- a/(?P<minus>.+))$"
)
_EXPORT_DEFAULT_RE = re.compile(
    r"export\s+default\s+",
    re.MULTILINE,
)
_UNQUOTED_KEY_RE = re.compile(r"^[A-Za-z_$][\w$]*$")


GetFileRawFn = Callable[[str, str, str], str | None]
ListMrDiffsFn = Callable[..., list[dict[str, Any]]]
GetMrFn = Callable[..., Mapping[str, Any]]
PostFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@dataclass
class TranslationPair:
    project_id: str
    format: str                  # hbs | json | ts | properties | unknown
    target_path: str
    source_path: str
    logical_key: str
    string_key: str
    target_language: str
    source_text: str
    old_target_text: str
    new_target_text: str
    reconstructed_opus_id: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.old_target_text != self.new_target_text

    @property
    def oversized(self) -> bool:
        return len(self.new_target_text) > BUG_FIX_TEXT_LIMIT


@dataclass
class SyncPlan:
    source_kind: str             # mr | diff | git
    project_id: str
    mr_iid: int | None = None
    mr_url: str = ""
    source_branch: str = ""
    target_branch: str = ""
    head_sha: str = ""
    start_sha: str = ""
    mr_state: str = ""
    gitlab_base: str = gitlab_client.DEFAULT_BASE_URL
    pairs: list[TranslationPair] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def changed_pairs(self) -> list[TranslationPair]:
        return [p for p in self.pairs if p.changed]


@dataclass
class ProbeHit:
    pair: TranslationPair
    ice_target: str
    ice_match_type: str
    stale: bool
    error: str = ""


@dataclass
class ApplyBatchResult:
    source_path: str
    status: str
    submission_id: str = ""
    error: str = ""
    mr_url: str | None = None
    languages: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Locale / path helpers
# ---------------------------------------------------------------------------
def parse_mr_url(url: str) -> tuple[str, int]:
    text = (url or "").strip()
    match = _MR_URL_RE.match(text)
    if not match:
        raise ValueError(
            "Not a GitLab merge-request URL "
            "(expected https://<host>/<group>/<project>/-/merge_requests/<iid>)"
        )
    return match.group("project"), int(match.group("iid"))


def normalize_locale(token: str) -> str:
    raw = (token or "").strip().replace("_", "-")
    if not raw:
        return ""
    lower = raw.lower()
    for loc in _KNOWN_LOCALES:
        if loc.lower() == lower:
            return loc
    parts = raw.split("-")
    if len(parts) == 2:
        lang, region = parts
        if region.isdigit() or region.lower() == "419":
            return f"{lang.lower()}-{region}"
        return f"{lang.lower()}-{region.upper()}"
    return raw


def locale_from_filename(path: str) -> tuple[str, str] | None:
    """Return ``(bcp47, raw_token_in_filename)`` for a locale file path."""
    name = os.path.basename(path.replace("\\", "/"))
    candidates: list[tuple[int, str, str]] = []
    for bcp, underscored in _LOCALE_TOKEN_PAIRS:
        for token in (bcp, underscored):
            if token in name:
                candidates.append((len(token), bcp, token))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _length, bcp, token = candidates[0]
    return bcp, token


def source_path_for(path: str, source_locale: str = SOURCE_LOCALE) -> str | None:
    """Rewrite a target-locale path to the matching en-US source path."""
    found = locale_from_filename(path)
    if found is None:
        return None
    bcp, raw_token = found
    posix = path.replace("\\", "/")
    directory, name = posix.rsplit("/", 1) if "/" in posix else ("", posix)
    if raw_token not in name:
        return None
    if "-" in raw_token:
        replacement = source_locale
    else:
        replacement = source_locale.replace("-", "_")
    new_name = _replace_last(name, raw_token, replacement)
    return f"{directory}/{new_name}" if directory else new_name


def _replace_last(text: str, old: str, new: str) -> str:
    index = text.rfind(old)
    if index < 0:
        return text
    return text[:index] + new + text[index + len(old):]


def classify_format(path: str) -> str:
    lower = path.replace("\\", "/").lower()
    if lower.endswith(".hbs"):
        return "hbs"
    if lower.endswith(".properties"):
        return "properties"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".ts") or lower.endswith(".js"):
        return "ts"
    return "unknown"


def is_source_locale_path(path: str) -> bool:
    found = locale_from_filename(path)
    return bool(found) and found[0] == SOURCE_LOCALE


# ---------------------------------------------------------------------------
# File parsers
# ---------------------------------------------------------------------------
def parse_properties(text: str) -> dict[str, str]:
    try:
        import repo_corpus
        return dict(repo_corpus.parse_properties(text or ""))
    except Exception:
        return _parse_properties_fallback(text or "")


def _parse_properties_fallback(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped[0] in "#!":
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
        elif ":" in stripped:
            key, _, value = stripped.partition(":")
        else:
            continue
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def _flatten_json(value: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten_json(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            out.update(_flatten_json(child, path))
    elif value is None:
        return out
    else:
        out[prefix] = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False)
    return out


def parse_json_locale(text: str) -> dict[str, str]:
    if not (text or "").strip():
        return {}
    data = json.loads(text)
    if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()):
        return {str(k): v for k, v in data.items()}
    return _flatten_json(data)


def parse_ts_locale(text: str) -> dict[str, str]:
    """Parse a Service-Web ``export default { KEY: 'value' }`` locale file.

    Nested objects flatten to dotted keys. Functions / computed values are
    skipped. Raises ``ValueError`` when the object cannot be located.
    """
    body = text or ""
    match = _EXPORT_DEFAULT_RE.search(body)
    if not match:
        raise ValueError("no `export default` in TypeScript locale file")
    index = match.end()
    index = _skip_ts_trivia(body, index)
    if index >= len(body) or body[index] != "{":
        raise ValueError("export default is not an object literal")
    obj, _end = _parse_ts_object(body, index)
    return _flatten_json(obj)


def _skip_ts_trivia(text: str, index: int) -> int:
    n = len(text)
    while index < n:
        ch = text[index]
        if ch in " \t\r\n":
            index += 1
            continue
        if ch == "/" and index + 1 < n and text[index + 1] == "/":
            nl = text.find("\n", index)
            index = n if nl < 0 else nl + 1
            continue
        if ch == "/" and index + 1 < n and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            index = n if end < 0 else end + 2
            continue
        break
    return index


def _parse_ts_string(text: str, index: int) -> tuple[str, int]:
    quote = text[index]
    if quote not in "'\"`":
        raise ValueError("expected string")
    index += 1
    chars: list[str] = []
    n = len(text)
    while index < n:
        ch = text[index]
        if ch == "\\" and index + 1 < n:
            nxt = text[index + 1]
            escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                       "'": "'", '"': '"', "`": "`"}
            chars.append(escapes.get(nxt, nxt))
            index += 2
            continue
        if ch == quote:
            return "".join(chars), index + 1
        chars.append(ch)
        index += 1
    raise ValueError("unterminated string")


def _parse_ts_object(text: str, index: int) -> tuple[dict[str, Any], int]:
    assert text[index] == "{"
    index = _skip_ts_trivia(text, index + 1)
    out: dict[str, Any] = {}
    n = len(text)
    while index < n and text[index] != "}":
        index = _skip_ts_trivia(text, index)
        if index < n and text[index] == "}":
            break
        if text[index] in "'\"":
            key, index = _parse_ts_string(text, index)
        else:
            start = index
            while index < n and (text[index].isalnum() or text[index] in "_$"):
                index += 1
            key = text[start:index]
            if not key:
                raise ValueError(f"invalid object key at {start}")
        index = _skip_ts_trivia(text, index)
        if index >= n or text[index] != ":":
            raise ValueError(f"expected ':' after key {key!r}")
        index = _skip_ts_trivia(text, index + 1)
        value, index = _parse_ts_value(text, index)
        out[key] = value
        index = _skip_ts_trivia(text, index)
        if index < n and text[index] == ",":
            index = _skip_ts_trivia(text, index + 1)
    if index >= n or text[index] != "}":
        raise ValueError("unterminated object literal")
    return out, index + 1


def _parse_ts_value(text: str, index: int) -> tuple[Any, int]:
    index = _skip_ts_trivia(text, index)
    ch = text[index]
    if ch in "'\"`":
        return _parse_ts_string(text, index)
    if ch == "{":
        return _parse_ts_object(text, index)
    if ch == "[":
        # Skip arrays as a JSON-ish dump; flatten later if they contain strings.
        start = index
        depth = 1
        index += 1
        n = len(text)
        while index < n and depth:
            if text[index] in "'\"`":
                _s, index = _parse_ts_string(text, index)
                continue
            if text[index] == "[":
                depth += 1
            elif text[index] == "]":
                depth -= 1
            index += 1
        raw = text[start:index]
        try:
            return json.loads(raw), index
        except Exception:
            return raw, index
    start = index
    n = len(text)
    while index < n and text[index] not in ",}":
        index += 1
    token = text[start:index].strip()
    if token in ("true", "false"):
        return token == "true", index
    if token == "null":
        return None, index
    try:
        if "." in token:
            return float(token), index
        return int(token), index
    except ValueError:
        return token, index


def parse_locale_map(path: str, text: str) -> dict[str, str]:
    fmt = classify_format(path)
    if fmt == "hbs":
        return {"__file__": text or ""}
    if fmt == "json":
        return parse_json_locale(text or "{}")
    if fmt == "ts":
        return parse_ts_locale(text or "")
    if fmt == "properties":
        return parse_properties(text or "")
    raise ValueError(f"unsupported locale file format: {path}")


def parse_unified_diff_paths(diff_text: str) -> list[str]:
    """Collect ``b/`` paths from a unified diff (local ``git diff`` / patch)."""
    paths: list[str] = []
    seen: set[str] = set()
    for line in (diff_text or "").splitlines():
        match = _GIT_DIFF_PATH_RE.match(line)
        if not match:
            continue
        path = match.group("b") or match.group("plus") or match.group("a") or match.group("minus")
        if not path or path == "/dev/null" or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Pair extraction
# ---------------------------------------------------------------------------
def _product_for_project(project_id: str) -> dict[str, Any] | None:
    try:
        import repo_corpus
        products = repo_corpus.load_products()
    except Exception:
        return None
    needle = (project_id or "").strip().lower()
    for product in products.get("products") or []:
        gitlab = str(product.get("gitlab") or "").strip().lower()
        if gitlab == needle:
            return product
    return None


def _alias_for_project(project_id: str) -> str:
    product = _product_for_project(project_id)
    aliases = (product or {}).get("aliases") or []
    if aliases:
        return str(aliases[0])
    if "/" in (project_id or ""):
        return project_id.rsplit("/", 1)[-1]
    return project_id or "unknown"


def reconstruct_opus_id(project_id: str, source_path: str, logical_key: str) -> str:
    product = _product_for_project(project_id) or {}
    alias = _alias_for_project(project_id)
    strip = str(product.get("strip") or "")
    try:
        import opus_id_monitor as om
        path_hash = om.md5_path(source_path, strip)
    except Exception:
        hashed = source_path.replace(strip, "") if strip else source_path
        path_hash = hashlib.md5(hashed.encode("utf-8")).hexdigest()
    return f"RingCentral.{alias}.{path_hash}.{logical_key}"


def _logical_key_for(path: str, key: str, fmt: str) -> str:
    if fmt == "hbs":
        parsed = gitlab_client.parse_uns_template_path(path)
        if parsed:
            return parsed[0]
        stem = os.path.basename(path).rsplit(".", 1)[0]
        return stem
    return key


def _string_key_for(logical_key: str, fmt: str) -> str:
    # Blob lookup matches searched_key as a substring of the extracted key.
    if fmt == "hbs" and "." in logical_key:
        return logical_key.split(".")[-1]
    return logical_key


def extract_pairs_for_file(
    *,
    project_id: str,
    target_path: str,
    old_target: str | None,
    new_target: str | None,
    source_text_by_key: Mapping[str, str] | None,
    source_path: str,
) -> tuple[list[TranslationPair], list[dict[str, str]]]:
    skipped: list[dict[str, str]] = []
    if new_target is None:
        skipped.append({"path": target_path, "reason": "new blob missing"})
        return [], skipped
    found = locale_from_filename(target_path)
    if found is None:
        skipped.append({"path": target_path, "reason": "not a locale file"})
        return [], skipped
    locale, _raw = found
    if locale == SOURCE_LOCALE:
        skipped.append({"path": target_path, "reason": "source-locale file"})
        return [], skipped

    fmt = classify_format(target_path)
    try:
        new_map = parse_locale_map(target_path, new_target)
    except Exception as exc:
        skipped.append({"path": target_path, "reason": f"parse new: {exc}"})
        return [], skipped
    try:
        old_map = parse_locale_map(target_path, old_target or "") if old_target is not None else {}
    except Exception:
        old_map = {}

    source_map = dict(source_text_by_key or {})
    pairs: list[TranslationPair] = []
    keys = list(new_map.keys())
    if fmt == "hbs":
        keys = ["__file__"]
    for key in keys:
        new_value = new_map.get(key, "")
        old_value = old_map.get(key, "")
        if new_value == old_value:
            continue
        logical = _logical_key_for(target_path, key, fmt)
        source_value = source_map.get(key, source_map.get("__file__", ""))
        if fmt != "hbs" and key in source_map:
            source_value = source_map[key]
        pair = TranslationPair(
            project_id=project_id,
            format=fmt,
            target_path=target_path,
            source_path=source_path,
            logical_key=logical,
            string_key=_string_key_for(logical if fmt == "hbs" else key, fmt),
            target_language=locale,
            source_text=source_value,
            old_target_text=old_value,
            new_target_text=new_value,
            reconstructed_opus_id=reconstruct_opus_id(
                project_id, source_path, logical),
        )
        if not source_value:
            pair.warnings.append("en-US source text was empty or not found")
        if pair.oversized:
            pair.warnings.append(
                f"new target is {len(new_value)} chars "
                f"(legacy Bug Fix limit is {BUG_FIX_TEXT_LIMIT})"
            )
        pairs.append(pair)
    return pairs, skipped


def _load_locale_map(path: str, text: str | None) -> dict[str, str]:
    if text is None:
        return {}
    try:
        return parse_locale_map(path, text)
    except Exception:
        return {}


def extract_plan_from_changes(
    *,
    project_id: str,
    changes: Sequence[Mapping[str, Any]],
    new_ref: str,
    old_ref: str,
    get_file_raw: GetFileRawFn,
    source_kind: str = "mr",
    **meta: Any,
) -> SyncPlan:
    plan = SyncPlan(source_kind=source_kind, project_id=project_id, **{
        k: v for k, v in meta.items() if k in SyncPlan.__dataclass_fields__
    })
    source_cache: dict[str, str | None] = {}

    def source_blob(path: str) -> str | None:
        if path not in source_cache:
            source_cache[path] = get_file_raw(project_id, path, new_ref)
            if source_cache[path] is None and old_ref:
                source_cache[path] = get_file_raw(project_id, path, old_ref)
        return source_cache[path]

    for change in changes:
        new_path = str(change.get("new_path") or change.get("newPath") or "")
        old_path = str(change.get("old_path") or change.get("oldPath") or new_path)
        path = new_path or old_path
        if not path or change.get("deleted_file") or change.get("deletedFile"):
            if path:
                plan.skipped.append({"path": path, "reason": "deleted"})
            continue
        if is_source_locale_path(path):
            plan.skipped.append({"path": path, "reason": "source-locale file"})
            continue
        if locale_from_filename(path) is None:
            plan.skipped.append({"path": path, "reason": "not a locale file"})
            continue
        src_path = source_path_for(path)
        if not src_path:
            plan.skipped.append({"path": path, "reason": "cannot derive en-US path"})
            continue
        new_blob = get_file_raw(project_id, path, new_ref)
        old_blob = None
        if old_ref:
            old_blob = get_file_raw(project_id, old_path or path, old_ref)
        source_blob_text = source_blob(src_path)
        source_map = _load_locale_map(src_path, source_blob_text)
        pairs, skipped = extract_pairs_for_file(
            project_id=project_id,
            target_path=path,
            old_target=old_blob,
            new_target=new_blob,
            source_text_by_key=source_map,
            source_path=src_path,
        )
        plan.pairs.extend(pairs)
        plan.skipped.extend(skipped)
        if source_blob_text is None:
            plan.warnings.append(f"en-US source missing: {src_path}")
    if any(p.oversized for p in plan.pairs):
        plan.warnings.append(
            "Some targets exceed the legacy Bug Fix 20k-char field limit; "
            "blob-based apply does not enforce that cap."
        )
    return plan


def extract_plan_from_mr(
    client: Any,
    project_id: str,
    mr_iid: int,
    *,
    gitlab_base: str | None = None,
) -> SyncPlan:
    mr = client.get_merge_request(project_id, mr_iid)
    diffs = client.list_mr_diffs(project_id, mr_iid)
    diff_refs = mr.get("diff_refs") or {}
    head_sha = str(diff_refs.get("head_sha") or mr.get("sha") or "")
    start_sha = str(
        diff_refs.get("start_sha")
        or diff_refs.get("base_sha")
        or mr.get("diff_refs", {}).get("base_sha")
        or ""
    )
    web_url = str(mr.get("web_url") or "")
    plan = extract_plan_from_changes(
        project_id=project_id,
        changes=diffs,
        new_ref=head_sha,
        old_ref=start_sha,
        get_file_raw=client.get_file_raw,
        source_kind="mr",
        mr_iid=int(mr_iid),
        mr_url=web_url,
        source_branch=str(mr.get("source_branch") or ""),
        target_branch=str(mr.get("target_branch") or ""),
        head_sha=head_sha,
        start_sha=start_sha,
        mr_state=str(mr.get("state") or ""),
        gitlab_base=(gitlab_base or getattr(client, "base_url", None)
                     or gitlab_client.DEFAULT_BASE_URL),
    )
    if plan.mr_state and plan.mr_state != "merged":
        plan.warnings.append(
            f"GitLab MR is '{plan.mr_state}'. Apply will queue a Tranzor "
            "Bug Fix MR against the target branch; wait until merge if you "
            "do not want a competing git change."
        )
    return plan


def extract_plan_from_diff_text(
    *,
    project_id: str,
    diff_text: str,
    get_file_raw: GetFileRawFn,
    new_ref: str,
    old_ref: str,
) -> SyncPlan:
    paths = parse_unified_diff_paths(diff_text)
    changes = [{"new_path": path, "old_path": path} for path in paths]
    return extract_plan_from_changes(
        project_id=project_id,
        changes=changes,
        new_ref=new_ref,
        old_ref=old_ref,
        get_file_raw=get_file_raw,
        source_kind="diff",
        gitlab_base=gitlab_client.DEFAULT_BASE_URL,
    )


def git_show_file(repo_dir: str, path: str, ref: str) -> str | None:
    """Read ``ref:path`` from a local git checkout. Missing file → ``None``."""
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def local_file_reader(repo_dir: str) -> GetFileRawFn:
    def _read(_project_id: str, path: str, ref: str) -> str | None:
        return git_show_file(repo_dir, path, ref)
    return _read


# ---------------------------------------------------------------------------
# ICE probe
# ---------------------------------------------------------------------------
def word_count(text: str) -> int:
    return len([part for part in (text or "").split() if part])


def probe_ice(
    pairs: Sequence[TranslationPair],
    *,
    post_fn: PostFn | None = None,
    host: str | None = None,
    timeout: float = 15,
) -> list[ProbeHit]:
    hits: list[ProbeHit] = []
    if not pairs:
        return hits
    url = f"{(host or DEFAULT_ICE_HOST).rstrip('/')}{ICE_MATCH_PATH}"
    for pair in pairs:
        opus_id = pair.reconstructed_opus_id or ICE_DUMMY_OPUS_ID
        source = pair.source_text
        if not source:
            hits.append(ProbeHit(pair, "", "NO MATCH", False,
                                 "no source text to probe"))
            continue
        items = [{"opusID": opus_id, "stringValue": source}]
        if (word_count(source) >= ICE_CONTENT_ONLY_MIN_WORDS
                and opus_id != ICE_DUMMY_OPUS_ID):
            items.append({"opusID": ICE_DUMMY_OPUS_ID, "stringValue": source})
        payload = {
            "items": items,
            "languages": [pair.target_language],
        }
        try:
            if post_fn is None:
                import requests
                resp = requests.post(
                    url, json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            else:
                data = post_fn(url, payload, timeout)
        except Exception as exc:
            hits.append(ProbeHit(pair, "", "", False, str(exc)))
            continue
        ice_target, match_type = _best_ice_target(
            data, pair.target_language)
        stale = bool(ice_target) and ice_target != pair.new_target_text
        hits.append(ProbeHit(pair, ice_target, match_type, stale))
    return hits


def _best_ice_target(data: Any, language: str) -> tuple[str, str]:
    items = data if isinstance(data, list) else (
        data.get("items") if isinstance(data, Mapping) else [])
    best_text = ""
    best_type = "NO MATCH"
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        result = (item.get("translationMemoryMatchingResult")
                  or item.get("result") or {})
        if not isinstance(result, Mapping):
            result = {}
        match_type = str(result.get("type") or "NO MATCH")
        translations = result.get("translations") or {}
        if not isinstance(translations, Mapping):
            continue
        text = translations.get(language)
        if text is None:
            continue
        text = str(text)
        if match_type.upper() not in ("NO MATCH", "NO_MATCH", "") and text:
            return text, match_type
        if text and not best_text:
            best_text, best_type = text, match_type
    return best_text, best_type


# ---------------------------------------------------------------------------
# Bug Fix / blob apply
# ---------------------------------------------------------------------------
def blob_url(gitlab_base: str, project_id: str, ref: str, relative_path: str) -> str:
    origin = (gitlab_base or gitlab_client.DEFAULT_BASE_URL).rstrip("/")
    encoded_ref = quote(str(ref), safe="")
    return f"{origin}/{project_id}/-/blob/{encoded_ref}/{relative_path}"


def group_apply_batches(plan: SyncPlan) -> list[dict[str, Any]]:
    grouped: dict[str, list[TranslationPair]] = defaultdict(list)
    for pair in plan.changed_pairs():
        grouped[pair.source_path].append(pair)
    batches = []
    for source_path, pairs in grouped.items():
        keys = list(dict.fromkeys(p.string_key for p in pairs))
        languages = list(dict.fromkeys(p.target_language for p in pairs))
        ref = plan.target_branch or plan.head_sha or "HEAD"
        batches.append({
            "source_path": source_path,
            "git_blob_url": blob_url(
                plan.gitlab_base, plan.project_id, ref, source_path),
            "string_keys": keys,
            "target_languages": languages,
            "corrections": [
                {
                    "key": p.string_key,
                    "language": p.target_language,
                    "corrected_text": p.new_target_text,
                }
                for p in pairs
            ],
            "pairs": pairs,
        })
    return batches


def _platform_headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_post(
    url: str,
    payload: Mapping[str, Any],
    *,
    token: str | None,
    timeout: float = 60,
    post_fn: PostFn | None = None,
) -> tuple[int, Any]:
    if post_fn is not None:
        result = post_fn(url, dict(payload), timeout)
        if isinstance(result, tuple) and len(result) == 2:
            return int(result[0]), result[1]
        return 200, result
    import requests
    resp = requests.post(
        url, json=dict(payload),
        headers=_platform_headers(token),
        timeout=timeout,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return resp.status_code, body


def apply_plan(
    plan: SyncPlan,
    *,
    bug_id: str,
    token: str | None,
    platform_url: str = DEFAULT_PLATFORM_URL,
    target_branch: str | None = None,
    post_fn: PostFn | None = None,
    dry_run: bool = True,
) -> list[ApplyBatchResult]:
    branch = (target_branch or plan.target_branch or "").strip()
    if not branch:
        raise ValueError("target_branch is required to apply")
    if not (bug_id or "").strip():
        raise ValueError("bug_id is required to apply")
    origin = platform_url.rstrip("/")
    results: list[ApplyBatchResult] = []
    for batch in group_apply_batches(plan):
        lookup_body = {
            "git_blob_url": batch["git_blob_url"],
            "string_keys": batch["string_keys"],
            "target_languages": batch["target_languages"],
            "target_branch": branch,
        }
        if dry_run:
            results.append(ApplyBatchResult(
                source_path=batch["source_path"],
                status="dry_run",
                languages=list(batch["target_languages"]),
                keys=list(batch["string_keys"]),
            ))
            continue
        status, lookup = _json_post(
            origin + BLOB_LOOKUP_PATH, lookup_body,
            token=token, post_fn=post_fn)
        if status >= 400:
            results.append(ApplyBatchResult(
                source_path=batch["source_path"],
                status="lookup_failed",
                error=_error_message(lookup, status),
                languages=list(batch["target_languages"]),
                keys=list(batch["string_keys"]),
            ))
            continue
        token_value = ""
        if isinstance(lookup, Mapping):
            token_value = str(lookup.get("lookup_token") or "")
        if not token_value:
            results.append(ApplyBatchResult(
                source_path=batch["source_path"],
                status="lookup_failed",
                error="lookup response missing lookup_token",
                languages=list(batch["target_languages"]),
                keys=list(batch["string_keys"]),
            ))
            continue
        apply_body = {
            "lookup_token": token_value,
            "target_branch": branch,
            "bug_id": bug_id.strip(),
            "corrections": batch["corrections"],
        }
        status, applied = _json_post(
            origin + BLOB_APPLY_PATH, apply_body,
            token=token, post_fn=post_fn, timeout=120)
        if status >= 400:
            results.append(ApplyBatchResult(
                source_path=batch["source_path"],
                status="apply_failed",
                error=_error_message(applied, status),
                languages=list(batch["target_languages"]),
                keys=list(batch["string_keys"]),
            ))
            continue
        payload = applied if isinstance(applied, Mapping) else {}
        results.append(ApplyBatchResult(
            source_path=batch["source_path"],
            status=str(payload.get("status") or "queued"),
            submission_id=str(payload.get("submission_id") or ""),
            mr_url=payload.get("mr_url"),
            languages=list(batch["target_languages"]),
            keys=list(batch["string_keys"]),
        ))
    return results


def _error_message(body: Any, status: int) -> str:
    if isinstance(body, Mapping):
        detail = body.get("detail") or body.get("error") or body.get("message")
        if isinstance(detail, str) and detail.strip():
            return f"HTTP {status}: {detail}"
        try:
            return f"HTTP {status}: {json.dumps(detail or body, ensure_ascii=False)[:500]}"
        except Exception:
            return f"HTTP {status}"
    return f"HTTP {status}: {body}"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def plan_to_dict(plan: SyncPlan) -> dict[str, Any]:
    return {
        "source_kind": plan.source_kind,
        "project_id": plan.project_id,
        "mr_iid": plan.mr_iid,
        "mr_url": plan.mr_url,
        "source_branch": plan.source_branch,
        "target_branch": plan.target_branch,
        "head_sha": plan.head_sha,
        "start_sha": plan.start_sha,
        "mr_state": plan.mr_state,
        "gitlab_base": plan.gitlab_base,
        "warnings": list(plan.warnings),
        "skipped": list(plan.skipped),
        "pair_count": len(plan.pairs),
        "changed_count": len(plan.changed_pairs()),
        "pairs": [asdict(p) for p in plan.pairs],
    }


def plan_from_dict(data: Mapping[str, Any]) -> SyncPlan:
    pairs = [
        TranslationPair(**{
            k: v for k, v in raw.items()
            if k in TranslationPair.__dataclass_fields__
        })
        for raw in (data.get("pairs") or [])
        if isinstance(raw, Mapping)
    ]
    return SyncPlan(
        source_kind=str(data.get("source_kind") or "mr"),
        project_id=str(data.get("project_id") or ""),
        mr_iid=data.get("mr_iid"),
        mr_url=str(data.get("mr_url") or ""),
        source_branch=str(data.get("source_branch") or ""),
        target_branch=str(data.get("target_branch") or ""),
        head_sha=str(data.get("head_sha") or ""),
        start_sha=str(data.get("start_sha") or ""),
        mr_state=str(data.get("mr_state") or ""),
        gitlab_base=str(data.get("gitlab_base") or gitlab_client.DEFAULT_BASE_URL),
        pairs=pairs,
        skipped=list(data.get("skipped") or []),
        warnings=list(data.get("warnings") or []),
    )


def plan_to_tmx(plan: SyncPlan) -> str:
    tus: list[str] = []
    for pair in plan.changed_pairs():
        tuid = html.escape(pair.logical_key or pair.string_key, quote=True)
        src = html.escape(pair.source_text)
        tgt = html.escape(pair.new_target_text)
        lang = html.escape(pair.target_language, quote=True)
        tus.append(
            "    <tu tuid=\"{tuid}\">\n"
            "      <prop type=\"project\">{project}</prop>\n"
            "      <prop type=\"path\">{path}</prop>\n"
            "      <tuv xml:lang=\"en-US\"><seg>{src}</seg></tuv>\n"
            "      <tuv xml:lang=\"{lang}\"><seg>{tgt}</seg></tuv>\n"
            "    </tu>".format(
                tuid=tuid,
                project=html.escape(pair.project_id),
                path=html.escape(pair.target_path),
                src=src,
                lang=lang,
                tgt=tgt,
            )
        )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<tmx version=\"1.4\">\n"
        "  <header creationtool=\"mr-tm-sync\" creationtoolversion=\"1.0\"\n"
        "          segtype=\"block\" o-tmf=\"tranzor\" adminlang=\"en-US\"\n"
        "          srclang=\"en-US\" datatype=\"plaintext\"/>\n"
        "  <body>\n"
        + ("\n".join(tus) if tus else "    <!-- no changed pairs -->")
        + "\n  </body>\n</tmx>\n"
    )


def format_plan_table(plan: SyncPlan) -> str:
    lines = [
        f"project     {plan.project_id}",
        f"mr          {plan.mr_iid or '—'}  {plan.mr_state or ''}".rstrip(),
        f"branches    {plan.source_branch or '—'} → {plan.target_branch or '—'}",
        f"pairs       {len(plan.changed_pairs())} changed / {len(plan.pairs)} extracted",
        f"skipped     {len(plan.skipped)}",
    ]
    if plan.warnings:
        lines.append("warnings:")
        for warning in plan.warnings:
            lines.append(f"  - {warning}")
    lines.append("")
    lines.append(
        f"{'locale':<8} {'fmt':<11} {'key':<48} path"
    )
    lines.append("-" * 96)
    for pair in plan.changed_pairs():
        key = pair.string_key
        if len(key) > 46:
            key = key[:43] + "..."
        lines.append(
            f"{pair.target_language:<8} {pair.format:<11} {key:<48} {pair.target_path}"
        )
    return "\n".join(lines)


def format_probe_table(hits: Sequence[ProbeHit]) -> str:
    stale = sum(1 for hit in hits if hit.stale)
    lines = [
        f"ICE probe: {len(hits)} pair(s), {stale} stale (TM ≠ MR text)",
        "",
        f"{'locale':<8} {'stale':<6} {'ice':<12} key",
    ]
    lines.append("-" * 72)
    for hit in hits:
        flag = "YES" if hit.stale else ("err" if hit.error else "no")
        key = hit.pair.string_key
        if len(key) > 40:
            key = key[:37] + "..."
        ice = (hit.ice_match_type or hit.error or "—")[:12]
        lines.append(f"{hit.pair.target_language:<8} {flag:<6} {ice:<12} {key}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_auth_token() -> str:
    try:
        import tranzor_auth
        tranzor_auth.load()
        return tranzor_auth.get_token() or ""
    except Exception:
        return os.environ.get("TRANZOR_JWT") or ""


def _build_client() -> gitlab_client.GitLabClient:
    return gitlab_client.GitLabClient()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mr_tm_sync",
        description=(
            "Extract translation fixes from a GitLab MR / git patch and "
            "optionally upsert them into Tranzor TM via Bug Fix (blob-based)."
        ),
    )
    parser.add_argument("--mr", help="GitLab merge-request URL")
    parser.add_argument("--project", help="GitLab project path, e.g. common/uns")
    parser.add_argument("--iid", type=int, help="Merge-request IID")
    parser.add_argument("--diff", help="Unified diff / patch file")
    parser.add_argument("--repo-dir", help="Local git checkout (with --diff)")
    parser.add_argument("--new-ref", default="HEAD",
                        help="Git ref for the new side of --diff (default HEAD)")
    parser.add_argument("--old-ref", default="HEAD^",
                        help="Git ref for the old side of --diff (default HEAD^)")
    parser.add_argument("--json-out", help="Write the extracted plan as JSON")
    parser.add_argument("--from-json", help="Load a previously extracted plan")
    parser.add_argument("--tmx-out", help="Write changed pairs as TMX 1.4")
    parser.add_argument("--probe", action="store_true",
                        help="Probe ICE TM for each pair")
    parser.add_argument("--apply", action="store_true",
                        help="Submit blob-based Bug Fix (writes TM + queues MR)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --apply, only print the payloads")
    parser.add_argument("--yes", action="store_true",
                        help="Required together with --apply")
    parser.add_argument("--bug-id", default="",
                        help="Jira key recorded on the Bug Fix submission")
    parser.add_argument("--target-branch", default="",
                        help="Override the Bug Fix target branch")
    parser.add_argument("--platform-url", default=DEFAULT_PLATFORM_URL)
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan: SyncPlan | None = None
    if args.from_json:
        try:
            with open(args.from_json, encoding="utf-8") as fh:
                plan = plan_from_dict(json.load(fh))
        except OSError as exc:
            print(f"Cannot read {args.from_json}: {exc}", file=sys.stderr)
            return 2
    elif args.mr or (args.project and args.iid):
        if args.mr:
            project_id, iid = parse_mr_url(args.mr)
        else:
            project_id, iid = args.project, int(args.iid)
        client = _build_client()
        if not client.has_token():
            print("GitLab token missing. Set TRANZOR_GITLAB_TOKEN "
                  "or ~/.tranzor_exporter_config.json", file=sys.stderr)
            return 2
        plan = extract_plan_from_mr(client, project_id, iid)
    elif args.diff:
        if not args.project:
            print("--project is required with --diff", file=sys.stderr)
            return 2
        repo_dir = args.repo_dir or os.getcwd()
        with open(args.diff, encoding="utf-8") as fh:
            diff_text = fh.read()
        plan = extract_plan_from_diff_text(
            project_id=args.project,
            diff_text=diff_text,
            get_file_raw=local_file_reader(repo_dir),
            new_ref=args.new_ref,
            old_ref=args.old_ref,
        )
        plan.source_branch = args.new_ref
    else:
        parser.print_help()
        return 2

    assert plan is not None
    print(format_plan_table(plan))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(plan_to_dict(plan), fh, ensure_ascii=False, indent=2)
        print(f"\nWrote {args.json_out}")
    if args.tmx_out:
        with open(args.tmx_out, "w", encoding="utf-8") as fh:
            fh.write(plan_to_tmx(plan))
        print(f"Wrote {args.tmx_out}")

    if args.probe:
        print()
        hits = probe_ice(plan.changed_pairs())
        print(format_probe_table(hits))

    if args.apply:
        if not args.yes:
            print("\nRefusing to apply without --yes "
                  "(this writes Tranzor TM and queues a Bug Fix MR).",
                  file=sys.stderr)
            return 3
        if not args.bug_id.strip():
            print("--bug-id is required with --apply", file=sys.stderr)
            return 3
        token = _load_auth_token()
        if not token:
            print("Tranzor JWT missing. Log in via Tranzor Helper "
                  "or set TRANZOR_JWT.", file=sys.stderr)
            return 2
        dry = bool(args.dry_run)
        results = apply_plan(
            plan,
            bug_id=args.bug_id,
            token=token,
            platform_url=args.platform_url,
            target_branch=args.target_branch or None,
            dry_run=dry,
        )
        print()
        print("apply" + (" (dry-run)" if dry else "") + ":")
        for item in results:
            extra = item.error or item.submission_id or ""
            print(f"  {item.status:<14} {item.source_path}  {extra}")
            if item.mr_url:
                print(f"               MR {item.mr_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
