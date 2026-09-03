"""
MR → JIRA ID 提取 —— MR Pipeline「JIRA」列的纯逻辑层
=====================================================

基于 JIRA 的敏捷开发下，同一个 JIRA ticket 通常关联多个 MR（例如
``BUP-4360`` 的 MR1…MR4），这些 MR 触发的翻译任务互为「同源任务」。
在 MR Pipeline 表格里直接标出每行所属的 JIRA ID，用户扫一眼就能把
同 ticket 的任务归到一起。

Tranzor 平台的任务 payload 只带 ``merge_request_iid`` / ``project_id``，
不带 MR 标题，所以 JIRA 归属需要一跳 GitLab：

1. ``GET /projects/:id/merge_requests/:iid`` 拿 MR 元数据（复用
   :func:`task_post_edit._shared_gitlab_client` 的进程级共享客户端 ——
   它自身还带 per-(project, iid) 的 MR 响应缓存，双层缓存下同一 MR
   全程只打一次 GitLab）；
2. 用正则从 title 里提取 JIRA ID（``BUP-4360`` 这类 ``KEY-123`` 形态，
   见 :data:`JIRA_ID_RE`），并把标题开头的 ticket 前缀去掉，作为表格中
   与 JIRA ID 同源的 Title；
3. 同一响应里的 ``state``（``opened`` / ``merged`` / ``closed`` /
   ``locked``）映射为 MR Pipeline「MR Status」列的展示文案（Open /
   Merged / Closed / Locked）。表格每次 Search/Refresh 带
   ``force_refresh=True`` 再打一次 GitLab，避免会话里一直显示过期的
   Open。

本模块只放 **纯逻辑**（不依赖 Tkinter），便于单测；GUI 层
(:mod:`gui_tabs` 的 MR Pipeline tab) 引用本模块渲染 JIRA / Title /
MR Status 列。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Optional

# JIRA issue key：项目 key 以大写字母开头、总长 ≥2（JIRA 对项目 key 的
# 硬性要求就是至少 2 个字符），后接 ``-<数字>``。
#
# 边界用 ASCII lookaround 而不是 ``\b``：Python 的 unicode ``\w`` 把汉字
# 也算词字符，"修复BUP-4360购买流程" 这种 ticket 紧贴中文的标题在 ``\b``
# 下两头都找不到边界、整段落空 —— 中文标题是这套工具的常态输入。
# ASCII lookaround 只拦 "XBUP-4360 里从 B 起匹配" 这类真嵌入，放行 CJK 邻接。
#
# 尾部再叠一个负向前瞻挡版本号误伤 —— "BUI-26.3.1" 这类 release 名不含
# JIRA ID，不挡会截出个假的 "BUI-26"（回溯到 "BUI-2" 会被尾部 lookahead
# 拦下，所以整段安全落空）。
JIRA_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])(?!\.\d)")

# 形似 ticket 却几乎必然不是的常见技术缩写（"UTF-8" / "SHA-256" /
# "ISO-8601" / "RFC-2616" / "CVE-2024-1234" / "MR-2"）。按 key（去掉尾部
# ``-<数字>`` 的部分）拦截；真有团队用这些当 JIRA 项目 key 的概率远低于
# 标题里出现这些缩写的概率。按需扩充。
NON_TICKET_KEYS = frozenset({"UTF", "SHA", "ISO", "RFC", "CVE", "MR"})

# Canonical target for clickable ticket IDs in the MR Pipeline table.
JIRA_BROWSE_BASE_URL = "https://jira.ringcentral.com/browse/"


# GitLab API ``state`` → MR Pipeline column label. The API says
# ``opened``; the GitLab UI (and this column) say ``Open``.
_MR_STATE_LABELS = {
    "opened": "Open",
    "merged": "Merged",
    "closed": "Closed",
    "locked": "Locked",
}


@dataclass(frozen=True)
class JiraMetadata:
    """Fields resolved from one GitLab MR response for the MR Pipeline table.

    ``state`` is GitLab's raw value (``opened`` / ``merged`` / …). The GUI
    paints :func:`display_mr_state` of that string in the MR Status column.
    """

    jira_id: str
    title: str
    state: str = ""


def display_mr_state(raw) -> str:
    """Map GitLab's ``state`` field to the MR Status column label.

    Unknown values are title-cased so a future GitLab state still renders
    something readable instead of a blank cell. Empty / None → ``""``.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    known = _MR_STATE_LABELS.get(text.lower())
    if known:
        return known
    return text[:1].upper() + text[1:].lower()


