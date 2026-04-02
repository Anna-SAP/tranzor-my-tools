# export_changes.py 产品化方案报告

> **目标**：让完全不懂代码的语言工作者能轻松完成翻译变更导出与 TMX 维护工作。

---

## 1. 用户画像与痛点分析

### 用户画像

| 维度 | 描述 |
|------|------|
| **角色** | 翻译审校员、本地化 PM、QA 审核员 |
| **技术水平** | 精通 XTM/MemoQ/Trados 等 CAT 工具，但不具备编程或命令行经验 |
| **日常工具** | 浏览器、Excel、CAT 平台、企业通讯（Slack/飞书）、Confluence Wiki |
| **交互偏好** | **点击 > 选择 > 拖拽** ≫ 输入命令 |
| **工作节奏** | 每天/每周定期检查翻译变更，按需导出 TMX 导入 TM 库 |

### 当前痛点

| # | 痛点 | 严重程度 | 说明 |
|---|------|----------|------|
| 1 | **Python 环境配置** | 🔴 致命 | 安装 Python、pip install 依赖对语言工作者是不可逾越的门槛 |
| 2 | **命令行操作** | 🔴 致命 | 打开终端、cd 到目录、输入命令 — 每一步都可能出错 |
| 3 | **参数记忆负担** | 🟡 中等 | `--task 53 --xlsx -o xxx` 需要查文档才能用 |
| 4 | **无可视化入口** | 🟡 中等 | 没有"打开即用"的界面，用户不知道工具在哪、怎么启动 |
| 5 | **错误排查困难** | 🟠 较高 | 超时、网络错误等只有终端 log，用户无法自助解决 |

---

## 2. 解决方案矩阵

### 方案 A：一键启动器（.bat / .exe）

> **成本**：🟢 低 &nbsp;|&nbsp; **交付周期**：1-2 天

**交互形态**：桌面双击图标 → 自动运行 → 浏览器打开报告

**用户旅程**：
1. 在共享网盘或 Slack 频道获取"Tranzor 报告.bat"
2. 双击运行
3. 看到一个简短的黑窗口显示进度
4. 报告自动在浏览器中打开

**技术实现**：
```bat
@echo off
cd /d "%~dp0"
python export_changes.py
pause
```
- 进阶版：用 PyInstaller 打包为 `.exe` 单文件，**无需安装 Python**
- 命令：`pyinstaller --onefile export_changes.py`

**优缺点**：

| Pros | Cons |
|------|------|
| 零开发成本 | 仍需预装 Python（.bat 版） |
| 双击即用，完全消除命令行 | 无法选择参数（如指定 Task ID） |
| 共享和分发极简 | .exe 打包后体积 ~30MB |
| | 黑窗口对用户不够友好 |

---

### 方案 B：轻量级本地 GUI

> **成本**：🟡 中 &nbsp;|&nbsp; **交付周期**：3-5 天

**交互形态**：桌面应用窗口，含参数选择器 + 进度条 + 一键运行

**用户旅程**：
1. 双击"Tranzor Exporter"图标
2. 在界面中选择：全部 Task / 指定 Task ID / 输出格式（HTML/Excel）
3. 点击"▶ 开始导出"
4. 进度条实时显示（正在拉取 [12/51]…）
5. 完成后点击"📂 打开报告"

**技术实现**（推荐 Gradio，5 分钟出 UI）：

```python
import gradio as gr

def run_export(task_id, fmt):
    # 复用 collect_changes() + write_html() / write_xlsx()
    ...
    return "✓ 报告已生成", filepath

gr.Interface(
    fn=run_export,
    inputs=[
        gr.Textbox(label="Task ID（留空=全部）", placeholder="如 53"),
        gr.Radio(["HTML", "Excel"], value="HTML", label="输出格式"),
    ],
    outputs=[gr.Textbox(label="状态"), gr.File(label="下载报告")],
    title="Tranzor 翻译变更导出器",
).launch()
```

- 用 PyInstaller 打包为 `.exe`，无需 Python 环境
- 或部署为内网 Web 页面（`launch(server_name="0.0.0.0")`）

**优缺点**：

| Pros | Cons |
|------|------|
| 图形界面，零学习成本 | 需要一定开发量（3-5 天） |
| 支持参数选择，覆盖所有命令行场景 | 本地部署仍需分发安装包 |
| Gradio/Streamlit 开发极快 | 打包后体积 ~80-120MB |
| 可进度反馈 + 错误提示 | |

---

### 方案 C：Slack / 飞书 Bot 机器人

> **成本**：🟡 中 &nbsp;|&nbsp; **交付周期**：5-7 天

**交互形态**：在团队沟通频道中 @bot 发指令，bot 返回报告文件

