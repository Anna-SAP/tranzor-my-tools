"""
MR → JIRA ID 提取 —— MR Pipeline「JIRA」列的纯逻辑层
=====================================================

基于 JIRA 的敏捷开发下，同一个 JIRA ticket 通常关联多个 MR（例如
``BUP-4360`` 的 MR1…MR4），这些 MR 触发的翻译任务互为「同源任务」。
在 MR Pipeline 表格里直接标出每行所属的 JIRA ID，用户扫一眼就能把
同 ticket 的任务归到一起。

Tranzor 平台的任务 payload 只带 ``merge_request_iid`` / ``project_id``，
不带 MR 标题，所以 JIRA 归属需要一跳 GitLab：

1. ``GET /projects/:id/merge_requests/:iid`` 拿 MR title（复用
   :func:`task_post_edit._shared_gitlab_client` 的进程级共享客户端 ——
   它自身还带 per-(project, iid) 的 MR 响应缓存，双层缓存下同一 MR
   全程只打一次 GitLab）；
2. 用正则从 title 里提取 JIRA ID（``BUP-4360`` 这类 ``KEY-123`` 形态，
   见 :data:`JIRA_ID_RE`）。

本模块只放 **纯逻辑**（不依赖 Tkinter），便于单测；GUI 层
(:mod:`gui_tabs` 的 MR Pipeline tab) 引用本模块渲染 JIRA 列。
"""
from __future__ import annotations

import re
import threading
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


# ---------------------------------------------------------------------------
# 进程级缓存 —— {(project_id, mr_iid): jira_id}。MR title 一经建立基本不改
# （改了也不影响已归档任务的归属判断），进程内缓存足够；不落盘，跟
# task_post_edit.PostEditCache 同一个生命周期哲学。
# ---------------------------------------------------------------------------
_cache: dict[tuple[str, int], str] = {}
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


def fetch_jira_id(project_id, mr_iid, client=None) -> Optional[str]:
    """按 ``(project_id, mr_iid)`` 解析 JIRA ID（阻塞，供工作线程调用）。

    成功拿到 title → 提取结果写缓存并返回（title 里没有 ID 时为 ``""``）；
    失败（无 token / 网络错 / 404）→ 返回 ``None`` 且 **不缓存**，下次
    渲染自动重试 —— 瞬时故障不该永久固化成 "—"。

    ``client`` 仅供测试注入；缺省走进程级共享 GitLabClient。
    """
    key = _normalize_key(project_id, mr_iid)
    if key is None:
        return None
    with _cache_lock:
        if key in _cache:
            return _cache[key]
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
        mr = client.get_merge_request(key[0], key[1]) or {}
    except Exception:
        # 失败前再查一次缓存：同 key 的并发 fetch（重绘期间排队的第二发）
        # 可能已经成功落缓存 —— 迟到的失败不该把人家的答案顶成 None。
        with _cache_lock:
            return _cache.get(key)
    jira = extract_jira_id(mr.get("title"))
    with _cache_lock:
        _cache[key] = jira
    return jira


def clear_cache() -> int:
    """清空缓存，返回清掉的条数。目前只有测试隔离在用。"""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
        return n
