"""
OPUS 翻译搜索 —— 跨全量本地索引按 OPUS ID / 源文 / 译文 / 产品 检索
==================================================================

服务于本地化经理的 **Bug Fixing 工作流**：拿到一个 OPUS ID（或一段源文 /
译文 / 产品别名），立刻看到它当前的英文源 + 所有目标语言的最新译文，
不必再去下载滞后的 ``UNS.zip`` 或在 Tranzor 网页端逐条翻找。

数据层：复用 :mod:`opus_id_monitor` 维护的本地 SQLite 索引
``~/.tranzor_exporter/opus_index.db``（``opus_index`` 表）。该索引由
``opus_id_monitor.sync_full / sync_incremental`` 从 Tranzor 三个数据源
（MR Pipeline / Scan Task / File Translation）增量同步而来。

数据新鲜度 / 完整度说明（重要）::

    今天：索引数据来自 Tranzor API —— **最新但不完整**（只含 Tranzor 经手
          过的串；产品里从未经 Tranzor 动过的老译文不在其中）。
    规划：后续接入「GitLab 产品仓库全量基线」摄取（见 ``products.json``），
          把每个产品仓库里 *已合并* 的全套本地化文件灌进同一张索引表，
          届时搜索结果将变为 **最新 + 最全**。

    根因背景见项目记忆 ``project-loc-full-export-pivot``：Tranzor 上线后
    翻译只回写 GitLab 产品仓库，不再回写旧 OPUS/loc-central，故 HTTP 目录
    的全量包 (``UNS.zip`` 等) 时间戳严重滞后。

设计约束：
    - 纯加法：不修改任何现有模块，只读地复用 opus_id_monitor 的连接 / 建表。
    - 索引友好：精确 / 前缀匹配走 ``opus_id`` 主键索引；文本子串用 LIKE，
      并强制至少一个收窄条件，避免在 127 万行上裸扫。

用法（CLI）::

    python opus_search.py --opus RingCentral.uns.<hash>.<key>
    python opus_search.py --opus RingCentral.uns. --match prefix --limit 20
    python opus_search.py --product uns --source "Welcome"
    python opus_search.py --product scp --lang de-DE --translation "Speichern"
    python opus_search.py --key "button.save" --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 复用 opus_id_monitor 的连接与建表逻辑，保证 schema 单一来源。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opus_id_monitor import _connect, init_db  # noqa: E402


# 至少需要其中一个"收窄"条件才允许查询（target_language 单独不够选择性）。
_NARROWING_FIELDS = (
    "opus_id", "product", "source_contains",
    "translation_contains", "logical_key_contains", "project_contains",
)

# search_index 单次返回的最大 opus_id "卡片"数（防止 UI / 终端被刷屏）。
_MAX_LIMIT = 2000


def _esc(s: str) -> str:
    """转义 SQL LIKE 的元字符，配合 ``ESCAPE '\\'`` 使用。

    否则用户搜 ``100%`` / ``a_b`` 时 ``%`` ``_`` 会被当通配符。
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean(v: str | None) -> str | None:
    """空串 / 纯空白视为未提供。"""
    if v is None:
        return None
    v = v.strip()
    return v or None