def extract_jira_id(title) -> str:
    """从 MR title 提取第一个 JIRA ID；无匹配返回 ``""``。

    取 **第一个** 非 denylist 匹配：观察到的 MR 命名惯例把 ticket 放在
    标题最前（"BUP-4360 - BUI:: Purchase - …"），偶发的第二个 ID 多是
    描述里顺带提到的关联单，不代表本 MR 的归属；而 "Fix UTF-8 handling
    for BUP-4360" 这种 denylist 词打头的标题要跳过假匹配取真 ticket。
    """
    if not title:
        return ""
    for m in JIRA_ID_RE.finditer(str(title)):
        candidate = m.group(1)
        key = candidate.rsplit("-", 1)[0]
        if key in NON_TICKET_KEYS:
            continue
        return candidate
    return ""


def _single_line(value) -> str:
    """Collapse arbitrary title text to the Treeview's single-line form."""
    return " ".join(str(value or "").split())


def extract_jira_title(title, jira_id=None) -> str:
    """Return the title paired with ``jira_id`` in an MR title.

    The common GitLab convention is ``[BUP-4360] Purchase flow`` or
    ``BUP-4360 - Purchase flow``. In those leading-ticket forms the redundant
    ticket token and separator are removed, leaving the human-readable title.
    If the ticket occurs later in a sentence, the normalized full title is
    retained rather than producing a grammatically broken fragment.

    A title without a valid JIRA ID returns ``""``: the Title column is a
    companion to JIRA, not a general-purpose MR-title column.
    """
    text = _single_line(title)
    ticket = normalize_jira_id(jira_id) if jira_id else extract_jira_id(text)
    if not text or not ticket:
        return ""

    match = next(
        (m for m in JIRA_ID_RE.finditer(text) if m.group(1) == ticket), None)
    if match is None:
        return ""

    # Permit only punctuation before a leading ticket. A prose prefix such
    # as "Fix UTF-8 handling for BUP-4360" stays intact for readability.
    leading = text[:match.start()].strip()
    if leading and not re.fullmatch(r"[\[\(\{\u3010]+", leading):
        return text

    remainder = text[match.end():]
    remainder = re.sub(
        r"^[\s\]\)\}\u3011:：|/\\,_\-\u2013\u2014>]+", "", remainder)
    return remainder.strip()


def normalize_jira_id(value) -> str:
    """Normalize a pasted JIRA ID for exact filtering.

    Lowercase input is accepted for convenience, but URLs, free text and
    ticket-shaped technical acronyms remain invalid. An invalid value returns
    ``""`` so the GUI can surface a useful validation message.
    """
    candidate = str(value or "").strip().upper()
    if not candidate:
        return ""
    return candidate if extract_jira_id(candidate) == candidate else ""


def jira_browse_url(value) -> str:
    """Return the canonical RingCentral JIRA URL for one issue ID.

    Invalid values and table placeholders are deliberately not linkable.
    Normalization also makes a lower-case ticket safe for callers outside the
    table while keeping the final URL in JIRA's conventional upper-case form.
    """
    jira_id = normalize_jira_id(value)
    return f"{JIRA_BROWSE_BASE_URL}{jira_id}" if jira_id else ""


