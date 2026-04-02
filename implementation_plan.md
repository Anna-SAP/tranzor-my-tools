# 轻量级 GUI 实现计划

使用 Python 内置 tkinter 构建原生桌面应用，零额外依赖，直接包裹 [export_changes.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py) 的核心函数。

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **UI 框架** | tkinter | Python 自带，无需 pip install，打包体积小 |
| **架构** | 单文件 `export_gui.py` | import `export_changes` 的函数，不改动原脚本 |
| **线程** | `threading.Thread` | 长时间 API 调用放子线程，UI 不卡顿 |
| **进度反馈** | 重定向 stdout + 实时更新 Text 组件 | 复用原有 print 输出 |

## UI 布局

```
┌─────────────────────────────────────────┐
│  🌐 Tranzor 翻译变更导出器              │
├─────────────────────────────────────────┤
│  Task ID    [_______________] (留空=全部)│
│  输出格式    ◉ HTML    ○ Excel          │
├─────────────────────────────────────────┤
│  [▶ 开始导出]    [📂 打开报告]          │
├─────────────────────────────────────────┤
│  ┌─ 运行日志 ────────────────────────┐  │
│  │ 正在获取 task 列表...             │  │
│  │ 找到 51 个已完成的 task           │  │
│  │ [2/51] Task 'Anna LOC-24096'...  │  │
│  │ ✓ 报告已生成: xxx.html            │  │
│  └───────────────────────────────────┘  │
│  ████████████████████░░░░  75%          │
└─────────────────────────────────────────┘
```

## 修改内容

### [NEW] [export_gui.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_gui.py)

- tkinter 界面：Task ID 输入框、格式单选按钮、开始/打开按钮
- 子线程运行导出逻辑，stdout 重定向到 UI 日志区
- 进度条（基于 task 计数估算）
- 完成后自动启用"打开报告"按钮
- 错误弹窗提示

### export_changes.py 不做任何修改

GUI 通过 `from export_changes import collect_changes, write_html, write_xlsx` 直接调用。

## 验证计划

1. 运行 `python export_gui.py`，验证窗口正常显示
2. 留空 Task ID + HTML 格式 → 点击开始 → 验证报告生成
3. 输入 Task ID → 验证只导出单个 task
4. 选择 Excel → 验证 xlsx 生成
5. 验证进度日志实时滚动、按钮状态切换
