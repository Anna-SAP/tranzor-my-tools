"""
轻量 GitLab API 客户端 —— 仅用于 TranzorExporter 从 tranzor-fix 分支
commit diff 中恢复 Language Lead BATCH_FIX 的 pre-fix 原译文。

覆盖的 API：
- list_branches(project_id, search)       列分支
- get_commit(project_id, sha)             取 commit 元数据
- get_commit_diff(project_id, sha)        取 commit unified diff（含缓存）
- get_merge_request(project_id, mr_iid)   取 MR 元数据（含 labels）—— 用于
                                          识别哪些 MR 被打了 ``skip-translate``
                                          这类阻止 Tranzor 翻译流水线的标签

配置来源（优先级）：
1. 环境变量 TRANZOR_GITLAB_TOKEN / TRANZOR_GITLAB_BASE_URL
2. 用户家目录文件 ~/.tranzor_exporter_config.json
"""
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

CONFIG_PATH = os.path.expanduser("~/.tranzor_exporter_config.json")
DEFAULT_BASE_URL = "https://git.ringcentral.com"


def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_token():
    return os.getenv("TRANZOR_GITLAB_TOKEN") or load_config().get("gitlab_token") or ""


def get_base_url():
    return (os.getenv("TRANZOR_GITLAB_BASE_URL")
            or load_config().get("gitlab_base_url")
            or DEFAULT_BASE_URL)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
class GitLabClient:
    def __init__(self, base_url=None, token=None, timeout=30):
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self.token = token or get_token()
        self.timeout = timeout
        self._session = requests.Session()
        if self.token:
            self._session.headers["PRIVATE-TOKEN"] = self.token
        self._commit_diff_cache = {}   # sha -> diff list
        self._branches_cache = {}      # (project_id, search) -> branches list
        self._mr_cache = {}            # (project_id, mr_iid) -> mr dict

    def has_token(self):
        return bool(self.token)

    def _encode(self, s):
        return quote(s, safe="")

    def list_branches(self, project_id, search=None, max_pages=50):
        """List branches, paginating until exhausted (capped at ``max_pages``).

        Previously hard-coded to the first 3 pages (300 branches), which silently
        truncated long-lived ``tranzor-fix`` histories. Now walks until an empty
        / partial page is returned, with ``max_pages`` (default 50 → 5000) as a
        runaway safety cap.
        """
        key = (project_id, search or "")
        if key in self._branches_cache:
            return self._branches_cache[key]

        url = (f"{self.base_url}/api/v4/projects/"
               f"{self._encode(project_id)}/repository/branches")
        results = []
        for page in range(1, max_pages + 1):
            params = {"per_page": 100, "page": page}
            if search:
                params["search"] = search
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            batch = resp.json() or []
            results.extend(batch)
            if len(batch) < 100:
                break
        self._branches_cache[key] = results
        return results

    def get_commit(self, project_id, sha):
        url = (f"{self.base_url}/api/v4/projects/"
               f"{self._encode(project_id)}/repository/commits/{sha}")
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_commit_diff(self, project_id, sha):
        if sha in self._commit_diff_cache:
            return self._commit_diff_cache[sha]
        url = (f"{self.base_url}/api/v4/projects/"
               f"{self._encode(project_id)}/repository/commits/{sha}/diff")
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json() or []
        self._commit_diff_cache[sha] = data
        return data

    def get_merge_request(self, project_id, mr_iid):
        """Fetch full MR metadata from GitLab.

        Returns the raw ``GET /api/v4/projects/:id/merge_requests/:iid``
        response dict — its ``labels`` field is the only thing my-tools
        cares about today (Tranzor Platform's ``SKIP_TRANSLATE_LABEL``
        machinery, default ``skip-translate``, lives entirely in GitLab
        labels per LOC-skip / commit ``8663a18``).

        Cached per ``(project_id, mr_iid)`` for the lifetime of this
        client instance — a Tranzor Checks sync touches each MR exactly
        once, but the cache shields us if the same MR is referenced by
        multiple ``task_checks`` rows (rare but possible).

        Raises on HTTP error so callers can decide whether to swallow
        the failure (typical: store ``""`` to signal "we tried, no data")
        or surface it. The Tranzor Checks sync path swallows: missing
        labels must never tank an otherwise healthy sync.
        """
        key = (str(project_id), int(mr_iid))
        if key in self._mr_cache:
            return self._mr_cache[key]
        url = (f"{self.base_url}/api/v4/projects/"
               f"{self._encode(project_id)}/merge_requests/{int(mr_iid)}")
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json() or {}
        self._mr_cache[key] = data
        return data

    def fetch_mr_labels(self, project_id, mr_iid):
        """Convenience wrapper returning ``mr['labels']`` as ``list[str]``.

        Failures bubble up; the typical caller (Tranzor Checks sync) wraps
        this in try/except so a single 404 doesn't kill the batch.
        """
        mr = self.get_merge_request(project_id, mr_iid)
        labels = mr.get("labels") or []
        return [str(x) for x in labels if x]


