# [Tranzor] Temporary Solution for Importing Human Translation Changes into XTM TM

## 1. 文档概述 (Overview)

[export_changes.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py) 是一个本地化工具脚本，用于从 Tranzor 平台**批量导出人工翻译变更记录**，生成可视化 HTML 报告，并支持通过**高级筛选**将选定条目导出为 **TMX 1.4 文件**，直接导入 XTM Cloud 的 Translation Memory 库，作为日后翻译的 leverage 参考。

> **业务价值**：弥补 Tranzor → XTM TM 之间缺少自动同步通道的问题，确保人工审校的高质量翻译能被后续翻译任务复用。

---

## 2. 前提条件 (Prerequisites)

| 项目 | 要求 |
|------|------|
| **Python** | 3.8+ |
| **依赖包** | `requests`（必需）、`openpyxl`（仅 XLSX 模式需要） |
| **网络** | 可访问 Tranzor 平台内网地址 |
| **浏览器** | 现代浏览器（Chrome/Edge/Firefox），用于查看报告和导出 TMX |
| **XTM 权限** | 具有 TM 库导入权限的 XTM 账户 |

**安装依赖**：

```bash
pip install requests openpyxl
```

---

## 3. 核心功能与工作流 (Core Workflows)

### 3.1 导出人工翻译变更

脚本通过 Tranzor HTTP API 自动遍历所有已完成的 Task，提取 **Manual Edit** 和 **LLM Retranslate** 类型的翻译记录及其编辑历史，生成结构化报告。

**数据流**：

```
Tranzor API
  │
  ├─ 1. GET /tasks （获取所有已完成 Task）
  │
  ├─ 2. GET /tasks/{id}/translations （筛选 Manual Edit 条目）
  │
  └─ 3. GET /tasks/{id}/translations/{id}/edit-logs （获取编辑前后文本）
        │
        ▼
  HTML 报告（按 Editor 分组，含 Diff 可视化）
```

`[此处插入图片：数据拉取流程图，展示 API 调用链路]`

**报告内容**：

- 按 **Editor（编辑者）** 分组展示，每组有独立配色
- 每行包含：Time / Task / Lang / String Key / Source (en-US) / Before / After / Diff / Notes
- Diff 列以红绿高亮显示修改前后的差异

`[此处插入图片：HTML 报告整体界面截图，标注各区域]`

### 3.2 高级筛选与 TMX 导出

报告内置了浏览器端的交互功能，支持从大量变更中精准筛选并导出 TMX。

**筛选器类型**：

| 筛选字段 | 类型 | 说明 |
|---------|------|------|
| **Time** | 文本输入 | 模糊匹配时间戳（如 `2026-03` 或 `07:37`） |
| **Task** | 文本输入 | 关键字搜索任务名称 |
| **Lang** | 下拉选择 | 精确选择目标语言 |
| **String Key / Source / Before / After** | 复合筛选器 | 支持 AND/OR 逻辑、正向/反向匹配、正则、大小写、全字匹配 |

`[此处插入图片：高级筛选面板展开后的界面截图，标注各筛选器]`

**TMX 导出规则**：

