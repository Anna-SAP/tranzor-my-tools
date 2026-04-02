"""
质量概览模块
=============
质量数据聚合与可视化，支持 tkinter Canvas 图表 + HTML / Excel 导出。
"""

import html as html_mod
import math
import os
import platform
import webbrowser
from collections import OrderedDict

# ---------------------------------------------------------------------------
# 跨平台字体适配
# ---------------------------------------------------------------------------
if platform.system() == "Darwin":  # macOS
    FONT_FAMILY = "Helvetica Neue"
else:  # Windows / Linux
    FONT_FAMILY = "Segoe UI"


# ---------------------------------------------------------------------------
# 1) 数据聚合
# ---------------------------------------------------------------------------
SCORE_BINS = [
    ("0–60",  0,  60),
    ("60–80", 60, 80),
    ("80–90", 80, 90),
    ("90–95", 90, 95),
    ("95–100", 95, 101),   # 101 = inclusive upper bound for 100
]

LOW_SCORE_THRESHOLD = 98

ERROR_CATEGORIES = [
    "Accuracy", "Fluency", "Terminology", "Consistency", "Locale Convention"
]


def aggregate_quality_data(overview_data, cases_data):
    """从 dashboard overview + cases 聚合质量分析数据"""
    # --- 从 overview 获取概要 ---
    total_tasks = overview_data.get("total_tasks", 0)
    completed = overview_data.get("completed", 0)
    failed = overview_data.get("failed", 0)
    total_translations = overview_data.get("total_translations", 0)
    overall_avg_score = overview_data.get("average_score", 0)

    # --- 从 cases 遍历逐条翻译 ---
    all_items = []  # flat list of translation items
    mrs = cases_data.get("merge_requests", [])
    for mr in mrs:
        for item in mr.get("translations", []):
            all_items.append(item)

    if not all_items and total_translations == 0:
        total_translations = 0

    # Recompute from items if we have them
    if all_items:
        total_translations = len(all_items)
        scored_items = [it for it in all_items if it.get("final_score") is not None]
        if scored_items:
            overall_avg_score = sum(it["final_score"] for it in scored_items) / len(scored_items)

    # Low-score items
    low_items = [
        it for it in all_items
        if it.get("final_score") is not None and it["final_score"] < LOW_SCORE_THRESHOLD
    ]

    # Error category distribution
    err_dist = {}
    for cat in ERROR_CATEGORIES:
        err_dist[cat] = 0
    for it in all_items:
        cat = it.get("error_category")
        if cat and cat in err_dist:
            err_dist[cat] += 1
        elif cat:
            err_dist[cat] = err_dist.get(cat, 0) + 1

    # Score distribution
    score_dist = OrderedDict()
    for label, lo, hi in SCORE_BINS:
        score_dist[label] = 0
    for it in all_items:
        s = it.get("final_score")
        if s is None:
            continue
        for label, lo, hi in SCORE_BINS:
            if lo <= s < hi:
                score_dist[label] += 1
                break

    # By-language breakdown
    by_lang = {}
    for it in all_items:
        lang = it.get("target_language", "(unknown)")
        if lang not in by_lang:
            by_lang[lang] = {
                "count": 0, "score_sum": 0, "scored_count": 0,
                "critical": 0, "major": 0, "minor": 0,
            }
        entry = by_lang[lang]
        entry["count"] += 1
        score = it.get("final_score")
        if score is not None:
            entry["score_sum"] += score
            entry["scored_count"] += 1
        # Severity counts from item-level data
        entry["critical"] += it.get("critical_count", 0)
        entry["major"] += it.get("major_count", 0)
        entry["minor"] += it.get("minor_count", 0)

    lang_details = []
    for lang, d in sorted(by_lang.items()):
        avg = d["score_sum"] / d["scored_count"] if d["scored_count"] else None
        lang_details.append({
            "language": lang,
            "count": d["count"],
            "average_score": round(avg, 1) if avg is not None else None,
            "critical": d["critical"],
            "major": d["major"],
            "minor": d["minor"],
        })

    return {
        "total_tasks": total_tasks,
        "completed": completed,
        "failed": failed,
        "total_translations": total_translations,
        "overall_avg_score": round(overall_avg_score, 1) if overall_avg_score else 0,
        "low_score_count": len(low_items),
        "low_score_threshold": LOW_SCORE_THRESHOLD,
        "error_distribution": err_dist,
        "score_distribution": score_dist,
        "by_language": lang_details,
        "low_items": low_items,
    }