**用户旅程**：
1. 在 Slack/飞书频道输入 `@tranzor-bot export` 或 `@tranzor-bot export --task 53`
2. Bot 回复"⏳ 正在生成报告…"
3. 30 秒后 Bot 发送 HTML/XLSX 文件附件
4. 用户直接在聊天中下载

**技术实现**：
- 后端服务（Flask/FastAPI）封装 [collect_changes()](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#195-228) + [write_html()](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py#278-962)
- 注册 Slack Bot / 飞书 Bot，监听 slash command
- 运行在内网服务器上，用户无需安装任何东西

**优缺点**：

| Pros | Cons |
|------|------|
| **零安装**，开口说话即可 | 需要内网服务器持续运行 |
| 融入日常工作流（沟通工具） | Bot 开发有平台 API 学习成本 |
| 支持团队共享，自动推送 | 交互能力有限（无法实现高级筛选） |
| 可扩展定时任务（每日自动报告） | TMX 导出仍需在浏览器完成 |

---

### 方案 D：内部 Web 门户

> **成本**：🔴 高 &nbsp;|&nbsp; **交付周期**：2-4 周

**交互形态**：内网 Web 页面，一站式完成导出、筛选、TMX 下载

**用户旅程**：
1. 浏览器访问 `http://tranzor-tools.int/export`
2. 在页面上选择 Task 范围、输出格式
3. 点击"生成报告"，等待加载
4. 在同一个页面中直接查看报告、使用高级筛选、勾选导出 TMX
5. 所有操作在浏览器内完成

**技术实现**：
- 前端：将现有 HTML 报告提升为 SPA（React/Vue），嵌入筛选面板
- 后端：FastAPI 服务封装数据拉取逻辑，提供 REST API
- 部署在内网 Docker 容器中

**优缺点**：

| Pros | Cons |
|------|------|
| **终极体验**：浏览器直达、零安装 | 开发成本最高（前后端 + 部署） |
| 筛选 + TMX 导出一体化 | 需要持续运维 |
| 可扩展：用户权限、操作日志、定时任务 | 相比原有方案是一次重构 |
| 团队共享，多人可同时使用 | |

---

## 3. 演进路线图 (Roadmap)

```
Phase 1 (本周)          Phase 2 (1-2 周)         Phase 3 (1-2 月)
━━━━━━━━━━━             ━━━━━━━━━━━               ━━━━━━━━━━━
Quick Win               提升体验                  平台化
```

### Phase 1 — Quick Win（本周交付）

> **目标**：立即消除"打开终端输命令"这个最大障碍

- ✅ 提供 `.bat` 一键启动器（全量导出 + 指定 Task 两个版本）
- ✅ 用 PyInstaller 打包 `.exe` 分发给团队
- ✅ 在 Confluence Wiki 上补全操作指南 + 下载链接

**预期效果**：用户双击桌面图标即可生成报告，完成度 70%

### Phase 2 — 提升体验（2 周内）

> **目标**：增加参数选择能力 + 融入日常通讯工具

- 🔧 用 Gradio 构建简单 GUI，支持 Task ID 输入 + 格式选择 + 进度显示
- 🔧 打包为 `.exe`，替代 Phase 1 的 `.bat`
- 🔧（可选）搭建 Slack Bot，支持 `/export` 命令，自动推送报告到频道

**预期效果**：用户有了"产品化"的工具体验，满足 95% 日常需求

### Phase 3 — 平台化（按需启动）

> **目标**：构建长期可维护的内部工具平台

- 🏗 将导出功能做成内网 Web 服务
- 🏗 集成报告在线预览 + 筛选 + TMX 导出（将当前 HTML 内的 JS 功能搬到 Web 平台）
- 🏗 增加定时任务、邮件推送、操作审计等企业级特性
- 🏗 考虑与 Tranzor 平台本身深度集成

**预期效果**：从"工具"升级为"平台"，成为本地化团队的标准工作台

---

## 决策建议

> [!IMPORTANT]
> **推荐立即执行 Phase 1**（投入 < 1 天），解决 80% 的用户痛点。
> Phase 2 的 Gradio GUI 方案性价比最高，推荐作为下一步重点投入。

| 方案 | 成本 | 痛点解决度 | 推荐优先级 |
|------|------|-----------|-----------|
| A. 一键启动器 | 🟢 低 | 70% | ⭐⭐⭐ **立即执行** |
| B. 轻量级 GUI | 🟡 中 | 95% | ⭐⭐⭐ **短期重点** |
| C. Slack Bot | 🟡 中 | 80% | ⭐⭐ 按需并行 |
| D. Web 门户 | 🔴 高 | 100% | ⭐ 长期规划 |