# ---------------------------------------------------------------------------
# Fix-commit discovery
# ---------------------------------------------------------------------------
BRANCH_TS_RE = re.compile(r"(\d{14})(?:[^\d]|$)")  # trailing YYYYMMDDHHMMSS


def parse_branch_timestamp(branch_name):
    """``tranzor-fix/26-2-2_XMN/20260417070703`` -> naive datetime."""
    m = BRANCH_TS_RE.search(branch_name or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _parse_fixed_at(fixed_at_iso):
    """Tolerant ISO-8601 parse; returns naive datetime in UTC terms."""
    if not fixed_at_iso:
        return None
    s = fixed_at_iso
    if s.endswith("Z"):
        s = s[:-1]
    # Strip any fractional seconds / timezone suffix
    s = re.split(r"[+\-]\d{2}:?\d{2}$", s)[0]
    s = s.split(".", 1)[0]
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


class GitLabAccessError(Exception):
    """Raised when GitLab returns 401/403/404 on project-scoped endpoints —
    typically means the PAT user is not a member of the target project.
    """
    def __init__(self, project_id, status_code, url=None):
        self.project_id = project_id
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"GitLab access denied for project '{project_id}' "
            f"(HTTP {status_code}). Likely the PAT user lacks membership.")


def _candidate_branches_within_window(client, project_id, fixed_at_iso,
                                      window_minutes):
    """Return branches whose name timestamp is within ``window_minutes`` of
    ``fixed_at_iso``, sorted by absolute time-delta ascending.

    Common helper for both the legacy time-only matcher and the new key-aware
    matcher. Raises :class:`GitLabAccessError` on 401/403/404 from listing.
    """
    fixed_at = _parse_fixed_at(fixed_at_iso)
    if not fixed_at:
        return []
    try:
        branches = client.list_branches(project_id, search="tranzor-fix")
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (401, 403, 404):
            raise GitLabAccessError(project_id, code,
                                    url=getattr(e.response, "url", None)) from e
        raise

    window = timedelta(minutes=window_minutes)
    candidates = []
    for b in branches:
        ts = parse_branch_timestamp(b.get("name", ""))
        if not ts:
            continue
        delta = abs(ts - fixed_at)
        if delta <= window:
            candidates.append((delta, b))
    candidates.sort(key=lambda x: x[0])
    return [b for _delta, b in candidates]


def find_fix_commit_sha(client, project_id, fixed_at_iso,
                       window_minutes=60):
    """Legacy time-only matcher — returns the closest-by-time ``tranzor-fix``
    branch HEAD SHA within ``window_minutes``.

    .. deprecated::
        In batch-fix scenarios many cases share near-identical ``fixed_at``,
        and time-min alone can pick a sibling fix's commit → silent wrong-fill
        downstream. Prefer :func:`find_fix_commit_for_key`, which iterates
        candidates ordered by time-delta and verifies that the chosen diff
        actually edits the target ``(opus_id, target_language)``.

    Raises:
        GitLabAccessError: PAT lacks read access on the project (401/403/404).
    """
    candidates = _candidate_branches_within_window(
        client, project_id, fixed_at_iso, window_minutes)
    if not candidates:
        return None
    commit = candidates[0].get("commit") or {}
    return commit.get("id")


def find_fix_commit_for_key(client, project_id, fixed_at_iso,
                            opus_id, target_language, window_minutes=60):
    """Find the ``tranzor-fix`` commit that actually edits
    ``(opus_id, target_language)``.

    Walks all candidate branches whose name timestamp is within
    ``window_minutes`` of ``fixed_at_iso``, ordered by absolute time-delta
    ascending. For each candidate, fetches its diff and runs
    :func:`extract_diff_values`. Returns the first candidate whose diff
    actually contains the target key for the target language.

    This guards against the batch-fix silent-fill scenario: when many cases
    share near-identical ``fixed_at`` (e.g. repeated-string mass fixes),
    matching purely by time-delta can pick a sibling fix's commit, and the
    downstream diff parser would either return ``(None, None)`` (silent skip)
    or — worse — pick up an unrelated occurrence of the key.

    Returns:
        (sha, pre, post): commit SHA + diff '-' / '+' values when a candidate's
        diff contains the key. ``(None, None, None)`` if no candidate matched
        (no branches in window, or none of their diffs touched the key).

    Raises:
        GitLabAccessError: PAT lacks read access on the project (401/403/404).
    """
    candidates = _candidate_branches_within_window(
        client, project_id, fixed_at_iso, window_minutes)
    for b in candidates:
        sha = (b.get("commit") or {}).get("id")
        if not sha:
            continue
        try:
            diff = client.get_commit_diff(project_id, sha)
        except Exception:
            # Diff fetch failure on one candidate shouldn't abort the search —
            # later candidates may still match.
            continue
        pre, post = extract_diff_values(diff, opus_id, target_language)
        if pre is not None or post is not None:
            return sha, pre, post
    return None, None, None