# ---------------------------------------------------------------------------
# 2) tkinter Canvas 图表
# ---------------------------------------------------------------------------
PIE_COLORS = [
    "#4472C4", "#E67E22", "#27AE60", "#8E44AD",
    "#E74C3C", "#16A085", "#D4AC0D", "#2C3E50",
]

BAR_COLORS = [
    "#E74C3C", "#E67E22", "#D4AC0D", "#4472C4", "#27AE60",
]


def draw_pie_chart(canvas, data, width, height, title=""):
    """在 tkinter Canvas 上绘制饼图
    data: dict { label: count, ... }
    """
    canvas.delete("all")

    # Title
    if title:
        canvas.create_text(width // 2, 16, text=title,
                           fill="#ccc", font=(FONT_FAMILY, 11, "bold"))

    total = sum(data.values())
    if total == 0:
        canvas.create_text(width // 2, height // 2, text="No Data",
                           fill="#666", font=(FONT_FAMILY, 10))
        return

    cx, cy = width // 2 - 40, height // 2 + 10
    r = min(width, height) // 2 - 40
    if r < 30:
        r = 30

    start = 0
    legend_items = []
    for i, (label, count) in enumerate(data.items()):
        if count == 0:
            continue
        extent = (count / total) * 360
        color = PIE_COLORS[i % len(PIE_COLORS)]
        canvas.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=start, extent=extent,
            fill=color, outline="#1a1a2e", width=1,
            style="pieslice"
        )
        legend_items.append((label, count, color))
        start += extent

    # Legend
    lx = cx + r + 24
    ly = cy - r + 10
    for i, (label, count, color) in enumerate(legend_items):
        y = ly + i * 20
        canvas.create_rectangle(lx, y, lx + 12, y + 12, fill=color, outline="")
        pct = (count / total * 100) if total else 0
        canvas.create_text(
            lx + 18, y + 6, anchor="w",
            text=f"{label}: {count} ({pct:.0f}%)",
            fill="#bbb", font=(FONT_FAMILY, 9)
        )