- **单语言**：直接下载 [.tmx](file:///d:/Downloads_D/Tranzor_Platform/tmx_ref_temp/en_US-ja_JP.tmx) 文件（如 `tranzor_tm_en_US-fr_FR.tmx`）
- **多语言**：下载 [.zip](file:///d:/Downloads_D/Tranzor_Platform/Ringcentral_Core-All-All-2026-03-18.zip) 包，内含每种语言一个 [.tmx](file:///d:/Downloads_D/Tranzor_Platform/tmx_ref_temp/en_US-ja_JP.tmx) 文件
- **导出范围**：仅导出 **筛选可见 + 已勾选** 的条目
- **格式**：TMX 1.4，与 XTM Cloud 完全兼容

---

## 4. 使用指南 (Usage Guide)

### 4.1 命令行参数

```bash
# 导出所有已完成 Task 的变更（HTML 格式）
python export_changes.py

# 导出所有变更（Excel 格式）
python export_changes.py --xlsx

# 只导出指定 Task 的变更
python export_changes.py --task 12345
```

运行后，报告会自动在默认浏览器中打开。

### 4.2 场景一：导出人工翻译变更

**适用场景**：需要审查近期人工翻译的修改情况、进行质量检查或留档。

1. 在 `mytools/` 目录下运行脚本：
   ```bash
   python export_changes.py
   ```
2. 脚本自动拉取数据并生成 HTML 报告（并发拉取，通常 10-30 秒）
3. 报告在浏览器中打开，按编辑者分组展示所有变更
4. 如需 Excel 版本，使用 `--xlsx` 参数

`[此处插入图片：命令行运行脚本的终端输出截图]`

### 4.3 场景二：筛选 → 导出 TMX → 导入 XTM

**适用场景**：将特定语言或特定任务的人工翻译成果导入 XTM TM 库。

#### 步骤 1：打开筛选面板

点击工具栏中的 **🔍 Filters** 按钮，展开高级筛选面板。

#### 步骤 2：设置筛选条件

根据需求组合使用筛选器，例如：

- 筛选 `fr-CA` 语言 → 在 **Lang** 下拉中选择 `fr-CA`
- 筛选特定 Task → 在 **Task** 输入关键字（如 `LOC-24096`）
- 排除 voicemail 相关 → 在 **Source** 的 **Neg** 输入 `voicemail`

#### 步骤 3：应用筛选

点击 **▶ Apply** 按钮，表格仅显示匹配的行。工具栏显示 `Showing X / Y` 提示。

`[此处插入图片：应用筛选后的报告截图，显示筛选计数]`

#### 步骤 4：选择并导出

1. 点击 **☑ Select All** 勾选所有筛选结果（或手动勾选特定行）
2. 点击 **📦 Export TMX** 下载 TMX 文件

`[此处插入图片：导出成功后的工具栏状态截图]`

#### 步骤 5：导入 XTM TM 库

1. 登录 XTM Cloud → 进入 **Translation Memories** 页面
2. 选择目标 TM 库 → 点击 **Import**
3. 上传下载的 [.tmx](file:///d:/Downloads_D/Tranzor_Platform/tmx_ref_temp/en_US-ja_JP.tmx) 文件（或解压 [.zip](file:///d:/Downloads_D/Tranzor_Platform/Ringcentral_Core-All-All-2026-03-18.zip) 后逐个上传）
4. 确认导入设置，完成导入

`[此处插入图片：XTM Cloud TM 导入界面截图，标注上传入口]`

---

## 5. 常见问题 (FAQ)

### Q1：脚本运行缓慢，卡在数据拉取阶段

**原因**：数据量较大时（50+ Task），需要并发请求上百次 API。

**排查建议**：
- 确认网络连接正常，可访问 Tranzor 平台
- 使用 `--task <id>` 缩小范围，只导出特定 Task
- 当前已采用 8 线程并发优化，正常应在 30 秒内完成

### Q2：Export TMX 导出了错误的语言或条目

**排查建议**：
- 确认已点击 **▶ Apply** 应用筛选（仅设置条件不自动生效）
- 检查工具栏是否显示 `Showing X / Y`，确认筛选已激活
- 导出范围 = **可见行 ∩ 已勾选行**，请确认筛选后再勾选

### Q3：多语言导出时提示 "JSZip not loaded"

**原因**：多语言导出需要从 CDN 加载 JSZip 库来创建 ZIP 包。

**排查建议**：
- 确认浏览器可访问外网（`cdn.jsdelivr.net`）
- 如在离线环境使用，可按单一语言分别筛选并逐个导出 [.tmx](file:///d:/Downloads_D/Tranzor_Platform/tmx_ref_temp/en_US-ja_JP.tmx)

---

> **文档维护者**：Anna Su  
> **最后更新**：2026-03-18
