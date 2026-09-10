"""Live Shared TM search, separate ICE matching, and historical Tranzor records.

The GUI reads /api/v1/translation-memory/search on every search. Store pairs
provide IDs and source text even when no MR/file records exist. The endpoint
must be deployed; unavailable or invalid responses are explicit failures.
Historical provenance adapters remain available for callers that need them,
but are not a substitute for live TM pairs. GUI code is in gui_tab_tm_panel.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Constants (aligned with platform code, not guessed)
# ---------------------------------------------------------------------------
# app/config.py CACHE_STRICT_MATCH_MAX_WORDS = 2
# translation_memory matcher: word count <= 2 requires (source_id, source)
SHORT_STRING_MAX_WORDS = 2
# context-service tm_matcher._has_more_than_three_words: > 3 → content-only
ICE_CONTENT_ONLY_MIN_WORDS = 4

OPUS_ID_RE = re.compile(
    r"^RingCentral\.(?P<alias>[^.]+)\.(?P<hash>[0-9a-fA-F]{32})\.(?P<logical_key>.+)$"
)
HASH_ONLY_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_QUOTE_STRIP_RE = re.compile(r"^[`'\"“”‘’]+|[`'\"“”‘’]+$")

PIPELINE_SEP = ":::seg:::"

DEFAULT_ICE_HOST = (
    os.environ.get("TRANZOR_CONTEXT_SERVICE_HOST")
    or "http://l10n-context-service.int.rclabenv.com"
).rstrip("/")
ICE_MATCH_PATH = "/api/v1/translation-memory/match"
# Dummy identity so ICE rule 3 (content-only, >3 words) can be probed when
# the user has source text but no OPUS ID. Rule 1 will miss this key.
ICE_DUMMY_OPUS_ID = (
    "RingCentral.probe.00000000000000000000000000000000.content"
)

LAYERS = ("ice", "shared", "records")
MATCH_MODES = ("ignore_hash", "fuzzy", "exact")

PAGE_SIZE = 200
MAX_RECORD_PAGES = 3
MAX_ICE_PROBES = 24
MAX_PROVENANCE_MRS = 6
ICE_TIMEOUT_S = 15

# File Translation rows that the legacy processor treats as TM hits.
_FILE_TM_TYPES = frozenset({"tm", "TM", "translation memory", "Translation Memory"})


SearchRecordsFn = Callable[..., Mapping[str, Any]]
IceMatchFn = Callable[..., Sequence[Mapping[str, Any]]]
LocalSearchFn = Callable[..., Mapping[str, Any]]
ProvenanceFn = Callable[..., Sequence[Mapping[str, Any]]]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def strip_wrapping_quotes(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    return _QUOTE_STRIP_RE.sub("", value).strip()


def split_pipeline_key(key: str) -> tuple[str, int | None]:
    """Strip UNS ``:::seg:::N`` so record-table search can hit ``opus_id``."""
    text = str(key or "")
    opus_id, separator, seg_uid = text.rpartition(PIPELINE_SEP)
    if separator and opus_id:
        if seg_uid.isdecimal() and int(seg_uid) > 0:
            return opus_id, int(seg_uid)
        return text, None
    return text, None


def parse_opus_id(raw: str) -> dict[str, str]:
    """Split a full OPUS ID into alias / path_hash / logical_key.

    Uses the handbook regex (32-hex hash). Non-matching input returns empty
    alias/hash and ``logical_key`` equal to the cleaned raw string so callers
    can still use it as a fuzzy needle.
    """
    text, _seg = split_pipeline_key(strip_wrapping_quotes(raw))
    if not text:
        return {"raw": "", "alias": "", "path_hash": "", "logical_key": "",
                "valid": False}
    match = OPUS_ID_RE.match(text)
    if match:
        return {
            "raw": text,
            "alias": match.group("alias"),
            "path_hash": match.group("hash").lower(),
            "logical_key": match.group("logical_key"),
            "valid": True,
        }
    # Fallback: RingCentral.{alias}.{rest} without a 32-hex hash (UNS-style
    # or truncated paste). Keep the rest as the search needle.
    parts = text.split(".", 2)
    if len(parts) >= 3 and parts[0] == "RingCentral":
        maybe_hash = parts[1]
        rest = parts[2]
        # alias.hash.logical when hash isn't 32 hex — treat rest as needle.
        return {
            "raw": text,
            "alias": maybe_hash,
            "path_hash": "",
            "logical_key": rest,
            "valid": False,
        }
    return {
        "raw": text,
        "alias": "",
        "path_hash": "",
        "logical_key": text,
        "valid": False,
    }


def word_count(text: str) -> int:
    """Platform word count: whitespace-split, empty parts dropped."""
    return len([part for part in (text or "").split() if part])


def is_short_string(text: str) -> bool:
    """True when Shared TM / DB cache require exact ``source_id``."""
    return word_count(text) <= SHORT_STRING_MAX_WORDS


def ice_allows_content_only(text: str) -> bool:
    """True when ICE rule 3 (content-only Exact Match) can fire."""
    return word_count(text) >= ICE_CONTENT_ONLY_MIN_WORDS


def ignore_hash_needle(raw: str) -> str:
    """SOP step 0: drop the hash, search by logical key (or the raw tail).

    A 32-hex paste is kept as-is so the user can still hunt one hash.
    """
    text = strip_wrapping_quotes(raw)
    if not text:
        return ""
    if HASH_ONLY_RE.match(text):
        return text.lower()
    parsed = parse_opus_id(text)
    if parsed["valid"]:
        return parsed["logical_key"]
    if parsed["logical_key"]:
        return parsed["logical_key"]
    return text


@dataclass
class QueryIntent:
    """Normalised search form."""

    query: str = ""
    match_mode: str = "ignore_hash"
    source_text: str = ""
    translated_text: str = ""
    product_line: str = ""
    target_language: str = ""
    source_type: str = "all"
    layers: frozenset[str] = field(default_factory=lambda: frozenset(LAYERS))

    def cleaned(self) -> "QueryIntent":
        layers = frozenset(
            layer for layer in (self.layers or LAYERS) if layer in LAYERS
        ) or frozenset(LAYERS)
        mode = (self.match_mode or "ignore_hash").strip().lower()
        if mode not in MATCH_MODES:
            mode = "ignore_hash"
        source_type = (self.source_type or "all").strip().lower()
        if source_type not in ("all", "mr", "file"):
            source_type = "all"
        return QueryIntent(
            query=strip_wrapping_quotes(self.query),
            match_mode=mode,
            source_text=(self.source_text or "").strip(),
            translated_text=(self.translated_text or "").strip(),
            product_line=(self.product_line or "").strip(),
            target_language=(self.target_language or "").strip(),
            source_type=source_type,
            layers=layers,
        )


def validate_intent(intent: QueryIntent) -> str | None:
    """Return an error message if the form cannot run; else None."""
    clean = intent.cleaned()
    if not any((
        clean.query,
        clean.source_text,
        clean.translated_text,
        clean.product_line,
    )):
        return "need_filter"
    if "ice" in clean.layers and "records" not in clean.layers:
        parsed = parse_opus_id(clean.query)
        has_opus = parsed["valid"] or bool(clean.query)
        if not clean.source_text and not has_opus:
            return "ice_needs_source_or_query"
    return None


def record_search_params(intent: QueryIntent) -> dict[str, Any]:
    """Map a QueryIntent onto ``GET /translations/search`` parameters."""
    clean = intent.cleaned()
    params: dict[str, Any] = {
        "source_type": clean.source_type,
        "match_mode": "exact" if clean.match_mode == "exact" else "fuzzy",
        "limit": PAGE_SIZE,
        "offset": 0,
    }
    if clean.source_text:
        params["source_text"] = clean.source_text
    if clean.translated_text:
        params["translated_text"] = clean.translated_text
    if clean.target_language:
        params["target_language"] = clean.target_language
    if clean.product_line:
        params["product_line"] = clean.product_line
    if clean.query:
        if clean.match_mode == "ignore_hash":
            params["opus_id"] = ignore_hash_needle(clean.query)
            params["match_mode"] = "fuzzy"
        else:
            params["opus_id"] = clean.query
    return params


def local_search_kwargs(intent: QueryIntent) -> dict[str, Any]:
    """Map a QueryIntent onto :func:`opus_search.search_index` kwargs."""
    clean = intent.cleaned()
    kwargs: dict[str, Any] = {"limit": 500}
    if clean.target_language:
        kwargs["target_language"] = clean.target_language
    if clean.product_line:
        kwargs["product"] = clean.product_line
    if clean.source_text:
        kwargs["source_contains"] = clean.source_text
    if clean.translated_text:
        kwargs["translation_contains"] = clean.translated_text
    if not clean.query:
        return kwargs
    parsed = parse_opus_id(clean.query)
    if clean.match_mode == "exact" and parsed["valid"]:
        kwargs["opus_id"] = parsed["raw"]
        kwargs["opus_match"] = "exact"
    elif clean.match_mode == "ignore_hash":
        needle = ignore_hash_needle(clean.query)
        if HASH_ONLY_RE.match(needle):
            kwargs["opus_id"] = needle
            kwargs["opus_match"] = "contains"
        else:
            kwargs["logical_key_contains"] = needle
        if parsed["alias"] and not clean.product_line:
            kwargs["product"] = parsed["alias"]
    else:
        kwargs["opus_id"] = clean.query
        kwargs["opus_match"] = "contains"
    return kwargs


# ---------------------------------------------------------------------------
# Record / ICE / provenance shapes
# ---------------------------------------------------------------------------
def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False", "no"):
        return False
    return bool(value)


def parse_record_entry(entry: Mapping[str, Any], *, origin: str = "api") -> dict[str, Any]:
    """Normalise one translations/search (or local-index) row."""
    opus_id = str(entry.get("opus_id") or "").strip()
    parsed = parse_opus_id(opus_id)
    source_text = str(entry.get("source_text") or "")
    return {
        "translation_id": str(entry.get("translation_id") or ""),
        "task_id": str(entry.get("task_id") or ""),
        "task_name": str(entry.get("task_name") or ""),
        "product_line": str(entry.get("product_line") or entry.get("alias") or ""),
        "project_id": str(entry.get("project_id") or ""),
        "opus_id": opus_id,
        "source_text": source_text,
        "target_language": str(entry.get("target_language") or ""),
        "translated_text": str(entry.get("translated_text") or ""),
        "translation_type": str(entry.get("translation_type") or ""),
        "created_at": str(entry.get("created_at") or entry.get("task_created_at") or ""),
        "source_type": str(entry.get("source_type") or entry.get("source_kind") or ""),
        "mr_iid": entry.get("mr_iid"),
        "alias": parsed["alias"],
        "path_hash": parsed["path_hash"],
        "logical_key": parsed["logical_key"] or opus_id,
        "tm_match": _as_bool(entry.get("tm_match")),
        "ice_match": _as_bool(entry.get("ice_match")),
        "cached": _as_bool(entry.get("cached")),
        "fixed_by_lead": entry.get("fixed_by_lead") or "",
        "tm_match_category": entry.get("tm_match_category") or "",
        "origin": origin,
        "short_string": is_short_string(source_text),
        "file_tm": str(entry.get("translation_type") or "") in _FILE_TM_TYPES,
    }


def parse_ice_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one ICE ``/translation-memory/match`` item."""
    result = item.get("translationMemoryMatchingResult") or item.get("result") or {}
    if not isinstance(result, Mapping):
        result = {}
    match_type = str(result.get("type") or "NO MATCH")
    translations = result.get("translations") or {}
    if not isinstance(translations, Mapping):
        translations = {}
    clean_translations = {
        str(lang): str(text)
        for lang, text in translations.items()
        if lang and text is not None
    }
    opus_id = str(item.get("opusID") or item.get("opus_id") or "")
    source_text = str(item.get("stringValue") or item.get("source_text") or "")
    hit = match_type.upper() not in ("NO MATCH", "NO_MATCH", "")
    return {
        "opus_id": opus_id,
        "source_text": source_text,
        "match_type": match_type,
        "translations": clean_translations,
        "hit": hit,
        "dummy": opus_id == ICE_DUMMY_OPUS_ID,
        "error": "",
    }