def draw_bar_chart(canvas, data, width, height, title=""):
    """在 tkinter Canvas 上绘制柱状图
    data: OrderedDict { label: count, ... }
    """
    canvas.delete("all")

    # Title
    if title:
        canvas.create_text(width // 2, 16, text=title,
                           fill="#ccc", font=(FONT_FAMILY, 11, "bold"))

    labels = list(data.keys())
    values = list(data.values())
    max_val = max(values) if values else 1
    if max_val == 0:
        max_val = 1

    n = len(labels)
    if n == 0:
        canvas.create_text(width // 2, height // 2, text="No Data",
                           fill="#666", font=(FONT_FAMILY, 10))
        return

    pad_left = 50
    pad_right = 20
    pad_top = 40
    pad_bottom = 40
    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bottom
    bar_w = chart_w // n - 8
    if bar_w < 10:
        bar_w = 10

    # Draw bars
    for i, (label, val) in enumerate(zip(labels, values)):
        x = pad_left + i * (chart_w // n) + (chart_w // n - bar_w) // 2
        bar_h = int((val / max_val) * chart_h)
        y_top = pad_top + chart_h - bar_h
        y_bot = pad_top + chart_h
        color = BAR_COLORS[i % len(BAR_COLORS)]

        canvas.create_rectangle(x, y_top, x + bar_w, y_bot,
                                fill=color, outline="")

        # Value label on top
        canvas.create_text(x + bar_w // 2, y_top - 8, text=str(val),
                           fill="#ccc", font=(FONT_FAMILY, 9, "bold"))

        # X-axis label
        canvas.create_text(x + bar_w // 2, y_bot + 14, text=label,
                           fill="#888", font=(FONT_FAMILY, 8))

    # Y-axis line
    canvas.create_line(pad_left - 2, pad_top, pad_left - 2,
                       pad_top + chart_h, fill="#444", width=1)
    # X-axis line
    canvas.create_line(pad_left - 2, pad_top + chart_h,
                       width - pad_right, pad_top + chart_h, fill="#444", width=1)


# ---------------------------------------------------------------------------
# 3) HTML 导出 — 质量报告
# ---------------------------------------------------------------------------
def write_quality_html(aggregated, filename, label):
    """生成质量报告 HTML"""
    a = aggregated

    # Error distribution as chart bars (inline CSS)
    err_bars = ""
    err_total = sum(a["error_distribution"].values())
    for i, (cat, count) in enumerate(a["error_distribution"].items()):
        pct = (count / err_total * 100) if err_total else 0
        color = PIE_COLORS[i % len(PIE_COLORS)]
        err_bars += (
            f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0;">'
            f'<span style="min-width:140px;font-size:13px;">{html_mod.escape(cat)}</span>'
            f'<div style="background:#eee;border-radius:6px;flex:1;height:20px;">'
            f'<div style="background:{color};border-radius:6px;height:100%;width:{pct:.1f}%;"></div>'
            f'</div>'
            f'<span style="min-width:60px;font-size:12px;color:#888;">{count} ({pct:.0f}%)</span>'
            f'</div>'
        )

    # Score distribution as chart
    score_bars = ""
    score_max = max(a["score_distribution"].values()) if a["score_distribution"] else 1
    if score_max == 0:
        score_max = 1
    for i, (label_text, count) in enumerate(a["score_distribution"].items()):
        pct = (count / score_max * 100)
        color = BAR_COLORS[i % len(BAR_COLORS)]
        score_bars += (
            f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0;">'
            f'<span style="min-width:60px;font-size:13px;">{label_text}</span>'
            f'<div style="background:#eee;border-radius:6px;flex:1;height:20px;">'
            f'<div style="background:{color};border-radius:6px;height:100%;width:{pct:.1f}%;"></div>'
            f'</div>'
            f'<span style="min-width:40px;font-size:12px;color:#888;">{count}</span>'
            f'</div>'
        )

    # Language detail table
    lang_rows = ""
    for ld in a["by_language"]:
        avg_str = f'{ld["average_score"]}' if ld["average_score"] is not None else "—"
        lang_rows += (
            f'<tr>'
            f'<td>{html_mod.escape(ld["language"])}</td>'
            f'<td style="text-align:center">{ld["count"]}</td>'
            f'<td style="text-align:center">{avg_str}</td>'
            f'<td style="text-align:center;color:#E74C3C">{ld["critical"]}</td>'
            f'<td style="text-align:center;color:#E67E22">{ld["major"]}</td>'
            f'<td style="text-align:center;color:#D4AC0D">{ld["minor"]}</td>'
            f'</tr>'
        )

    # Low-score items table
    low_rows = ""
    for i, it in enumerate(a["low_items"]):
        score = it.get("final_score", "—")
        score_style = ' style="color:#E74C3C;font-weight:bold"' if isinstance(score, (int, float)) and score < 80 else ""
        low_rows += (
            f'<tr>'
            f'<td class="num">{i + 1}</td>'
            f'<td class="key">{html_mod.escape(it.get("opus_id", ""))}</td>'
            f'<td>{html_mod.escape(it.get("target_language", ""))}</td>'
            f'<td>{html_mod.escape(it.get("source_text", ""))}</td>'
            f'<td>{html_mod.escape(it.get("translated_text", ""))}</td>'
            f'<td{score_style}>{score}</td>'
            f'<td>{html_mod.escape(it.get("error_category") or "—")}</td>'
            f'<td>{html_mod.escape(it.get("reason") or "")}</td>'
            f'</tr>'
        )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Quality Overview - {html_mod.escape(label)}</title>
<style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif; background:#f5f6fa; padding:24px; color:#333; }}
    h1 {{ font-size:20px; margin-bottom:4px; }}
    h2 {{ font-size:16px; margin:20px 0 12px; color:#2c3e50; }}
    .meta {{ color:#666; font-size:14px; margin-bottom:16px; }}

    .cards {{ display:flex; gap:16px; margin-bottom:24px; flex-wrap:wrap; }}
    .card {{
        background:#fff; border-radius:10px; padding:20px; min-width:180px;
        box-shadow:0 2px 8px rgba(0,0,0,.06); text-align:center; flex:1;
    }}
    .card-value {{ font-size:28px; font-weight:bold; }}
    .card-label {{ font-size:12px; color:#888; text-transform:uppercase; letter-spacing:.5px; margin-top:4px; }}
    .card-accent {{ color:#4472C4; }}
    .card-warn {{ color:#E74C3C; }}
    .card-ok {{ color:#27AE60; }}

    .chart-section {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,.06); }}

    table {{ border-collapse:collapse; width:100%; background:#fff; border-radius:6px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.08); font-size:13px; margin-bottom:20px; }}
    th {{ background:#4472C4; color:#fff; padding:10px 8px; text-align:left; white-space:nowrap; }}
    td {{ padding:8px; border-bottom:1px solid #e8e8e8; vertical-align:top; line-height:1.5; }}
    tr:hover {{ background:#f8f9fa; }}
    .num {{ text-align:center; color:#999; min-width:30px; }}
    .key {{ font-family:monospace; font-size:12px; max-width:200px; word-break:break-all; color:#555; }}
</style>
</head>
<body>
    <h1>📊 Quality Overview</h1>
    <p class="meta">{html_mod.escape(label)}</p>

    <div class="cards">
        <div class="card">
            <div class="card-value card-accent">{a['total_tasks']}</div>
            <div class="card-label">Total Tasks</div>
        </div>
        <div class="card">
            <div class="card-value card-accent">{a['total_translations']}</div>
            <div class="card-label">Translation Items</div>
        </div>
        <div class="card">
            <div class="card-value card-ok">{a['overall_avg_score']}</div>
            <div class="card-label">Average Score</div>
        </div>
        <div class="card">
            <div class="card-value card-warn">{a['low_score_count']}</div>
            <div class="card-label">Below {a['low_score_threshold']}</div>
        </div>
    </div>

    <div class="chart-section">
        <h2>Error Category Distribution</h2>
        {err_bars}
    </div>

    <div class="chart-section">
        <h2>Score Distribution</h2>
        {score_bars}
    </div>

    <h2>By Language Breakdown</h2>
    <table>
        <thead><tr>
            <th>Language</th><th>Count</th><th>Avg Score</th>
            <th>Critical</th><th>Major</th><th>Minor</th>
        </tr></thead>
        <tbody>{lang_rows}</tbody>
    </table>

    <h2>Low-Score Items (Score &lt; {a['low_score_threshold']})</h2>
    <table>
        <thead><tr>
            <th>#</th><th>String Key</th><th>Language</th>
            <th>Source</th><th>Translated</th><th>Score</th>
            <th>Error Category</th><th>Reason</th>
        </tr></thead>
        <tbody>{low_rows if low_rows else '<tr><td colspan="8" style="text-align:center;color:#888;">No low-score items</td></tr>'}</tbody>
    </table>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(page)


# ---------------------------------------------------------------------------
# 4) Excel 导出 — 质量报告
# ---------------------------------------------------------------------------
def write_quality_excel(aggregated, filename):
    """生成质量报告 Excel（多 Sheet）"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        print("错误: 缺少 openpyxl 包，请先运行: pip install openpyxl")
        return

    a = aggregated
    wb = Workbook()

    # --- Sheet 1: Summary ---
    ws = wb.active
    ws.title = "Summary"
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)

    summary_data = [
        ("Metric", "Value"),
        ("Total Tasks", a["total_tasks"]),
        ("Total Translations", a["total_translations"]),
        ("Average Score", a["overall_avg_score"]),
        (f"Low Score (< {a['low_score_threshold']})", a["low_score_count"]),
    ]
    for row_i, (metric, val) in enumerate(summary_data, 1):
        c1 = ws.cell(row=row_i, column=1, value=metric)
        c2 = ws.cell(row=row_i, column=2, value=val)
        if row_i == 1:
            c1.fill = header_fill
            c1.font = header_font
            c2.fill = header_fill
            c2.font = header_font
    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 15

    # --- Sheet 2: By Language ---
    ws2 = wb.create_sheet("By Language")
    lang_headers = ["Language", "Count", "Avg Score", "Critical", "Major", "Minor"]
    for col_i, h in enumerate(lang_headers, 1):
        cell = ws2.cell(row=1, column=col_i, value=h)
        cell.fill = header_fill
        cell.font = header_font

    for row_i, ld in enumerate(a["by_language"], 2):
        ws2.cell(row=row_i, column=1, value=ld["language"])
        ws2.cell(row=row_i, column=2, value=ld["count"])
        ws2.cell(row=row_i, column=3, value=ld["average_score"] if ld["average_score"] else "")
        ws2.cell(row=row_i, column=4, value=ld["critical"])
        ws2.cell(row=row_i, column=5, value=ld["major"])
        ws2.cell(row=row_i, column=6, value=ld["minor"])

    for i, w in enumerate([15, 10, 12, 10, 10, 10], 1):
        ws2.column_dimensions[chr(64 + i)].width = w

    # --- Sheet 3: Low-Score Items ---
    ws3 = wb.create_sheet("Low Score Items")
    low_headers = ["#", "String Key", "Language", "Source", "Translated",
                   "Score", "Error Category", "Reason"]
    for col_i, h in enumerate(low_headers, 1):
        cell = ws3.cell(row=1, column=col_i, value=h)
        cell.fill = header_fill
        cell.font = header_font

    for row_i, it in enumerate(a["low_items"], 2):
        ws3.cell(row=row_i, column=1, value=row_i - 1)
        ws3.cell(row=row_i, column=2, value=it.get("opus_id", ""))
        ws3.cell(row=row_i, column=3, value=it.get("target_language", ""))
        ws3.cell(row=row_i, column=4, value=it.get("source_text", ""))
        ws3.cell(row=row_i, column=5, value=it.get("translated_text", ""))
        score = it.get("final_score")
        score_cell = ws3.cell(row=row_i, column=6, value=score if score is not None else "")
        if score is not None and score < 80:
            score_cell.font = Font(color="E74C3C", bold=True)
        ws3.cell(row=row_i, column=7, value=it.get("error_category") or "")
        ws3.cell(row=row_i, column=8, value=it.get("reason") or "")

    for i, w in enumerate([6, 30, 10, 40, 40, 8, 18, 30], 1):
        ws3.column_dimensions[chr(64 + i)].width = w

    wb.save(filename)


# ---------------------------------------------------------------------------
# 5) 统一保存入口
# ---------------------------------------------------------------------------
def save_quality_file(aggregated, filename, label, fmt):
    """保存质量报告，文件被占用时自动加序号"""
    base, ext = os.path.splitext(filename)
    save_path = filename
    for attempt in range(100):
        try:
            if fmt == "html":
                write_quality_html(aggregated, save_path, label)
            else:
                write_quality_excel(aggregated, save_path)
            print(f"已导出: {save_path}")
            if fmt == "html":
                webbrowser.open(os.path.abspath(save_path))
            return save_path
        except PermissionError:
            attempt_num = attempt + 1
            save_path = f"{base}_{attempt_num}{ext}"
            print(f"  文件被占用，尝试保存为: {save_path}")
    return None
