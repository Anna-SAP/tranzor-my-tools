"""repo_corpus 单元测试（pytest 兼容 + 独立可跑）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_id_monitor as om  # noqa: E402
import opus_search as osr  # noqa: E402
import repo_corpus as rc  # noqa: E402


# ----------------------------- parse_properties -----------------------------
def test_parse_basic_separators():
    txt = "A=All\nB:PIN\nC  Greeting\n"
    d = rc.parse_properties(txt)
    assert d == {"A": "All", "B": "PIN", "C": "Greeting"}


def test_parse_comments_and_blank():
    txt = "# comment\n! also comment\n\nKEY = Value \n"
    assert rc.parse_properties(txt) == {"KEY": "Value"}


def test_parse_continuation():
    txt = "MSG=Hello \\\n  World\n"
    assert rc.parse_properties(txt) == {"MSG": "Hello World"}


def test_parse_escapes_and_unicode():
    txt = r"K1=a\=b" + "\n" + r"K2=éclair" + "\n" + r"K3=line\nbreak" + "\n"
    d = rc.parse_properties(txt)
    assert d["K1"] == "a=b"
    assert d["K2"] == "éclair"
    assert d["K3"] == "line\nbreak"


# ----------------------------- fake GitLab client ---------------------------
class FakeClient:
    def __init__(self, by_path):
        self.by_path = by_path

    def get_file_raw(self, project, path, ref):
        return self.by_path.get(path)


_PROD = {
    "name": "FAKEUNS", "aliases": ["fakeuns"], "gitlab": "common/fakeuns",
    "formats": ["properties"],
    "locale_globs": ["x/Translations_*.properties"],
    "source_locale_token": "en_US",
}


def _files():
    return {
        "x/Translations_en_US.properties": "ALL=All\nPIN=PIN\n# c\nGREETING=Hello",
        "x/Translations_de_DE.properties": "ALL=Alle\nGREETING=Hallo",
    }


def _mkdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    om.init_db(path)
    return path


def test_ingest_rows_and_searchable():
    db = _mkdb()
    stats = rc.ingest_properties_product(
        _PROD, ref="master", client=FakeClient(_files()),
        db_path=db, locale_tokens=["en_US", "de_DE"])
    assert stats["files_seen"] == 2
    assert stats["keys"] == 3                 # ALL, PIN, GREETING
    assert stats["rows"] == 5                 # en_US x3 + de_DE x2 (PIN 无 de)

    # 可被 opus_search 命中：产品 + 源文
    res = osr.search_index(product="fakeuns", source_contains="Hello", db_path=db)
    assert res["count"] == 1
    card = res["results"][0]
    langs = {t["target_language"]: t["translated_text"] for t in card["translations"]}
    assert langs == {"en-US": "Hello", "de-DE": "Hallo"}

    # 精确 opus_id（派生 path_hash = md5(源相对路径)）可被 get_opus_detail 取到
    h = om.md5_path("x/Translations_en_US.properties")
    detail = om.get_opus_detail(f"RingCentral.fakeuns.{h}.GREETING", db_path=db)
    assert detail["found"] is True
    assert detail["source_kind"] == "gitlab"


def test_path_hash_borrowed_from_index():
    db = _mkdb()
    # 预置一条 Tranzor 经手的 ALL（带平台 path_hash），模拟"该文件已部分入库"
    with om._connect(db) as c:
        c.execute(
            "INSERT INTO opus_index (opus_id, target_language, task_id, alias, "
            "path_hash, logical_key, source_kind, first_seen) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("RingCentral.fakeuns.PLATFORMHASH.ALL", "en-US", "t-old",
             "fakeuns", "PLATFORMHASH", "ALL", "mr", "2026-06-01T00:00:00Z"))
    stats = rc.ingest_properties_product(
        _PROD, client=FakeClient(_files()), db_path=db,
        locale_tokens=["en_US", "de_DE"])
    assert stats["borrowed_hashes"] >= 1
    # ALL 应借用平台 path_hash → opus_id 用 PLATFORMHASH
    res = osr.search_index(opus_id="RingCentral.fakeuns.PLATFORMHASH.ALL",
                           db_path=db)
    assert res["count"] == 1
    # GREETING 没进过库 → 用派生 md5
    h = om.md5_path("x/Translations_en_US.properties")
    assert osr.search_index(opus_id=f"RingCentral.fakeuns.{h}.GREETING",
                            db_path=db)["count"] == 1


def test_idempotent_reingest():
    db = _mkdb()
    fc = FakeClient(_files())
    rc.ingest_properties_product(_PROD, client=fc, db_path=db,
                                 locale_tokens=["en_US", "de_DE"])
    with om._connect(db) as c:
        n1 = c.execute("SELECT COUNT(*) FROM opus_index").fetchone()[0]
    rc.ingest_properties_product(_PROD, client=fc, db_path=db,
                                 locale_tokens=["en_US", "de_DE"])
    with om._connect(db) as c:
        n2 = c.execute("SELECT COUNT(*) FROM opus_index").fetchone()[0]
    assert n1 == n2 == 5  # INSERT OR REPLACE，重复摄取不增行


def test_dry_run_writes_nothing():
    db = _mkdb()
    stats = rc.ingest_properties_product(
        _PROD, client=FakeClient(_files()), db_path=db,
        locale_tokens=["en_US", "de_DE"], dry_run=True)
    assert stats["keys"] == 3 and stats["rows"] == 0
    with om._connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM opus_index").fetchone()[0] == 0


# ----------------------------- strip-aware path hashing ---------------------
def test_md5_path_strip_matches_platform_replace():
    import hashlib
    p = "src/locales/en/translation.json"
    # 无 strip：等价旧行为（原始路径 md5）
    assert om.md5_path(p) == hashlib.md5(p.encode("utf-8")).hexdigest()
    # strip='en'：平台朴素 replace（去掉所有 'en' 出现）后再 md5
    stripped = p.replace("en", "")
    assert om.md5_path(p, strip="en") == hashlib.md5(stripped.encode("utf-8")).hexdigest()
    # 剥后与原始 hash 不同（否则说明没生效）
    assert om.md5_path(p, strip="en") != om.md5_path(p)
    # 空 strip 与缺省一致
    assert om.md5_path(p, strip="") == om.md5_path(p)


def test_resolve_path_hash_uses_strip_when_deriving():
    db = _mkdb()
    with om._connect(db) as conn:  # 空库 → 无可借用 → 走派生
        ph, borrowed = rc._resolve_path_hash(
            conn, "novaStudio", "some.key",
            "src/locales/en/translation.json", strip="en")
    assert borrowed is False
    assert ph == om.md5_path("src/locales/en/translation.json", strip="en")
    assert ph != om.md5_path("src/locales/en/translation.json")


def test_ingest_threads_strip_into_derived_hash():
    db = _mkdb()
    prod = dict(_PROD, aliases=["stripped"], strip="en_US")
    rc.ingest_properties_product(
        prod, client=FakeClient(_files()), db_path=db,
        locale_tokens=["en_US", "de_DE"])
    # 源相对路径 = glob 用 en_US 展开；派生 hash 应剥掉 'en_US'
    src_rel = "x/Translations_en_US.properties"
    h = om.md5_path(src_rel, strip="en_US")
    assert osr.search_index(
        opus_id=f"RingCentral.stripped.{h}.GREETING", db_path=db)["count"] == 1
    # 反证：不剥的 hash 不应命中
    h_raw = om.md5_path(src_rel)
    assert osr.search_index(
        opus_id=f"RingCentral.stripped.{h_raw}.GREETING", db_path=db)["count"] == 0


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