def ice_probe_items(
    intent: QueryIntent,
    records: Sequence[Mapping[str, Any]],
    *,
    cap: int = MAX_ICE_PROBES,
) -> list[dict[str, str]]:
    """Build unique ``{opusID, stringValue}`` probes for the ICE match API."""
    clean = intent.cleaned()
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, str]] = []

    def _add(opus_id: str, source: str) -> None:
        opus_id = (opus_id or "").strip()
        source = source if source is not None else ""
        if not opus_id or not source:
            return
        key = (opus_id, source)
        if key in seen:
            return
        seen.add(key)
        items.append({"opusID": opus_id, "stringValue": source})

    parsed = parse_opus_id(clean.query)
    if parsed["valid"] and clean.source_text:
        _add(parsed["raw"], clean.source_text)

    for row in records:
        if len(items) >= cap:
            break
        _add(str(row.get("opus_id") or ""), str(row.get("source_text") or ""))

    if (
        len(items) < cap
        and clean.source_text
        and ice_allows_content_only(clean.source_text)
        and (ICE_DUMMY_OPUS_ID, clean.source_text) not in seen
    ):
        _add(ICE_DUMMY_OPUS_ID, clean.source_text)

    return items[:cap]


def flatten_local_cards(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Turn opus_search cards into record-shaped rows."""
    rows: list[dict[str, Any]] = []
    for card in (payload or {}).get("results") or []:
        opus_id = str(card.get("opus_id") or "")
        source_text = str(card.get("source_text") or "")
        translations = card.get("translations") or []
        if not translations:
            rows.append(parse_record_entry({
                "opus_id": opus_id,
                "source_text": source_text,
                "target_language": "",
                "translated_text": "",
                "source_type": card.get("source_kind") or "local",
                "product_line": card.get("alias") or "",
                "created_at": card.get("task_created_at") or "",
            }, origin="local"))
            continue
        for trans in translations:
            rows.append(parse_record_entry({
                "opus_id": opus_id,
                "source_text": source_text,
                "target_language": trans.get("target_language") or "",
                "translated_text": trans.get("translated_text") or "",
                "source_type": card.get("source_kind") or trans.get("source_kind") or "local",
                "product_line": card.get("alias") or "",
                "created_at": trans.get("task_created_at") or card.get("task_created_at") or "",
                "task_id": trans.get("task_id") or "",
                "project_id": trans.get("project_id") or card.get("project_id") or "",
                "mr_iid": trans.get("mr_iid") or card.get("mr_iid"),
            }, origin="local"))
    return rows


def _record_dedupe_key(row: Mapping[str, Any]) -> tuple:
    tid = str(row.get("translation_id") or "")
    if tid:
        return ("id", tid)
    return (
        "row",
        row.get("opus_id") or "",
        row.get("target_language") or "",
        row.get("translated_text") or "",
        row.get("task_id") or "",
        row.get("origin") or "",
    )


def merge_records(*batches: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe record rows, API hits first, newest ``created_at`` first."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for batch in batches:
        for raw in batch or []:
            row = raw if "logical_key" in raw else parse_record_entry(raw)
            key = _record_dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(row))
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return out


def apply_provenance(
    records: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Stamp tm_match / ice_match / cached / fixed_by_lead from MR cases."""
    by_id: dict[str, Mapping[str, Any]] = {}
    by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for case in cases or []:
        opus_id = str(case.get("raw_opus_id") or case.get("opus_id") or "")
        lang = str(case.get("target_language") or "")
        text = str(case.get("translated_text") or "")
        tid = str(case.get("translation_id") or "")
        if tid:
            by_id[tid] = case
        if opus_id and lang:
            by_key[(opus_id, lang, text)] = case
    out = []
    for row in records:
        updated = dict(row)
        case = None
        tid = str(row.get("translation_id") or "")
        if tid and tid in by_id:
            case = by_id[tid]
        else:
            case = by_key.get((
                str(row.get("opus_id") or ""),
                str(row.get("target_language") or ""),
                str(row.get("translated_text") or ""),
            ))
        if case:
            updated["tm_match"] = updated["tm_match"] or _as_bool(case.get("tm_match"))
            updated["ice_match"] = updated["ice_match"] or _as_bool(case.get("ice_match"))
            updated["cached"] = updated["cached"] or _as_bool(case.get("cached"))
            if case.get("fixed_by_lead") and not updated.get("fixed_by_lead"):
                updated["fixed_by_lead"] = case.get("fixed_by_lead")
            if case.get("tm_match_category") and not updated.get("tm_match_category"):
                updated["tm_match_category"] = case.get("tm_match_category")
        out.append(updated)
    return out


def is_shared_tm_hit(row: Mapping[str, Any]) -> bool:
    return bool(row.get("origin") == "shared_tm" or row.get("tm_match") or row.get("file_tm"))


def provenance_targets(
    records: Sequence[Mapping[str, Any]],
    *,
    cap: int = MAX_PROVENANCE_MRS,
) -> list[tuple[str, int, str]]:
    """Unique ``(project_id, mr_iid, opus_id)`` triples for mr-cases lookups."""
    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int, str]] = []
    for row in records:
        if str(row.get("source_type") or "") not in ("mr",):
            continue
        project_id = str(row.get("project_id") or "").strip()
        mr_iid = row.get("mr_iid")
        try:
            mr_int = int(mr_iid)
        except (TypeError, ValueError):
            continue
        if not project_id or mr_int <= 0:
            continue
        pair = (project_id, mr_int)
        if pair in seen:
            continue
        seen.add(pair)
        out.append((project_id, mr_int, str(row.get("opus_id") or "")))
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# Lineage grouping
# ---------------------------------------------------------------------------
def _hash_recency(records: Sequence[Mapping[str, Any]]) -> str:
    newest = ""
    newest_at = ""
    for row in records:
        created = str(row.get("created_at") or "")
        path_hash = str(row.get("path_hash") or "")
        if path_hash and created >= newest_at:
            newest_at = created
            newest = path_hash
    return newest


