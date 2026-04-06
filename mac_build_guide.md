# TranzorExporter Mac App 构建指南

本文档以当前仓库的真实交付方式为准。

## 当前正式构建方式

TranzorExporter 的 Mac app 不是在本地手工打包为主，而是统一通过 GitHub Actions 构建。

对应文件：

- Workflow: [build-mac.yml](/D:/Downloads_D/Tranzor_Platform/my-tools/.github/workflows/build-mac.yml)
- PyInstaller 配置: [TranzorExporter_mac.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter_mac.spec)

当前 workflow 的关键约束如下：

- 运行环境：`macos-latest`
- Python 版本：`3.12`
- 构建命令：`pyinstaller TranzorExporter_mac.spec --clean`
- 产物上传名：`TranzorExporter-Mac`

## 项目经理视角的标准操作

### 1. 确认代码已经推送到 GitHub

Mac app 的正式构建依赖 GitHub 仓库最新代码，因此先确认需要发布的代码已经推送到目标分支。

### 2. 在 GitHub Actions 手动触发构建

进入仓库的 Actions 页面，选择 `Build Mac App` workflow，然后点击 `Run workflow`。

### 3. 等待 workflow 完成

成功标准：

- workflow 状态为绿色
- 没有安装依赖或 PyInstaller 失败的报错

### 4. 下载产物

构建完成后，从该次 workflow 的 Artifacts 中下载：

- `TranzorExporter-Mac`

下载后应能拿到：

- `TranzorExporter.app`

### 5. 做一次真实打开验证

不要只看 workflow 成功。

真正的验收标准是：

- `.app` 可以在 macOS 上打开
- 主窗口能正常显示
- 基础标签页能切换

## 为什么这条链路比 Windows 更稳定

Mac 构建目前由 CI 固化，稳定性主要来自下面三点：

- 构建环境固定在 GitHub 的 macOS runner，而不是依赖某台本地机器
- Python 版本固定为 3.12，不会随着本地环境漂移
- 始终通过 [TranzorExporter_mac.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter_mac.spec) 构建，而不是临时拼命令

这意味着：

- 更换 AI 工具本身不会天然破坏 Mac 构建
- 真正容易破坏构建的，是对 workflow 或 spec 的随意修改

## 本次之后需要特别保持的约束

这次 Windows EXE 的问题没有直接发生在 Mac 上，但我们已经知道哪些约束必须明确写下来，防止后续误改。

### 1. 不要随意修改 Python 版本

当前 workflow 使用 Python 3.12：

```yaml
python-version: '3.12'
```

如果未来要升级 Python 版本，必须同时验证：

- PyInstaller 是否仍能正常构建 `.app`
- `tkinter` GUI 是否正常启动
- 共享模块是否仍兼容

### 2. 不要绕过 `TranzorExporter_mac.spec`

正式构建应始终走：

```bash
pyinstaller TranzorExporter_mac.spec --clean
```

不要把正式发布改成临时手工拼接的 PyInstaller 命令。

### 3. 不要丢掉下面两个关键配置

在 [TranzorExporter_mac.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter_mac.spec) 中，以下两项目前是有明确意义的：

- `argv_emulation=True`
- `target_arch='universal2'`

它们分别关系到：

- 双击启动体验
- Intel Mac 与 Apple Silicon Mac 的兼容性

### 4. 每次共享 GUI 改动后，都要重新跑一次 Mac workflow

虽然 Mac 构建链路稳定，但下面这些文件一旦变动，仍可能影响 `.app` 的运行：

- [export_gui.py](/D:/Downloads_D/Tranzor_Platform/my-tools/export_gui.py)
- [gui_tabs.py](/D:/Downloads_D/Tranzor_Platform/my-tools/gui_tabs.py)
- [quality_overview.py](/D:/Downloads_D/Tranzor_Platform/my-tools/quality_overview.py)
- [export_mr_pipeline.py](/D:/Downloads_D/Tranzor_Platform/my-tools/export_mr_pipeline.py)
- [export_changes.py](/D:/Downloads_D/Tranzor_Platform/my-tools/export_changes.py)
- [export_translations.py](/D:/Downloads_D/Tranzor_Platform/my-tools/export_translations.py)

## 常见误区

### 误区 1：Workflow 绿了，就等于可交付

不等于。

Workflow 成功只说明 CI 构建链跑通，不代表 `.app` 在真实 Mac 上已经验证过打开体验。

### 误区 2：Mac 一直没出过问题，所以以后也不会出问题

不建议这样假设。

Mac 现在稳定，是因为 workflow 和 spec 已经被固定住了。如果后续改了：

- Python 版本
- PyInstaller 参数
- `TranzorExporter_mac.spec`
- 共享 GUI 入口

仍然可能引入回归。

### 误区 3：换一个 AI 工具就会改变构建结果

通常不会。

只要新工具遵守同一套仓库规则：

- 推送代码到 GitHub
- 触发 [build-mac.yml](/D:/Downloads_D/Tranzor_Platform/my-tools/.github/workflows/build-mac.yml)
- 不绕过 [TranzorExporter_mac.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter_mac.spec)

那么它构建出来的 Mac app 原理上应与现在一致。

## 本地构建的定位

仓库中仍保留本地脚本：

- [build_mac.command](/D:/Downloads_D/Tranzor_Platform/my-tools/build_mac.command)

它的用途更适合：

- 在真实 Mac 上做本地调试
- CI 之外的补充验证

但正式发布路径，仍建议以 GitHub Actions 为准。

## 推荐的发布前检查清单

每次准备发新版本 Mac app 时，按下面顺序检查：

1. 代码是否已经推送到正确分支。
2. GitHub Actions 的 `Build Mac App` 是否成功。
3. 下载到的 artifact 是否包含 `TranzorExporter.app`。
4. 是否在真实 Mac 上打开过一次。
5. 主窗口是否正常显示。
6. 关键标签页是否至少点过一轮。

## 一句话原则

Mac app 的稳定，依赖的是“固定 workflow + 固定 spec + 真实启动验证”。

以后无论是谁构建，哪怕换成别的编程工具，只要不绕开这三件事，结果就应该稳定可控。
