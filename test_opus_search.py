"""opus_search.search_index 的单元测试。

既可在 pytest 下运行，也可直接 ``python test_opus_search.py`` 独立运行
（不依赖 pytest fixture —— 仓库当前环境未安装 pytest 时也能验证）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_id_monitor as om  # noqa: E402
import opus_search as osr  # noqa: E402


# (opus_id, lang, task_id, alias, path_hash, logical_key, project_id, release,
#  source_text, translated_text, source_file_path, source_kind, mr_iid,
#  task_created_at, first_seen)
_COLS = (
    "opus_id", "target_language", "task_id", "alias", "path_hash",
    "logical_key", "project_id", "release", "source_text", "translated_text",
    "source_file_path", "source_kind", "mr_iid", "task_created_at", "first_seen",
)

_UNS = "RingCentral.uns.hash1.welcome.title"
_SCP = "RingCentral.scp.hash2.button.save"

_ROWS = [
    # UNS welcome.title —— de/fr，外加一条更新的 de 任务（应覆盖旧 de）
    (_UNS, "de-DE", "t1", "uns", "hash1", "welcome.title", "common/uns", "26.2",
     "Welcome", "Willkommen (old)",
     "uns-app/metadataStorage/translations/Translations_en_US.properties",
     "file", None, "2026-06-01", "2026-06-01T00:00:00Z"),
    (_UNS, "fr-FR", "t1", "uns", "hash1", "welcome.title", "common/uns", "26.2",
     "Welcome", "Bienvenue",
     "uns-app/metadataStorage/translations/Translations_en_US.properties",
     "file", None, "2026-06-01", "2026-06-01T00:00:00Z"),
    (_UNS, "de-DE", "t2", "uns", "hash1", "welcome.title", "common/uns", "26.3",
     "Welcome", "Willkommen!",  # 更新版，first_seen 更晚 → 应为返回值
     "uns-app/metadataStorage/translations/Translations_en_US.properties",
     "file", None, "2026-06-05", "2026-06-05T00:00:00Z"),
    # SCP button.save —— de，含一个百分号源文用于 LIKE 转义测试
    (_SCP, "de-DE", "t3", "scp", "hash2", "button.save", "admin-web/frontend",
     "26.2", "Save 100% now", "Speichern",
     "src/locales/en-US/common.json", "mr", 444, "2026-06-03",
     "2026-06-03T00:00:00Z"),
]


def _mkdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # init_db 会重新创建
    om.init_db(path)
    placeholders = ",".join("?" * len(_COLS))
    with om._connect(path) as c:
        c.executemany(
            f"INSERT OR REPLACE INTO opus_index ({','.join(_COLS)}) "
            f"VALUES ({placeholders})",
            _ROWS,
        )
    return path


def test_exact_opus_id_returns_all_langs_latest_wins():
    db = _mkdb()
    res = osr.search_index(opus_id=_UNS, db_path=db)
    assert res["count"] == 1
    card = res["results"][0]
    assert card["opus_id"] == _UNS
    assert card["alias"] == "uns"
    langs = {t["target_language"]: t["translated_text"] for t in card["translations"]}
    assert langs == {"de-DE": "Willkommen!", "fr-FR": "Bienvenue"}  # 最新 de 覆盖旧 de
    assert card["source_text"] == "Welcome"


def test_product_filter():
    db = _mkdb()
    assert osr.search_index(product="uns", db_path=db)["count"] == 1
    res = osr.search_index(product="scp", db_path=db)
    assert res["count"] == 1
    assert res["results"][0]["opus_id"] == _SCP


def test_prefix_match():
    db = _mkdb()
    res = osr.search_index(opus_id="RingCentral.uns.", opus_match="prefix", db_path=db)
    assert res["count"] == 1 and res["results"][0]["opus_id"] == _UNS
    # 前缀不匹配 scp
    assert osr.search_index(opus_id="RingCentral.scp.", opus_match="prefix",
                            db_path=db)["results"][0]["opus_id"] == _SCP


def test_source_and_translation_contains():
    db = _mkdb()
    assert osr.search_index(source_contains="Welcome", db_path=db)["count"] == 1
    res = osr.search_index(translation_contains="Speichern", db_path=db)
    assert res["count"] == 1 and res["results"][0]["opus_id"] == _SCP


def test_language_filter_combined():
    db = _mkdb()
    # fr-FR 只在 UNS 里有
    res = osr.search_index(product="uns", target_language="fr-FR", db_path=db)
    assert res["count"] == 1
    # scp 没有 fr-FR → 0
    assert osr.search_index(product="scp", target_language="fr-FR", db_path=db)["count"] == 0


def test_like_escaping_percent_is_literal():
    db = _mkdb()
    # 源文 "Save 100% now" 含字面 %；搜 "100%" 应命中它，而不是被当通配符
    res = osr.search_index(source_contains="100%", db_path=db)
    assert res["count"] == 1 and res["results"][0]["opus_id"] == _SCP
    # 搜一个不存在的字面 % 串应为 0（验证 % 没被当通配符匹配一切）
    assert osr.search_index(source_contains="zzz%zzz", db_path=db)["count"] == 0


def test_no_filter_raises():
    db = _mkdb()
    raised = False
    try:
        osr.search_index(db_path=db)
    except ValueError:
        raised = True
    assert raised, "无收窄条件时应抛 ValueError"
    # 仅 target_language 也不够
    raised = False
    try:
        osr.search_index(target_language="de-DE", db_path=db)
    except ValueError:
        raised = True
    assert raised, "仅 target_language 时应抛 ValueError"


def test_limit_and_truncation():
    db = _mkdb()
    # 两个 distinct opus_id；limit=1 → 截断
    res = osr.search_index(translation_contains="e", db_path=db, limit=1)
    assert res["count"] == 1 and res["truncated"] is True


def _run_standalone():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(_run_standalone())