# ---------------------------------------------------------------------------
# 进程级缓存 —— {(project_id, mr_iid): jira_id / title / state}。MR title
# 一经建立基本不改（改了也不影响已归档任务的归属判断）；``state`` 会变
# （opened → merged），所以 GUI 在每次 Search/Refresh 时 ``force_refresh``
# 覆盖它。不落盘，跟 task_post_edit.PostEditCache 同一个生命周期哲学。
# ---------------------------------------------------------------------------
_cache: dict[tuple[str, int], str] = {}
_title_cache: dict[tuple[str, int], str] = {}
_state_cache: dict[tuple[str, int], str] = {}
_cache_lock = threading.Lock()


def _normalize_key(project_id, mr_iid) -> Optional[tuple[str, int]]:
    if not project_id or mr_iid is None:
        return None
    try:
        return (str(project_id), int(mr_iid))
    except (TypeError, ValueError):
        return None


def get_cached(project_id, mr_iid) -> Optional[str]:
    """同步查缓存：命中返回已提取的 JIRA ID（可能是 ``""`` —— 已抓过
    title 但里面没有 ID）；从未成功抓取过返回 ``None``。"""
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    with _cache_lock:
        return _cache.get(key)


def get_cached_title(project_id, mr_iid) -> Optional[str]:
    """Return the cached display title, including ``""`` for no title."""
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    with _cache_lock:
        return _title_cache.get(key)


def get_cached_state(project_id, mr_iid) -> Optional[str]:
    """Return the cached raw GitLab MR state, including ``""`` if fetched
    but the payload had no ``state``. Never-fetched → ``None``."""
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    with _cache_lock:
        return _state_cache.get(key)


def get_cached_metadata(project_id, mr_iid) -> Optional[JiraMetadata]:
    """Return metadata only when both ID and title were resolved together."""
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    with _cache_lock:
        if key not in _cache or key not in _title_cache:
            return None
        return JiraMetadata(
            _cache[key], _title_cache[key], _state_cache.get(key, ""))


def can_fetch() -> bool:
    """GitLab 侧是否可用（共享客户端可构建且配置了 token）。

    GUI 用它决定单元格初始渲染 "…"（稍后会有异步结果）还是 "—"
    （不会有 —— 免得挂一个永远不落地的加载占位）。
    """
    try:
        import task_post_edit as _tpe
        client = _tpe._shared_gitlab_client()
        return client is not None and client.has_token()
    except Exception:
        return False


def _metadata_from_caches(key) -> Optional[JiraMetadata]:
    """Build a :class:`JiraMetadata` when ID + title are both cached."""
    if key not in _cache or key not in _title_cache:
        return None
    return JiraMetadata(
        _cache[key], _title_cache[key], _state_cache.get(key, ""))


def fetch_jira_metadata(project_id, mr_iid, client=None, *,
                        force_refresh=False) -> Optional[JiraMetadata]:
    """按 ``(project_id, mr_iid)`` 解析 JIRA ID + Title + GitLab MR state。

    成功拿到 GitLab MR → 一次性提取 ID、展示标题、``state`` 并写缓存；
    失败（无 token / 网络错 / 404）→ 返回 ``None`` 且 **不缓存**，下次
    渲染自动重试 —— 瞬时故障不该永久固化成 "—"。若进程缓存里已有旧值
    （并发成功的另一 worker，或本次 ``force_refresh`` 失败），失败路径
    返回那份旧值，避免 GUI 把已画上的格子打回 "—"。

    ``force_refresh=True`` 跳过本模块缓存并让 GitLabClient 重新打 API，
    这样 MR Status 列能跟上 opened → merged 这类状态变化。JIRA ID /
    Title 顺带刷新，成本为零。

    ``client`` 仅供测试注入；缺省走进程级共享 GitLabClient。
    """
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    if not force_refresh:
        with _cache_lock:
            cached = _metadata_from_caches(key)
            if cached is not None:
                return cached
    if client is None:
        try:
            import task_post_edit as _tpe
            client = _tpe._shared_gitlab_client()
        except Exception:
            return None
    if client is None:
        return None
    try:
        if not client.has_token():
            return None
        try:
            mr = client.get_merge_request(
                key[0], key[1], force_refresh=force_refresh) or {}
        except TypeError:
            # Test doubles (and any older client) only accept (id, iid).
            mr = client.get_merge_request(key[0], key[1]) or {}
    except Exception:
        # A concurrent fetch for the same key may have succeeded first.
        with _cache_lock:
            return _metadata_from_caches(key)
    raw_title = mr.get("title")
    jira = extract_jira_id(raw_title)
    title = extract_jira_title(raw_title, jira)
    raw_state = mr.get("state")
    state = "" if raw_state is None else str(raw_state)
    with _cache_lock:
        _cache[key] = jira
        _title_cache[key] = title
        _state_cache[key] = state
    return JiraMetadata(jira, title, state)