def classify_hash_role(
    path_hash: str,
    *,
    query_hash: str = "",
    newest_hash: str = "",
) -> str:
    """Label a hash relative to the query and the newest record.

    Does **not** claim 'current branch ID' — that requires Blob-based Fix.
    """
    path_hash = (path_hash or "").lower()
    query_hash = (query_hash or "").lower()
    newest_hash = (newest_hash or "").lower()
    tags = []
    if query_hash and path_hash == query_hash:
        tags.append("pasted")
    if newest_hash and path_hash == newest_hash:
        tags.append("newest")
    if not tags:
        return "other"
    return "+".join(tags)


def group_lineages(
    records: Sequence[Mapping[str, Any]],
    ice_hits: Sequence[Mapping[str, Any]] | None = None,
    *,
    query_hash: str = "",
) -> list[dict[str, Any]]:
    """Group rows by ``(alias, logical_key)`` then by path_hash."""
    ice_by_opus: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for hit in ice_hits or []:
        ice_by_opus[str(hit.get("opus_id") or "")].append(dict(hit))

    families: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for row in records:
        alias = str(row.get("alias") or "")
        logical_key = str(row.get("logical_key") or row.get("opus_id") or "")
        key = (alias, logical_key)
        families.setdefault(key, []).append(dict(row))

    lineages = []
    for (alias, logical_key), rows in families.items():
        newest_hash = _hash_recency(rows)
        by_hash: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for row in rows:
            path_hash = str(row.get("path_hash") or "") or "(none)"
            by_hash.setdefault(path_hash, []).append(row)
        hashes = []
        for path_hash, hash_rows in by_hash.items():
            langs = sorted({
                str(r.get("target_language") or "")
                for r in hash_rows if r.get("target_language")
            })
            opus_ids = []
            seen_opus: set[str] = set()
            for r in hash_rows:
                oid = str(r.get("opus_id") or "")
                if oid and oid not in seen_opus:
                    seen_opus.add(oid)
                    opus_ids.append(oid)
            ice_for_hash = []
            for oid in opus_ids:
                ice_for_hash.extend(ice_by_opus.get(oid) or [])
            hashes.append({
                "path_hash": path_hash if path_hash != "(none)" else "",
                "role": classify_hash_role(
                    path_hash if path_hash != "(none)" else "",
                    query_hash=query_hash,
                    newest_hash=newest_hash,
                ),
                "languages": langs,
                "records": hash_rows,
                "ice": ice_for_hash,
                "shared_hits": sum(1 for r in hash_rows if is_shared_tm_hit(r)),
                "ice_pipeline_hits": sum(1 for r in hash_rows if r.get("ice_match")),
                "opus_ids": opus_ids,
            })
        hashes.sort(key=lambda h: (
            0 if "newest" in h["role"] else 1,
            0 if "pasted" in h["role"] else 1,
            h["path_hash"],
        ))
        lineages.append({
            "alias": alias,
            "logical_key": logical_key,
            "hash_count": len(hashes),
            "row_count": len(rows),
            "newest_hash": newest_hash,
            "hashes": hashes,
        })
    lineages.sort(key=lambda g: (-g["row_count"], g["alias"], g["logical_key"]))
    return lineages