def search_index(
    *,
    opus_id: str | None = None,
    opus_match: str = "exact",          # "exact" | "prefix" | "contains"
    product: str | None = None,          # alias，精确匹配（如 'uns' / 'scp'）
    target_language: str | None = None,  # 精确（如 'de-DE'）
    source_contains: str | None = None,  # 英文源子串
    translation_contains: str | None = None,  # 任一语言译文子串
    logical_key_contains: str | None = None,  # OPUS ID 末段 key 子串
    project_contains: str | None = None,  # project_id 子串（常是 GitLab 路径）
    limit: int = 200,
    db_path: str | None = None,
) -> dict:
    """在本地全量索引中搜索 OPUS 翻译。

    匹配语义：先按条件找出命中的 *distinct opus_id*（按最近出现排序），
    再为这些 opus_id 取出 **全部目标语言** 的最新译文（每个语言取 first_seen
    最新的一条）。这样即使你按 ``de-DE`` 译文搜，结果卡片里仍会列出该串的
    所有语言译文 —— 正是 Bug Fixing 需要的横向视图。

    Returns::

        {
          "count": <返回的 opus_id 卡片数>,
          "truncated": <是否还有更多未返回>,
          "results": [
            {
              "opus_id", "alias", "path_hash", "logical_key", "project_id",
              "source_kind", "release", "mr_iid", "source_text",
              "source_file_path", "task_id", "task_created_at", "first_seen",
              "translations": [{"target_language", "translated_text"}, ...]
            }, ...
          ]
        }

    Raises:
        ValueError: 未提供任何收窄条件时（避免全表扫描）。
    """
    opus_id = _clean(opus_id)
    product = _clean(product)
    target_language = _clean(target_language)
    source_contains = _clean(source_contains)
    translation_contains = _clean(translation_contains)
    logical_key_contains = _clean(logical_key_contains)
    project_contains = _clean(project_contains)

    narrowing = [opus_id, product, source_contains,
                 translation_contains, logical_key_contains, project_contains]
    if not any(narrowing):
        raise ValueError(
            "search_index 需要至少一个收窄条件："
            + " / ".join(_NARROWING_FIELDS)
            + "（target_language 单独不足以收窄）"
        )
    if opus_match not in ("exact", "prefix", "contains"):
        raise ValueError(f"opus_match 必须是 exact/prefix/contains，收到 {opus_match!r}")

    init_db(db_path)

    where: list[str] = []
    params: list = []

    if opus_id:
        if opus_match == "exact":
            where.append("opus_id = ?")
            params.append(opus_id)
        elif opus_match == "prefix":
            where.append("opus_id LIKE ? ESCAPE '\\'")
            params.append(_esc(opus_id) + "%")
        else:  # contains
            where.append("opus_id LIKE ? ESCAPE '\\'")
            params.append("%" + _esc(opus_id) + "%")
    if product:
        where.append("alias = ?")
        params.append(product)
    if target_language:
        where.append("target_language = ?")
        params.append(target_language)
    if source_contains:
        where.append("source_text LIKE ? ESCAPE '\\'")
        params.append("%" + _esc(source_contains) + "%")
    if translation_contains:
        where.append("translated_text LIKE ? ESCAPE '\\'")
        params.append("%" + _esc(translation_contains) + "%")
    if logical_key_contains:
        where.append("logical_key LIKE ? ESCAPE '\\'")
        params.append("%" + _esc(logical_key_contains) + "%")
    if project_contains:
        where.append("project_id LIKE ? ESCAPE '\\'")
        params.append("%" + _esc(project_contains) + "%")

    where_sql = " AND ".join(where)
    limit = max(1, min(int(limit), _MAX_LIMIT))

    with _connect(db_path) as conn:
        # Step 1: 命中的 distinct opus_id，按最近出现排序；多取 1 条判断截断。
        cur = conn.execute(
            f"SELECT opus_id, MAX(first_seen) AS f "
            f"FROM opus_index WHERE {where_sql} "
            f"GROUP BY opus_id ORDER BY f DESC, opus_id ASC LIMIT ?",
            (*params, limit + 1),
        )
        ids = [r["opus_id"] for r in cur.fetchall()]
        truncated = len(ids) > limit
        ids = ids[:limit]
        if not ids:
            return {"count": 0, "truncated": False, "results": []}

        # Step 2: 取这些 opus_id 的全部语言行；按 first_seen DESC 让每语言最新的在前。
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"SELECT opus_id, alias, path_hash, logical_key, project_id, "
            f"source_kind, release, mr_iid, target_language, source_text, "
            f"translated_text, source_file_path, task_id, task_created_at, "
            f"first_seen "
            f"FROM opus_index WHERE opus_id IN ({placeholders}) "
            f"ORDER BY opus_id, target_language, first_seen DESC",
            ids,
        )
        by_id: dict[str, dict] = {}
        for r in cur.fetchall():
            card = by_id.get(r["opus_id"])
            if card is None:
                card = {
                    "opus_id": r["opus_id"],
                    "alias": r["alias"],
                    "path_hash": r["path_hash"],
                    "logical_key": r["logical_key"],
                    "project_id": r["project_id"],
                    "source_kind": r["source_kind"],
                    "release": r["release"],
                    "mr_iid": r["mr_iid"],
                    "source_text": r["source_text"] or "",
                    "source_file_path": r["source_file_path"] or "",
                    "task_id": r["task_id"],
                    "task_created_at": r["task_created_at"],
                    "first_seen": r["first_seen"],
                    "translations": [],
                    "_langs": set(),
                }
                by_id[r["opus_id"]] = card
            # 源文 / 源路径取任一非空（早期行可能缺失）
            if not card["source_text"] and r["source_text"]:
                card["source_text"] = r["source_text"]
            if not card["source_file_path"] and r["source_file_path"]:
                card["source_file_path"] = r["source_file_path"]
            lang = r["target_language"]
            # 每语言只保留最新一条（ORDER BY first_seen DESC，故首次见到即最新）
            if lang and lang not in card["_langs"]:
                card["_langs"].add(lang)
                card["translations"].append({
                    "target_language": lang,
                    "translated_text": r["translated_text"] or "",
                })

        results = []
        for oid in ids:  # 保持 Step 1 的"最近优先"顺序
            card = by_id.get(oid)
            if not card:
                continue
            card.pop("_langs", None)
            card["translations"].sort(key=lambda t: t["target_language"])
            results.append(card)

    return {"count": len(results), "truncated": truncated, "results": results}