# ---------------------------------------------------------------------------
# Diff parser — 提取某个 (opus_id, target_language) 的 pre/post 文本
# ---------------------------------------------------------------------------
def _lang_path_variants(lang):
    variants = {lang}
    if "-" in lang:
        variants.add(lang.replace("-", "_"))
        variants.add(lang.replace("-", "."))
        variants.add(lang.lower())
    # 大部分 RC 项目资源文件走 zh-CN / zh_CN / zh-cn 三种其一
    return variants


def _key_last_segment(opus_id):
    if not opus_id:
        return ""
    return opus_id.rsplit(".", 1)[-1]


def _unescape_generic(s):
    """通用转义还原 —— 覆盖 JSON/JS/Python 字符串里最常见的转义序列。"""
    if not s:
        return s
    return (s.replace("\\\\", "\x00")  # 占位保护真实反斜杠
             .replace("\\n", "\n")
             .replace("\\r", "\r")
             .replace("\\t", "\t")
             .replace("\\'", "'")
             .replace('\\"', '"')
             .replace("\x00", "\\"))


# 针对常见 i18n 格式的取值正则；用 {key} 占位符，稍后替换为实际 key。
# 顺序决定优先级 —— 把最严格的放前面。
_VALUE_PATTERN_TEMPLATES = [
    # JSON:           "KEY" : "VALUE"     (VALUE 可含 \" \\ 转义)
    r'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"',
    # .strings:       "KEY" = "VALUE";
    r'"{key}"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;',
    # JS/TS 对象字面量，单引号：  KEY: 'VALUE'   或   'KEY': 'VALUE'
    r'(?:^|[\s{{\[,])\'?{key}\'?\s*:\s*\'((?:[^\'\\]|\\.)*)\'',
    # JS/TS 对象字面量，双引号：  KEY: "VALUE"   或   "KEY": "VALUE"
    r'(?:^|[\s{{\[,])"?{key}"?\s*:\s*"((?:[^"\\]|\\.)*)"',
    # YAML:           KEY: "VALUE"
    r'(?:^|\s){key}\s*:\s*"((?:[^"\\]|\\.)*)"',
    # properties:     KEY=VALUE           (行尾无引号，吃到行尾或逗号前)
    r'(?:^|\s){key}\s*=\s*([^\n\r]+?)\s*,?\s*$',
]


def _compile_value_regexes(key):
    key_re = re.escape(key)
    return [re.compile(t.replace("{key}", key_re))
            for t in _VALUE_PATTERN_TEMPLATES]


def _extract_value_from_line(line_body, value_regexes):
    for r in value_regexes:
        m = r.search(line_body)
        if m:
            return _unescape_generic(m.group(1))
    return None


def extract_diff_values(commit_diff, opus_id, target_language):
    """在 commit_diff 中定位 (opus_id, target_language) 的 pre/post 文本。

    Args:
        commit_diff: GitLab ``/commits/:sha/diff`` 返回的 list
        opus_id: Tranzor 的 opus_id（末段为 key）
        target_language: 目标语言码，用来过滤 diff 中的文件路径

    Returns:
        (pre_text, post_text) 元组；任何一端取不到则为 None。
    """
    key = _key_last_segment(opus_id)
    if not key:
        return None, None

    lang_frags = _lang_path_variants(target_language)
    value_regexes = _compile_value_regexes(key)

    for file_diff in commit_diff or []:
        path = (file_diff.get("new_path")
                or file_diff.get("old_path")
                or "")
        if not any(frag in path for frag in lang_frags):
            continue
        patch = file_diff.get("diff") or ""

        pre = post = None
        for line in patch.splitlines():
            if not line:
                continue
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line[0] not in ("-", "+"):
                continue
            if key not in line:
                continue
            body = line[1:]
            val = _extract_value_from_line(body, value_regexes)
            if val is None:
                continue
            if line[0] == "-" and pre is None:
                pre = val
            elif line[0] == "+" and post is None:
                post = val
            if pre is not None and post is not None:
                return pre, post
        if pre is not None or post is not None:
            return pre, post

    return None, None