def fetch_jira_id(project_id, mr_iid, client=None) -> Optional[str]:
    """Backward-compatible ID-only view of :func:`fetch_jira_metadata`."""
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    metadata = fetch_jira_metadata(project_id, mr_iid, client=client)
    if metadata is not None:
        return metadata.jira_id
    # Preserve the existing late-failure race contract: another worker may
    # have resolved just the ID (for example through find_merge_requests).
    with _cache_lock:
        return _cache.get(key)


def _matching_key(project_id, mr_iid) -> Optional[tuple[str, int]]:
    """Case-insensitive project-path key used by cross-project MR search."""
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    return (key[0].casefold(), key[1])


def task_matches_mrs(task, matching_mrs) -> bool:
    """Return whether a Tranzor task belongs to one of ``matching_mrs``."""
    if not isinstance(task, dict):
        return False
    key = _matching_key(task.get("project_id"), task.get("merge_request_iid"))
    return key is not None and key in matching_mrs


def _project_path_from_mr(mr, iid) -> str:
    """Extract ``group/project`` from GitLab's ``references.full`` value."""
    refs = mr.get("references") if isinstance(mr, dict) else None
    full = refs.get("full", "") if isinstance(refs, dict) else ""
    suffix = f"!{iid}"
    if full.endswith(suffix):
        return full[:-len(suffix)]
    return ""


def find_merge_requests(jira_id, project_id=None, client=None):
    """Find every GitLab MR whose displayed JIRA ID equals ``jira_id``.

    GitLab does the broad title search; :func:`extract_jira_id` then enforces
    the same exact first-ticket semantics as the table column. The returned
    set uses case-folded project paths so it can be matched directly against
    Tranzor task payloads.
    """
    jira = normalize_jira_id(jira_id)
    if not jira:
        raise ValueError("JIRA ID must look like BUP-4360.")
    if client is None:
        try:
            import task_post_edit as _tpe
            client = _tpe._shared_gitlab_client()
        except Exception:
            client = None
    if client is None or not client.has_token():
        raise RuntimeError("A GitLab token is required to filter by JIRA ID.")

    matches = set()
    mrs = client.list_merge_requests(
        jira, project_id=project_id, in_field="title")
    for mr in mrs:
        if not isinstance(mr, dict) or extract_jira_id(mr.get("title")) != jira:
            continue
        try:
            iid = int(mr.get("iid"))
        except (TypeError, ValueError):
            continue
        project = (str(project_id) if project_id
                   else _project_path_from_mr(mr, iid))
        key = _matching_key(project, iid)
        if key is None:
            continue
        matches.add(key)
        # Seed the display cache as well. Project-scoped results already use
        # the same path Tranzor supplied; global results use references.full.
        with _cache_lock:
            _cache[(project, iid)] = jira
            _title_cache[(project, iid)] = extract_jira_title(
                mr.get("title"), jira)
            if "state" in mr:
                raw_state = mr.get("state")
                _state_cache[(project, iid)] = (
                    "" if raw_state is None else str(raw_state))
    return matches


def clear_cache() -> int:
    """清空缓存，返回清掉的条数。目前只有测试隔离在用。"""
    with _cache_lock:
        n = len(set(_cache) | set(_title_cache) | set(_state_cache))
        _cache.clear()
        _title_cache.clear()
        _state_cache.clear()
        return n