# ---------------------------------------------------------------------------
# CLI —— 让搜索在 GUI 标签页落地之前就立刻可用
# ---------------------------------------------------------------------------
def _print_results(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    suffix = "  (结果已截断，请加更精确条件)" if res["truncated"] else ""
    print(f"匹配 {res['count']} 个 OPUS ID{suffix}")
    for card in res["results"]:
        print("─" * 72)
        print(f"OPUS ID : {card['opus_id']}")
        meta = f"产品={card['alias'] or '?'}  来源={card['source_kind']}"
        if card["project_id"]:
            meta += f"  项目={card['project_id']}"
        if card["mr_iid"]:
            meta += f"  MR=!{card['mr_iid']}"
        print(meta)
        if card["source_file_path"]:
            print(f"源文件  : {card['source_file_path']}")
        print(f"英文源  : {card['source_text']}")
        for t in card["translations"]:
            print(f"  {t['target_language']:<7}: {t['translated_text']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="opus_search",
        description="跨本地全量索引搜索 OPUS 翻译（源文 + 全语种译文）",
    )
    ap.add_argument("--opus", help="OPUS ID（配合 --match）")
    ap.add_argument("--match", choices=["exact", "prefix", "contains"],
                    default="exact", help="OPUS ID 匹配方式，默认 exact")
    ap.add_argument("--product", help="产品别名（如 uns / scp / chc）")
    ap.add_argument("--lang", help="目标语言（如 de-DE）")
    ap.add_argument("--source", help="英文源子串")
    ap.add_argument("--translation", help="任一语言译文子串")
    ap.add_argument("--key", help="OPUS ID 末段 logical key 子串")
    ap.add_argument("--project", help="project_id 子串（常为 GitLab 路径）")
    ap.add_argument("--limit", type=int, default=50, help="最多返回多少个 OPUS ID")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--db", help="自定义 opus_index.db 路径（默认用户主目录）")
    a = ap.parse_args(argv)
    try:
        res = search_index(
            opus_id=a.opus, opus_match=a.match, product=a.product,
            target_language=a.lang, source_contains=a.source,
            translation_contains=a.translation, logical_key_contains=a.key,
            project_contains=a.project, limit=a.limit, db_path=a.db,
        )
    except ValueError as e:
        print("错误:", e)
        return 2
    _print_results(res, a.json)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
