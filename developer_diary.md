# 开发者日记：[export_changes.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py) 全生命周期

> **项目**：Tranzor 翻译变更导出工具
> **时间跨度**：2026-03-18
> **作者**：Anna Su & AI Pair Programmer
> **状态**：已交付，含 GUI + .exe 打包

---

## 1. 背景与初始目标 (The Genesis)

### 痛点

Tranzor 是内部翻译管理平台，翻译审校员在其中对机器翻译结果进行人工修订。这些修订是高质量翻译资产，但 Tranzor 与 XTM Cloud（正式的 CAT 平台）之间**缺少自动同步通道**——人工审校的成果无法回流到 XTM 的 Translation Memory 库中。

这意味着：
- 审校过的高质量翻译**无法被后续任务 leverage**，造成重复劳动
- 没有直观的报告来审查"谁改了什么"

### 预期目标

构建一个 Python 脚本，能够：
1. 从 Tranzor API 批量拉取所有人工翻译变更记录
2. 生成可视化 HTML 报告（按编辑者分组，含 Diff 高亮）
3. 支持导出 TMX 1.4 文件，直接导入 XTM TM 库

---

## 2. 初步分析与设计 (Analysis & Design)

### 需求拆解

```
Tranzor API ──→ 数据采集 ──→ 结构化报告 ──→ TMX 导出
     ↑                           ↑              ↑
  REST API              HTML + Diff 可视化    TMX 1.4 XML
  分页遍历              按 Editor 分组        按 Language 分组
```

### API 链路

通过对 Tranzor 平台的 L1-L4 架构分析（前序对话 `2f541d6f`），确认了三层 API 调用链：

1. `GET /tasks` — 获取所有已完成的 Task 列表
2. `GET /tasks/{id}/translations` — 获取每个 Task 的翻译条目，筛选 `Manual Edit` 类型
3. `GET /tasks/{id}/translations/{id}/edit-logs` — 获取每条翻译的编辑历史（before/after）

