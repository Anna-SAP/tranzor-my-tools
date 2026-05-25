"""
JSON 导出器 — 统一格式，供翻译质量审计（QA）下游消费
====================================================
把"每行一条 (key, language) 翻译"的平铺数据，透视成
"每个 key 一条记录，包含所有目标语言"的字典结构，
schema 与 `/rc-core-products-trans-checker` 等 LQA Skill
期望的输入完全一致：

    [
      {
        "key": "RingCentral.bui.<hash>.STRING_KEY",
        "en-US": "Cancellation requested",
        "de-DE": "Kündigung beantragt",
        ...
      },
      ...
    ]

三个数据源（File Translation / MR Pipeline / Scan Tasks）的行 schema
略有差异，本模块自动归一化：

  - MR Pipeline / Scan Tasks (results dict):
        { "translations": [
            {"opus_id": ..., "target_language": ...,
             "source_text": ..., "translated_text": ...},
            ...
          ] }
  - File Translation (flat rows):
        [{"string_key": ..., "language": ...,
          "source_text": ..., "translated_text": ...,
          # 或 changes 模式下的 before/after
          "before": ..., "after": ...}, ...]

en-US 列的取值规则：
  1. 若存在显式的 en-US 行（target_language == "en-US"），优先用其 translated_text；
  2. 否则回退为该 key 任意一行的 source_text（en-US 通常就是源文）。
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict


# 列顺序：key 第一，en-US 第二（源语言），其余按字母序
_SOURCE_LANG = "en-US"


def _normalize_row(row):
    """归一化一条翻译行 → (key, lang, source_text, translated_text)。

    兼容两套字段命名：
      - MR/Scan 用 opus_id / target_language
      - File Translation 用 string_key / language
      - File Translation 的 "changes" 模式额外使用 before/after，
        我们取 after 作为最终译文。
    """
    key = row.get("opus_id") or row.get("string_key") or ""
    lang = row.get("target_language") or row.get("language") or ""
    src = row.get("source_text") or ""
    # 优先取 translated_text；其次 after（changes 模式的最终值）
    tgt = row.get("translated_text")
    if tgt is None or tgt == "":
        tgt = row.get("after") or ""
    return key, lang, src, tgt


def build_json_entries(rows):
    """把平铺行透视成 [{key, en-US, de-DE, ...}, ...]。

    - 缺少 key 或 lang 的行被跳过；
    - 同 (key, lang) 多行时，最后一条覆盖先前（通常意味着是更新后的最终译文）；
    - en-US 缺失时回退为 source_text；
    - 输出按 key 升序排序，列顺序固定为 key → en-US → 其余语言（字母序），
      与现有 LQA 工具期望的格式一致。
    """
    # key -> { lang: translated, "__source__": source_text }
    by_key = OrderedDict()

    for row in rows or []:
        key, lang, src, tgt = _normalize_row(row)
        if not key or not lang:
            continue
        bucket = by_key.setdefault(key, {})
        # 记住一次非空源文（不同行的 source 应该一致）
        if src and not bucket.get("__source__"):
            bucket["__source__"] = src
        bucket[lang] = tgt

    entries = []
    for key in sorted(by_key.keys()):
        bucket = by_key[key]
        source = bucket.pop("__source__", "")

        # en-US 缺失时用 source_text 兜底
        if _SOURCE_LANG not in bucket and source:
            bucket[_SOURCE_LANG] = source

        ordered = OrderedDict()
        ordered["key"] = key
        if _SOURCE_LANG in bucket:
            ordered[_SOURCE_LANG] = bucket[_SOURCE_LANG]
        for lang in sorted(l for l in bucket if l != _SOURCE_LANG):
            ordered[lang] = bucket[lang]
        entries.append(ordered)

    return entries


def write_translations_json(payload, filename):
    """把翻译数据写成 LQA 工具期望的 JSON 文件。

    payload 可以是：
      - dict（含 ``translations`` 列表）   → MR Pipeline / Scan Tasks
      - list（平铺行）                     → File Translation

    返回写入的条目列表（便于调用方记日志或测试）。
    """
    if isinstance(payload, dict):
        rows = payload.get("translations") or []
    else:
        rows = payload or []

    entries = build_json_entries(rows)

    # 始终用 UTF-8 + ensure_ascii=False 让非英文字符直接可读；
    # indent=2 与 BUI 样例对齐，便于人工审查。
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(
        f"已导出: {filename}  "
        f"({len(entries)} 个 key，覆盖 "
        f"{len({l for e in entries for l in e if l != 'key'})} 种语言)"
    )
    return entries


def save_json_file(payload, filename):
    """带"文件被占用自动加序号"的安全写入入口，对齐
    ``export_mr_pipeline.save_mr_file`` / ``export_translations.save_file``
    的行为，保证 GUI 反复导出时不会因为上一份还开着而失败。
    """
    base, ext = os.path.splitext(filename)
    save_path = filename
    for attempt in range(100):
        try:
            write_translations_json(payload, save_path)
            return save_path
        except PermissionError:
            attempt_num = attempt + 1
            save_path = f"{base}_{attempt_num}{ext}"
            print(f"  文件被占用，尝试保存为: {save_path}")
    return None