def kpis(
    records: Sequence[Mapping[str, Any]],
    ice_hits: Sequence[Mapping[str, Any]],
    lineages: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    ice_store_hits = sum(1 for h in ice_hits if h.get("hit"))
    return {
        "record_rows": sum(r.get("origin") not in ("shared_tm", "ice_seed") for r in records),
        "logical_keys": len(lineages),
        "hash_variants": sum(g.get("hash_count") or 0 for g in lineages),
        "ice_store_hits": ice_store_hits,
        "ice_store_misses": max(0, len(ice_hits) - ice_store_hits),
        "shared_hits": sum(1 for r in records if is_shared_tm_hit(r)),
        "pipeline_ice_hits": sum(1 for r in records if r.get("ice_match")),
        "cached_hits": sum(1 for r in records if r.get("cached")),
        "lead_fixed": sum(1 for r in records if r.get("fixed_by_lead")),
        "short_strings": sum(1 for r in records if r.get("short_string")),
    }


def warnings_for(
    intent: QueryIntent,
    records: Sequence[Mapping[str, Any]],
    ice_hits: Sequence[Mapping[str, Any]],
) -> list[str]:
    notes = ["current_id_unverified"]
    if any(r.get("short_string") for r in records) or (
        intent.source_text and is_short_string(intent.source_text)
    ):
        notes.append("short_string_id_sensitive")
    hashes = {
        str(r.get("path_hash") or "")
        for r in records if r.get("path_hash")
    }
    if len(hashes) > 1:
        notes.append("multiple_hashes")
    if ice_hits and not any(h.get("hit") for h in ice_hits):
        notes.append("ice_no_hit")
    dummy_hit = any(h.get("dummy") and h.get("hit") for h in ice_hits)
    if dummy_hit:
        notes.append("ice_content_only")
    return notes


def anatomy_card(intent: QueryIntent, lineages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    clean = intent.cleaned()
    parsed = parse_opus_id(clean.query)
    hashes = []
    seen: set[str] = set()
    for group in lineages:
        for bucket in group.get("hashes") or []:
            path_hash = str(bucket.get("path_hash") or "")
            if not path_hash or path_hash in seen:
                continue
            seen.add(path_hash)
            hashes.append({
                "path_hash": path_hash,
                "role": bucket.get("role") or "other",
                "rows": len(bucket.get("records") or []),
            })
    return {
        "raw": parsed["raw"] or clean.query,
        "valid": parsed["valid"],
        "alias": parsed["alias"],
        "path_hash": parsed["path_hash"],
        "logical_key": parsed["logical_key"],
        "needle": ignore_hash_needle(clean.query) if clean.query else "",
        "match_mode": clean.match_mode,
        "source_text": clean.source_text,
        "word_count": word_count(clean.source_text) if clean.source_text else None,
        "short_string": is_short_string(clean.source_text) if clean.source_text else None,
        "hashes": hashes,
    }


def format_handoff(
    anatomy: Mapping[str, Any],
    lineage: Mapping[str, Any] | None = None,
    *,
    target_language: str = "",
    source_text: str = "",
) -> str:
    """SOP §8 handoff template. Target commit is left blank on purpose."""
    newest = ""
    legacy = []
    if lineage:
        for bucket in lineage.get("hashes") or []:
            path_hash = bucket.get("path_hash") or ""
            if not path_hash:
                continue
            if "newest" in (bucket.get("role") or "") and not newest:
                newest = path_hash
            elif path_hash != newest:
                legacy.append(path_hash)
        if not newest and lineage.get("newest_hash"):
            newest = lineage.get("newest_hash") or ""
    alias = (lineage or {}).get("alias") or anatomy.get("alias") or ""
    logical_key = (
        (lineage or {}).get("logical_key")
        or anatomy.get("logical_key")
        or ""
    )
    current_id = ""
    if alias and newest and logical_key:
        current_id = f"RingCentral.{alias}.{newest}.{logical_key}"
    legacy_ids = [
        f"RingCentral.{alias}.{h}.{logical_key}"
        for h in legacy if alias and logical_key
    ]
    lines = [
        f"Project:              {alias or '(unknown)'}",
        "Target branch:        ",
        "Target commit SHA:    (confirm via Bug Fix → Blob-based Fix)",
        "Source relative path: ",
        "Hash source path:     ",
        f"Logical key:          {logical_key}",
        f"Newest-record Opus ID:{(' ' + current_id) if current_id else ' (none)'}",
        "Legacy Opus ID(s):    "
        + (", ".join(legacy_ids) if legacy_ids else "(none found)"),
        f"Exact source text:    {source_text or anatomy.get('source_text') or ''}",
        f"Target language:      {target_language or ''}",
        "Verified at:          (not verified — Exporter TM Panel lineage)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def paginate_records(
    search_fn: SearchRecordsFn,
    params: Mapping[str, Any],
    *,
    max_pages: int = MAX_RECORD_PAGES,
) -> tuple[list[dict[str, Any]], int, str]:
    """Walk ``/translations/search`` up to ``max_pages``. Returns (rows, total, err)."""
    rows: list[dict[str, Any]] = []
    total = 0
    offset = 0
    limit = int(params.get("limit") or PAGE_SIZE)
    for _ in range(max(1, max_pages)):
        call = dict(params)
        call["offset"] = offset
        call["limit"] = limit
        try:
            payload = search_fn(**call) or {}
        except Exception as exc:  # noqa: BLE001 — fail-open per layer
            return rows, total, str(exc)
        entries = payload.get("entries") or []
        total = int(payload.get("total") or total)
        for entry in entries:
            rows.append(parse_record_entry(entry, origin="api"))
        if len(entries) < limit:
            break
        offset += limit
        if total and offset >= total:
            break
    return rows, total, ""


def _safe_call(label: str, fn, *args, **kwargs) -> tuple[Any, str]:
    try:
        return fn(*args, **kwargs), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{label}: {exc}"


MAX_QUERY_KEYS = 100
MAX_TARGET_LANGUAGES = 30
MAX_BATCH_SEARCHES = 200


def split_queries(text: str) -> list[str]:
    """One literal key per line; accept Markdown-escaped underscores."""
    return list(dict.fromkeys(
        value for line in (text or "").splitlines()
        if (value := strip_wrapping_quotes(line).replace(r"\_", "_"))
    ))


def split_target_languages(text: str) -> list[str]:
    return list(dict.fromkeys(filter(None, re.split(r"[,;，；\s]+", text or ""))))


def search_tm_batch(intent: QueryIntent, **adapters) -> dict[str, Any]:
    """Run each key/language independently; retain partial failures and dedupe."""
    clean = intent.cleaned()
    queries = split_queries(clean.query) or [""]
    languages = split_target_languages(clean.target_language) or [""]
    if (len(queries) > MAX_QUERY_KEYS or len(languages) > MAX_TARGET_LANGUAGES
            or len(queries) * len(languages) > MAX_BATCH_SEARCHES):
        return {"ok": False, "error": "batch_limit"}
    if validate_intent(clean):
        return {"ok": False, "error": validate_intent(clean)}
    results = []
    records = []
    ice = []
    seen_ice = set()
    errors = {}
    notes = []
    statuses = {layer: [] for layer in LAYERS}
    for query in queries:
        for language in languages:
            single = replace(clean, query=query, target_language=language)
            view = search_tm_layers(single, **adapters)
            label = f"{query or '(source/product)'} [{language or 'all'}]"
            for name, message in view.get("errors", {}).items():
                errors[f"{label} / {name}"] = message
            # Seeds may be hidden from the records layer but are needed for grouping.
            rows = [r for family in view.get("lineages", [])
                    for bucket in family["hashes"] for r in bucket["records"]]
            records.extend(rows)
            for hit in view.get("ice", []):
                identity = (hit["opus_id"], hit["source_text"], hit["match_type"],
                            tuple(sorted(hit["translations"].items())))
                if identity not in seen_ice:
                    seen_ice.add(identity)
                    ice.append(hit)
            for layer in LAYERS:
                statuses[layer].append(view.get("status", {}).get(layer, "skipped"))
            notes.extend(view.get("warnings", []))
            results.append({"query": query, "language": language,
                            "status": view.get("status", {}),
                            "errors": view.get("errors", {}),
                            "records": sum(r.get("origin") != "shared_tm" for r in rows),
                            "shared_pairs": sum(r.get("origin") == "shared_tm" for r in rows),
                            "ice_hits": sum(h.get("hit", False) for h in view.get("ice", []))})
    records = merge_records(records)
    lineages = group_lineages(records, ice,
                             query_hash=parse_opus_id(queries[0])["path_hash"] if len(queries) == 1 else "")
    status = {}
    for layer, states in statuses.items():
        unique = set(states)
        status[layer] = states[0] if len(unique) == 1 else "partial"
    return {"ok": True, "error": "", "intent": _intent_public(clean),
            "anatomy": anatomy_card(clean, lineages),
            "records": records if "records" in clean.layers else [],
            "ice": ice, "lineages": lineages, "kpis": {**kpis(records, ice, lineages), **({"shared_hits": sum(r.get("origin") == "shared_tm" for r in records)} if adapters.get("store_fn") else {})},
            "warnings": list(dict.fromkeys(notes)), "errors": errors,
            "status": status, "query_results": results,
            "checked_at": datetime.now(timezone.utc).isoformat()}


def search_tm_layers(
    intent: QueryIntent,
    *,
    search_fn: SearchRecordsFn | None = None,
    ice_fn: IceMatchFn | None = None,
    local_fn: LocalSearchFn | None = None,
    provenance_fn: ProvenanceFn | None = None,
    store_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the selected TM layers and return a JSON-serialisable view model."""
    clean = intent.cleaned()
    err = validate_intent(clean)
    if err:
        return {
            "ok": False,
            "error": err,
            "intent": _intent_public(clean),
            "anatomy": anatomy_card(clean, []),
            "records": [],
            "ice": [],
            "lineages": [],
            "kpis": kpis([], [], []),
            "warnings": [],
            "errors": {},
            "status": {
                "records": "skipped",
                "ice": "skipped",
                "shared": "skipped",
            },
        }

    errors: dict[str, str] = {}
    status = {
        "records": "skipped",
        "ice": "skipped",
        "shared": "skipped",
    }

    need_seed = (
        "records" in clean.layers
        or "ice" in clean.layers
        or "shared" in clean.layers
    )
    api_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    total = 0
    if need_seed and search_fn is not None:
        params = record_search_params(clean)
        api_rows, total, rec_err = paginate_records(search_fn, params)
        if rec_err:
            errors["records"] = rec_err
            status["records"] = "error"
        else:
            status["records"] = "ok"
    elif need_seed and search_fn is None:
        status["records"] = "unavailable"

    if need_seed and local_fn is not None:
        local_payload, local_err = _safe_call(
            "local", local_fn, **local_search_kwargs(clean)
        )
        if local_err:
            errors["local"] = local_err
        elif local_payload:
            local_rows = flatten_local_cards(local_payload)

    records = merge_records(api_rows, local_rows)

    if "shared" in clean.layers and provenance_fn is not None:
        cases: list[Mapping[str, Any]] = []
        prov_err = ""
        for project_id, mr_iid, opus_id in provenance_targets(records):
            chunk, chunk_err = _safe_call(
                "provenance",
                provenance_fn,
                project_id=project_id,
                mr_iid=mr_iid,
                opus_id=ignore_hash_needle(opus_id) or opus_id,
                target_language=clean.target_language or None,
            )
            if chunk_err:
                prov_err = chunk_err
                continue
            if chunk:
                cases.extend(list(chunk))
        if cases:
            records = apply_provenance(records, cases)
            status["shared"] = "ok"
        elif prov_err:
            errors["shared"] = prov_err
            status["shared"] = "error"
        else:
            status["shared"] = "ok" if any(
                is_shared_tm_hit(r) for r in records
            ) else "empty"
    elif "shared" in clean.layers:
        status["shared"] = "ok" if any(
            is_shared_tm_hit(r) for r in records
        ) else "no_api"

    store_rows = []
    if store_fn is not None and ("shared" in clean.layers or "ice" in clean.layers):
        try:
            store_rows, truncated = search_live_store(clean, store_fn)
            status["shared"] = "truncated" if truncated else ("ok" if store_rows else "empty")
            if truncated:
                errors["shared"] = "Result limit reached; narrow the key or language filter."
        except Exception as exc:
            status["shared"] = "error"
            errors["shared"] = str(exc)
        # Store rows are distinct from pipeline records even if IDs/text coincide.
        records = merge_records(store_rows, records)

    ice_hits: list[dict[str, Any]] = []
    if "ice" in clean.layers:
        all_probes = ice_probe_items(clean, store_rows + records, cap=max(len(records) + 2, MAX_ICE_PROBES))
        probes = all_probes[:MAX_ICE_PROBES]
        if len(all_probes) > len(probes):
            errors["ice_limit"] = "ICE probe limit reached; narrow the key or language filter."
        if not probes:
            status["ice"] = "no_probe"
        elif ice_fn is None:
            status["ice"] = "unavailable"
        else:
            languages = [clean.target_language] if clean.target_language else None
            raw, ice_err = _safe_call("ice", ice_fn, probes, languages)
            if ice_err:
                errors["ice"] = ice_err
                status["ice"] = "error"
            else:
                for item in raw or []:
                    ice_hits.append(parse_ice_item(item))
                status["ice"] = "ok"

    display_records = records if "records" in clean.layers else []
    query_hash = parse_opus_id(clean.query)["path_hash"]
    lineages = group_lineages(
        display_records or records,
        ice_hits,
        query_hash=query_hash,
    )
    if "records" not in clean.layers:
        # Still group ICE-only / shared-only views around the seed records
        # so hash lineage remains visible, but drop record rows from KPIs.
        display_records = []

    notes = warnings_for(clean, records, ice_hits)
    if store_fn is not None:
        # Provenance markers are historical evidence, not live-store rows.
        live_count = len(store_rows)
    else:
        live_count = sum(is_shared_tm_hit(r) for r in records)
    view = {
        "ok": True,
        "error": "",
        "intent": _intent_public(clean),
        "anatomy": anatomy_card(clean, lineages),
        "records": display_records,
        "ice": ice_hits,
        "lineages": lineages,
        "kpis": kpis(display_records or records, ice_hits, lineages),
        "warnings": notes,
        "errors": errors,
        "status": status,
        "totals": {"records_api": total, "records_merged": len(records)},
    }
    view["kpis"]["shared_hits"] = live_count
    return view


def _intent_public(intent: QueryIntent) -> dict[str, Any]:
    return {
        "query": intent.query,
        "match_mode": intent.match_mode,
        "source_text": intent.source_text,
        "translated_text": intent.translated_text,
        "product_line": intent.product_line,
        "target_language": intent.target_language,
        "source_type": intent.source_type,
        "layers": sorted(intent.layers),
        "needle": ignore_hash_needle(intent.query) if intent.query else "",
    }


# ---------------------------------------------------------------------------
# Default HTTP adapters (injected in tests)
# ---------------------------------------------------------------------------
def search_live_store(intent: QueryIntent, search_fn) -> tuple[list[dict], bool]:
    rows = []
    params = {"key": ignore_hash_needle(intent.query) if intent.match_mode == "ignore_hash" else intent.query,
              "match_mode": "exact" if intent.match_mode == "exact" else "fuzzy",
              "source": intent.source_text, "target": intent.translated_text,
              "project": intent.product_line, "target_language": intent.target_language,
              "limit": PAGE_SIZE}
    for page in range(10):
        payload = search_fn(**params, offset=page * PAGE_SIZE)
        if not isinstance(payload, Mapping) or payload.get("store") != "shared_tm":
            raise ValueError("Shared TM search returned an invalid response (not a live store result).")
        entries = payload.get("entries")
        total = payload.get("total")
        if not isinstance(entries, list) or not isinstance(total, int) or total < 0:
            raise ValueError("Shared TM search returned invalid pagination data.")
        for item in entries:
            if not all(k in item for k in ("pair_id", "pair_type", "source", "target", "target_language")):
                raise ValueError("Shared TM search returned an incomplete translation pair.")
            row = parse_record_entry({
                "translation_id": f"shared_tm:{item['pair_type']}:{item['pair_id']}",
                "opus_id": item.get("source_id") or "",
                "source_text": item["source"], "translated_text": item["target"],
                "target_language": item["target_language"],
                "source_type": item["pair_type"], "project_id": item.get("project", ""),
                "created_at": item.get("updated_date") or item.get("created_date") or "",
            }, origin="shared_tm")
            row.update(pair_id=item["pair_id"], pair_type=item["pair_type"],
                       updated_at=item.get("updated_date"), checked_at=payload.get("checked_at"))
            rows.append(row)
        if (page + 1) * PAGE_SIZE >= total:
            return rows, False
        if len(entries) < PAGE_SIZE:
            raise ValueError("Shared TM returned an incomplete page; retry the query.")
    return rows, True


def default_search_store(base_url: str | None = None, **params) -> dict[str, Any]:
    import requests
    import export_mr_pipeline as mr_api
    url = (base_url or mr_api.TRANZOR_URL).rstrip("/") + "/api/v1/translation-memory/search"
    response = requests.get(url, params=params, headers={"Cache-Control": "no-cache"}, timeout=30)
    if response.status_code == 404:
        raise RuntimeError("Shared TM search API is not deployed on this Platform. This is not an empty TM result.")
    response.raise_for_status()
    return response.json()


def default_search_records(base_url: str | None = None, **kwargs) -> dict[str, Any]:
    import export_mr_pipeline as mr_api
    kwargs.setdefault("source_type", kwargs.pop("source_type", "all"))
    return mr_api.search_translations(base_url=base_url, **kwargs)


def default_local_search(**kwargs) -> dict[str, Any]:
    import opus_search
    return opus_search.search_index(**kwargs)


def default_ice_match(
    items: Sequence[Mapping[str, str]],
    languages: Sequence[str] | None = None,
    *,
    host: str | None = None,
    timeout: float = ICE_TIMEOUT_S,
    post_fn: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    if not items:
        return []
    url = f"{(host or DEFAULT_ICE_HOST).rstrip('/')}{ICE_MATCH_PATH}"
    payload: dict[str, Any] = {"items": [dict(it) for it in items]}
    if languages:
        payload["languages"] = list(languages)
    if post_fn is None:
        import requests
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    else:
        data = post_fn(url, payload, timeout)
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping) and isinstance(data.get("items"), list):
        return list(data["items"])
    return []


def default_provenance(
    *,
    project_id: str,
    mr_iid: int,
    opus_id: str = "",
    target_language: str | None = None,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """GET /dashboard/mr-cases for one MR, filtered by opus_id when given."""
    import export_mr_pipeline as mr_api
    params: dict[str, Any] = {
        "project_id": project_id,
        "mr_id": mr_iid,
        "limit": 200,
        "offset": 0,
        "stats_mode": "defer",
        "include_context_details": "false",
    }
    if opus_id:
        params["opus_id"] = opus_id
    if target_language:
        params["language"] = target_language
    resp = mr_api._api_get(
        f"{mr_api.mr_api_root(base_url)}/dashboard/mr-cases",
        params=params,
        timeout=(10, 60),
    )
    resp.raise_for_status()
    payload = resp.json() or {}
    return list(payload.get("cases") or [])


def tranzor_search_url(intent: QueryIntent, base_url: str | None = None) -> str:
    """Dashboard Search Translations URL mirroring this query (best-effort)."""
    import export_mr_pipeline as mr_api
    params = record_search_params(intent)
    origin = mr_api.tranzor_url(base_url)
    query = {k: v for k, v in params.items() if k not in ("limit", "offset") and v not in (None, "")}
    return f"{origin}/static/translations/search?{urlencode(query)}"
