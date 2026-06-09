"""
GitLab 产品仓库全量基线摄取 (Phase 1b)
======================================
把 GitLab 产品仓库里 *已合并* 的全套本地化文件解析进本地索引 ``opus_index.db``，
让 OPUS 搜索从「最新但不完整（仅 Tranzor 经手过的增量）」升级为「最新 + 最全」。

设计（用户选定 Option D —— 纯 Python，无 Node）：
    - 不从零重建 path_hash（实测 .ts / UNS 的 path_hash 由 l10n-cli 仓库级
      约定决定，不可简单 md5 反推）。改为：
        * 若某文件已有 key 进过索引 → **借用**该文件的真实 path_hash（与平台一致，
          因为同一源文件的所有 key 共享同一 path_hash，已验证）；
        * 否则用 ``md5(源相对路径)`` 作为**本工具自有**的派生 path_hash（来源标记
          source_kind='gitlab'，不冒充平台值）。
    - 搜索按 产品别名 + logical key + 源文/译文 命中即可（见 opus_search）。

当前已实现格式：``properties``（Java .properties，UNS metadataStorage）。
其余格式（ts / json / po / strings / xml / hbs）后续增量接入；本模块按 format
分派，未实现的格式会显式报错而非静默产出错误数据。

数据写入 ``opus_index`` 表，``source_kind='gitlab'``，可按此清理 / 重摄取。

用法（CLI）::

    python repo_corpus.py ingest --product UNS              # 摄取进默认 opus_index.db
    python repo_corpus.py ingest --product UNS --dry-run    # 只解析报数，不写库
    python repo_corpus.py ingest --product UNS --ref master --db /tmp/test.db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_id_monitor as om  # noqa: E402  复用 _connect/init_db/md5_path


# UNS 的目标 locale token（文件名里用下划线）。包含 en_ZZ(伪)/en_CA 等仓库实际存在的。
# 缺失的文件 get_file_raw 返回 None 会被跳过，因此多列无害。
_DEFAULT_LOCALE_TOKENS = [
    "en_US", "de_DE", "en_AU", "en_CA", "en_GB", "es_ES", "es_419",
    "fr_FR", "fr_CA", "it_IT", "nl_NL", "pt_BR", "pt_PT", "fi_FI",
    "ko_KR", "ja_JP", "zh_CN", "zh_TW", "zh_HK", "en_ZZ",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _locale_token_to_lang(token: str) -> str:
    """``en_US`` → ``en-US``（与 opus_index.target_language 的连字符风格对齐）。"""
    return token.replace("_", "-")


# ---------------------------------------------------------------------------
# .properties 解析（Java 风格：key=value / key:value / 续行 / # ! 注释 / \u 转义）
# ---------------------------------------------------------------------------
def _ends_with_odd_backslash(s: str) -> bool:
    n = 0
    i = len(s) - 1
    while i >= 0 and s[i] == "\\":
        n += 1
        i -= 1
    return n % 2 == 1


def _unescape(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "u" and i + 5 < n + 1 and len(s[i + 2:i + 6]) == 4:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            mapping = {"n": "\n", "t": "\t", "r": "\r", "f": "\f"}
            out.append(mapping.get(nxt, nxt))  # \= \: \\ \space → 字面
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _split_kv(s: str):
    """找第一个未转义的 = / : / 空白作为分隔符。返回 (key, value) 或 (None, None)。"""
    i = 0
    n = len(s)
    # 跳过 key 前导空白已在外层处理；这里找分隔符
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c in "=:":
            return s[:i].strip(), s[i + 1:].strip()
        if c in " \t\f":
            # 空白分隔：但 = / : 优先；向后看是否有 = / :
            j = i
            while j < n and s[j] in " \t\f":
                j += 1
            if j < n and s[j] in "=:":
                return s[:i].strip(), s[j + 1:].strip()
            return s[:i].strip(), s[j:].strip()
        i += 1
    return s.strip(), ""  # 只有 key 没有值


def parse_properties(text: str) -> dict:
    """解析 .properties 文本为 {key: value}（保序）。"""
    out: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        i += 1
        stripped = raw.lstrip()
        if not stripped or stripped[0] in "#!":
            continue
        logical = raw
        # 续行：当前物理行以奇数个反斜杠结尾 → 拼接下一行（去其前导空白）
        while _ends_with_odd_backslash(logical) and i < n:
            logical = logical[:-1] + lines[i].lstrip()
            i += 1
        s = logical.strip()
        if not s or s[0] in "#!":
            continue
        key, val = _split_kv(s)
        if not key:
            continue
        out[_unescape(key)] = _unescape(val)
    return out


# ---------------------------------------------------------------------------
# products.json
# ---------------------------------------------------------------------------
def load_products(path: str | None = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "products.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_product(name: str, products: dict | None = None) -> dict:
    products = products or load_products()
    for p in products.get("products", []):
        if p.get("name", "").lower() == name.lower() or name.lower() in [
                a.lower() for a in p.get("aliases", [])]:
            return p
    raise KeyError(f"products.json 里找不到产品 {name!r}")


# ---------------------------------------------------------------------------
# path_hash 选择：优先借用索引里同 (alias, key) 的真实 path_hash
# ---------------------------------------------------------------------------
def _resolve_path_hash(conn, alias: str, key: str, source_rel_path: str) -> tuple[str, bool]:
    """返回 (path_hash, borrowed)。

    借用条件：索引里该 (alias, logical_key) 只对应唯一 path_hash（说明同一
    平台文件），直接复用 → 与平台一致。否则用 md5(源相对路径) 作派生值。
    """
    rows = conn.execute(
        "SELECT DISTINCT path_hash FROM opus_index "
        "WHERE alias=? AND logical_key=? AND path_hash IS NOT NULL "
        "AND path_hash<>''",
        (alias, key)).fetchall()
    if len(rows) == 1 and rows[0]["path_hash"]:
        return rows[0]["path_hash"], True
    return om.md5_path(source_rel_path), False


# ---------------------------------------------------------------------------
# properties 产品摄取
# ---------------------------------------------------------------------------
def ingest_properties_product(
    product: dict,
    *,
    ref: str = "master",
    client=None,
    db_path: str | None = None,
    locale_tokens: list[str] | None = None,
    dry_run: bool = False,
    progress=None,
) -> dict:
    """摄取一个 properties 类产品（如 UNS metadataStorage）。

    从 ``locale_globs`` 里取 properties glob，按 locale token 展开成各语言文件，
    逐个 ``get_file_raw`` → 解析 → 按 key 汇成 {lang: value} → 写入 opus_index。

    Returns 统计 dict。client 需提供 ``get_file_raw(project, path, ref)``，
    便于单测注入假客户端。
    """
    alias = (product.get("aliases") or [product.get("name", "")])[0]
    gitlab = product.get("gitlab")
    if not gitlab:
        raise ValueError(f"产品 {product.get('name')} 未配置 gitlab 路径")
    globs = [g for g in product.get("locale_globs", []) if g.endswith(".properties")]
    if not globs:
        raise ValueError(f"产品 {product.get('name')} 无 .properties locale_glob")
    src_token = product.get("source_locale_token", "en_US")
    tokens = locale_tokens or _DEFAULT_LOCALE_TOKENS
    if client is None:
        import gitlab_client as gc
        client = gc.GitLabClient()

    def _log(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    glob = globs[0]  # 形如 uns-app/metadataStorage/translations/Translations_*.properties
    # key -> {lang_token: value}
    by_key: dict[str, dict[str, str]] = {}
    files_seen = 0
    for token in tokens:
        path = glob.replace("*", token)
        raw = client.get_file_raw(gitlab, path, ref)
        if raw is None:
            continue
        files_seen += 1
        kv = parse_properties(raw)
        _log(f"{path}: {len(kv)} keys")
        for k, v in kv.items():
            by_key.setdefault(k, {})[token] = v

    source_rel_path = glob.replace("*", src_token)
    stats = {
        "product": product.get("name"), "alias": alias, "gitlab": gitlab,
        "ref": ref, "files_seen": files_seen, "keys": len(by_key),
        "rows": 0, "borrowed_hashes": 0, "dry_run": dry_run,
    }
    if dry_run or not by_key:
        return stats

    now = _now_iso()
    task_id = f"gitlab:{product.get('name')}"
    om.init_db(db_path)
    with om._connect(db_path) as conn:
        rows = []
        borrowed = 0
        for key, lang_map in by_key.items():
            ph, was_borrowed = _resolve_path_hash(conn, alias, key, source_rel_path)
            if was_borrowed:
                borrowed += 1
            opus_id = f"RingCentral.{alias}.{ph}.{key}"
            source_text = lang_map.get(src_token, "")
            for token, val in lang_map.items():
                lang = _locale_token_to_lang(token)
                rows.append((
                    opus_id, lang, task_id, alias, ph, key, gitlab, None,
                    source_text, val, source_rel_path, "gitlab", None, ref, now,
                ))
        conn.executemany(
            "INSERT OR REPLACE INTO opus_index "
            "(opus_id, target_language, task_id, alias, path_hash, logical_key, "
            " project_id, release, source_text, translated_text, "
            " source_file_path, source_kind, mr_iid, task_created_at, first_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        stats["rows"] = len(rows)
        stats["borrowed_hashes"] = borrowed
    return stats


# ---------------------------------------------------------------------------
# 分派 + CLI
# ---------------------------------------------------------------------------
def ingest_product(name: str, *, ref: str = "master", db_path: str | None = None,
                   dry_run: bool = False, progress=None) -> dict:
    product = find_product(name)
    fmts = product.get("formats", [])
    if "properties" in fmts:
        return ingest_properties_product(
            product, ref=ref, db_path=db_path, dry_run=dry_run, progress=progress)
    raise NotImplementedError(
        f"产品 {name} 的格式 {fmts} 尚未实现摄取（当前仅支持 properties）。")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="repo_corpus",
                                 description="GitLab 产品仓库全量基线摄取 (Phase 1b)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ig = sub.add_parser("ingest", help="摄取一个产品的全量本地化到本地索引")
    ig.add_argument("--product", required=True, help="产品名或别名（见 products.json）")
    ig.add_argument("--ref", default="master", help="GitLab 分支/标签，默认 master")
    ig.add_argument("--db", default=None, help="自定义 opus_index.db 路径")
    ig.add_argument("--dry-run", action="store_true", help="只解析报数，不写库")
    a = ap.parse_args(argv)
    if a.cmd == "ingest":
        try:
            stats = ingest_product(a.product, ref=a.ref, db_path=a.db,
                                   dry_run=a.dry_run, progress=lambda m: print("  ", m))
        except Exception as e:  # noqa: BLE001
            print("错误:", e)
            return 2
        print("\n摄取结果:", json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
