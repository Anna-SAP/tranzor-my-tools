"""
轻量 GitLab API 客户端 —— 仅用于 TranzorExporter 从 tranzor-fix 分支
commit diff 中恢复 Language Lead BATCH_FIX 的 pre-fix 原译文。

覆盖的 API：
- list_branches(project_id, search)       列分支
- get_commit(project_id, sha)             取 commit 元数据
- get_commit_diff(project_id, sha)        取 commit unified diff（含缓存）

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

    def has_token(self):
        return bool(self.token)

    def _encode(self, s):
        return quote(s, safe="")

    def list_branches(self, project_id, search=None):
        key = (project_id, search or "")
        if key in self._branches_cache:
            return self._branches_cache[key]

        url = (f"{self.base_url}/api/v4/projects/"
               f"{self._encode(project_id)}/repository/branches")
        # 分页：tranzor-fix 分支可能很多；拉前 3 页足够覆盖最近 300 条
        results = []
        for page in range(1, 4):
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


def find_fix_commit_sha(client, project_id, fixed_at_iso,
                       window_minutes=60):
    """找到时间最接近 ``fixed_at`` 的 ``tranzor-fix/*`` 分支 HEAD commit SHA。

    分支名尾部的 ``YYYYMMDDHHMMSS`` 与 ``fixed_at`` 之差在 window_minutes
    以内且最小的那条；没有匹配返回 None。

    Raises:
        GitLabAccessError: PAT 对该项目无读权限（401/403/404）。
            调用方应捕获并对整个项目放弃恢复尝试、给出清晰提示。
    """
    fixed_at = _parse_fixed_at(fixed_at_iso)
    if not fixed_at:
        return None
    try:
        branches = client.list_branches(project_id, search="tranzor-fix")
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (401, 403, 404):
            raise GitLabAccessError(project_id, code,
                                    url=getattr(e.response, "url", None)) from e
        raise

    best = None
    best_delta = timedelta(minutes=window_minutes)
    for b in branches:
        name = b.get("name", "")
        ts = parse_branch_timestamp(name)
        if not ts:
            continue
        delta = abs(ts - fixed_at)
        if delta <= best_delta:
            best_delta = delta
            best = b
    if best:
        commit = best.get("commit") or {}
        return commit.get("id")
    return None


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