### 技术路线

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 报告格式 | 单体 HTML（内嵌 CSS + JS） | 分发简单，无需 Web 服务器 |
| Diff 算法 | 单词级 [word_diff_html()](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#255-276) | 粒度适中，红绿高亮直观 |
| TMX 生成 | 浏览器端 JS | 避免 Python 端引入 XML 依赖 |
| ZIP 打包 | JSZip CDN | 多语言导出需要打包 |

### 核心数据结构

```python
row = {
    "edit_id": "...",
    "edit_time": "2026-03-18T02:37:09Z",
    "editor": "Anna Su",
    "task_id": 42,
    "task_name": "[Tranzor] Product 26.2 LOC-24096",
    "language": "fr-CA",
    "string_key": "RingCentral.chc.8b41fc3c...",
    "source_text": "Voicemail",          # en-US 原文
    "before": "messagerie vocale",        # 修改前
    "after": "Messagerie vocale",         # 修改后
    "notes": "capitalization fix",
}
```

---

## 3. 开发与踩坑记录 (Development & Challenges)

### 3.1 初版实现

初版 [write_html()](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#278-962) 实现了基础报告：
- 编辑者分组 + 彩色 section header
- 单词级 Diff 高亮
- 复选框 + 工具栏 + JS TMX 生成

**里程碑**：报告成功生成，TMX 格式通过 XTM 导入验证。

### 3.2 🐛 Bug #1：TMX 索引错位

**现象**：导出 TMX 后，翻译内容与 String Key 不匹配。

**根因**：HTML 表格行按 `editor` 分组渲染，但 JSON 数据按原始顺序序列化。`data-idx` 指向的 JSON 索引与实际行内容不一致。

```python
# ❌ 错误：JSON 按原始 rows 顺序
rows_json = json.dumps([...for r in rows...])

# ✅ 修复：JSON 必须按与 HTML 相同的分组顺序
for editor_rows in groups.values():
    for r in editor_rows:
        js_rows.append({...})
```

**教训**：当存在两种数据视图（HTML 渲染顺序 vs JSON 数据顺序）时，必须确保索引映射一致。

### 3.3 🐛 Bug #2：TMX 目标语言错误

**现象**：用户选中 `fr-CA` 语言后导出，文件名和内容却是 [en_US-zh_CN.tmx](file:///d:/Downloads_D/Tranzor_Platform/tmx_ref_temp/en_US-zh_CN.tmx)。

**根因**：TMX 导出 JS 代码从错误的选择器获取语言信息，未正确关联到 JSON 数据中的 `language` 字段。

**修复**：确保 `buildTmx()` 从 `ROWS[idx].language` 读取语言，而非从 DOM 解析。

### 3.4 🐛 Bug #3：Export TMX 忽略筛选

**现象**：应用高级筛选后（显示 5/99），Export TMX 仍导出全部 99 条。

**根因**：选择器 `input.row-cb:checked` 包含了被 `row-hidden` 隐藏的行。

```javascript
// ❌ 导出全部已勾选（含隐藏行）
const checked = document.querySelectorAll('input.row-cb:checked');

// ✅ 只导出可见且已勾选
const checked = document.querySelectorAll('tr:not(.row-hidden) input.row-cb:checked');
```

**教训**：CSS 隐藏 ≠ DOM 删除。任何操作"已选择"集合的逻辑都必须考虑可见性。

### 3.5 ⚡ 性能问题：脚本运行极慢

**现象**：51 个 Task、数百条 edit log，脚本串行请求需要数分钟。

**根因**：三层 API 全部串行 → O(tasks × translations × logs) 次 HTTP 请求。

**第一版修复**：引入 `concurrent.futures.ThreadPoolExecutor`，Task 级和 edit-log 级双层并发。

```python
MAX_WORKERS = 8  # 初始值
_session = requests.Session()  # 连接池复用

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = [pool.submit(_process_single_task, t) for t in task_infos]
```

### 3.6 🐛 Bug #4：并发引发 `TimeoutError`

**现象**：优化后脚本直接崩溃，`TimeoutError: timed out`。

**根因**：8 个线程同时轰击 Tranzor 内网服务，服务端来不及响应。

**修复三板斧**：

```python
MAX_WORKERS = 4      # ① 降低并发数
MAX_RETRIES = 3      # ② 引入重试

def _api_get(url, **kwargs):
    """③ 统一的带重试 GET"""
    for attempt in range(MAX_RETRIES):
        try:
            return _session.get(url, **kwargs)
        except (Timeout, ConnectionError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
            else:
                raise
```

同时在 [_process_single_task](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#141-193) 和 [_fetch_one_log](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#161-186) 中增加 try/except，**单个 Task 失败不影响整体**。

**教训**：并发优化必须考虑目标服务的承载能力。激进的并发≠更快，可能适得其反。

### 3.7 高级筛选模块

**需求**：在 HTML 报告中添加交互式筛选器，支持从大量记录中精准定位。

**实现的筛选器**：

| 筛选器 | 类型 | 特殊能力 |
|--------|------|----------|
| Time | 文本输入 | 模糊匹配时间戳片段 |
| Task ID | 文本输入 | 后追加，模糊匹配 |
| Task | 文本输入 | 关键字搜索（不区分大小写） |
| Lang | 下拉选择 | 精确匹配 |
| String Key / Source / Before / After | 复合筛选器 ×4 | AND/OR 逻辑、正向/反向匹配、正则、大小写、全字匹配 |

**Task ID 后追加插曲**：Wiki 文档中提到 `--task <id>` 参数，用户才意识到报告中缺少 Task ID 字段。这触发了一次"从数据采集到筛选逻辑"的全链路补充——共 7 个修改点。

**教训**：写文档不是项目的"收尾工作"，而是**功能审计的利器**。文档化过程会暴露遗漏。

### 3.8 产品化：从命令行到 GUI

**痛点发现**：工具的核心用户是语言工作者（非技术背景），命令行是不可逾越的门槛。

**方案评估**：

| 方案 | 成本 | 痛点解决度 |
|------|------|-----------|
| .bat 启动器 | 🟢 低 | 70% |
| tkinter GUI | 🟡 中 | 95% |
| Slack Bot | 🟡 中 | 80% |
| Web 门户 | 🔴 高 | 100% |

**最终选择**：tkinter（Python 自带，零额外依赖）。

最初考虑 Gradio，但发现用户环境未安装，且打包体积达 120MB+。tkinter 打包后仅 25MB。

```python
class ExportApp:
    """深色主题原生桌面应用"""
    # 子线程运行导出 → stdout 重定向到 UI → 进度条动画
    # 零额外 pip install
```

PyInstaller 打包为 `Tranzor导出器.exe`，self-contained，接收者无需安装 Python。

**跨平台问题**：Windows `.exe` 不能在 macOS 运行。解决方案是在 Mac 上重新执行 PyInstaller，或提供 `.command` 脚本 + Python 安装指南。

---

## 4. 深度反思与总结 (Reflection & Summary)

### 认知盲区

| 盲区 | 发现时机 | 影响 |
|------|----------|------|
| JSON 与 HTML 渲染顺序不一致 | TMX 导出内容错乱 | 数据错误（严重） |
| CSS 隐藏行仍可被 JS 选中 | 筛选后导出全部 | 功能失效（中等） |
| 并发数过高压垮服务端 | 生产环境 Timeout | 脚本不可用（严重） |
| 报告缺 Task ID 字段 | 写 Wiki 时才发现 | 功能不完整（轻微） |
| 用户不会用命令行 | 产品化讨论时 | 工具无法推广（严重） |

### 设计缺陷复盘

1. **单体 HTML 的复杂度失控**：[write_html()](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#278-962) 从 ~100 行膨胀到 ~700 行，混合了 Python 模板、CSS、JavaScript 三种语言。维护成本高，但考虑到"单文件分发"的核心需求，这是合理的 tradeoff。

2. **缺乏自动化测试**：所有验证都是手动执行。理想情况下应有 mock API 的单元测试，但对于内部工具脚本，投入产出比不高。

3. **API 健壮性假设过于乐观**：初版代码假设所有 API 调用都会成功，没有 retry 和 fallback。内网服务并不意味着永远稳定。

---

## 5. 再实施与破局 (Re-implementation & Resolution)

### 迭代时间线

```
v0.1  基础 HTML 报告 + Diff 高亮
  │
v0.2  + 复选框 + 工具栏 + JS TMX 生成/下载
  │
v0.3  修复 JSON 索引错位 bug
  │
v0.4  修复 TMX 目标语言选择 bug
  │
v0.5  并发优化（ThreadPoolExecutor + Session 连接池）
  │
v0.6  + 高级筛选模块（6 种筛选器 + 复合文本筛选）
  │
v0.7  修复 Export TMX 忽略筛选 bug
  │
v0.8  + Task ID 字段（数据采集 → JSON → HTML → 筛选 全链路）
  │
v0.9  修复 TimeoutError（降并发 + 重试 + 容错）
  │
v1.0  tkinter GUI + PyInstaller .exe 打包
```

### 最终架构

```
export_changes.py          export_gui.py
┌──────────────────┐       ┌──────────────────┐
│  fetch_tasks()   │       │  tkinter 窗口     │
│  fetch_manuals() │  ←──  │  Task ID 输入     │
│  fetch_edit_logs │       │  格式选择         │
│  collect_changes │       │  实时日志         │
│  write_html()    │       │  进度条           │
│  write_excel()   │       │  一键打开报告      │
│  save_file()     │       └──────────────────┘
│  _api_get()      │              ↓
│  (retry+backoff) │       PyInstaller → .exe
└──────────────────┘       (self-contained)
```

---

## 6. 核心收获与未来备忘录 (Key Takeaways & Notes for the Future)

### 经验提炼

1. **"两种视图，一个索引"是万恶之源**。凡是有分组、排序、筛选的场景，JSON 数据的顺序必须与 DOM 渲染一一匹配。

2. **并发优化的第一原则：了解你的对手**。不是线程越多越快，内网服务的吞吐瓶颈决定了并发的上限。`MAX_WORKERS=4` + retry 是更稳健的策略。

3. **CSS `display:none` 不是"不存在"**。DOM 查询不关心元素是否可见。所有"选中""全选""导出"逻辑必须显式加 `:not(.hidden)` 过滤。

4. **文档化是最好的功能审计**。写 Wiki 时发现缺失 Task ID，这种收益无法被单元测试替代。

5. **面向用户设计，而非面向开发者**。如果工具的核心用户是非技术人员，命令行就=不可用。产品化思维应在设计阶段介入，而不是开发完成后。

### 维护建议

| 场景 | 建议 |
|------|------|
| Tranzor API 变更 | 检查 `fetch_*` 函数的 endpoint 和字段映射 |
| 新增筛选字段 | 在 `TF_FIELDS` 数组追加定义，JS 会自动渲染 |
| 新增 Task 属性 | 三步：[_process_single_task](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#141-193) 取值 → `js_rows` 序列化 → HTML 加列 |
| 更新 .exe | 在同一台机器重新执行 PyInstaller 命令 |
| macOS 打包 | 必须在 Mac 上执行 PyInstaller，不能交叉编译 |
| 性能再次恶化 | 先检查 Task 总数增长，再考虑调 `MAX_WORKERS` 或加分页缓存 |

### 潜在优化方向

- **增量导出**：按时间范围筛选，避免每次全量拉取
- **报告缓存**：避免重复生成相同内容
- **Web 门户**：如团队规模扩大，考虑从单体 HTML 迁移到 SPA + API 架构
- **CI/CD 集成**：定时生成报告推送到 Slack/飞书频道

---

> 写于 2026-03-19 凌晨。从第一行代码到 `.exe` 交付，前后不超过 12 小时，中间经历了至少 4 个"以为完成了但又发现 Bug"的循环。这大概就是软件工程的常态：**你以为是直线，其实是螺旋。**
