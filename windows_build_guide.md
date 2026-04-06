# TranzorExporter Windows 构建与排障指南

本文档专门记录 Windows EXE 的正确构建方式，以及 2026-04-06 这次打包事故沉淀下来的经验。

## 结论先说

- Windows EXE 必须通过 [TranzorExporter.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter.spec) 构建。
- 不要再临时手拼 `pyinstaller --onefile ...` 参数去打正式包。
- 推荐构建入口是 [build_windows.ps1](/D:/Downloads_D/Tranzor_Platform/my-tools/build_windows.ps1)。

## 正确构建方式

在 `my-tools` 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
```

等价命令：

```powershell
python -m PyInstaller .\TranzorExporter.spec --clean
```

产物位置：

```text
dist\TranzorExporter.exe
```

## 这次事故的真实根因

### 1. `tkinter` 在分析阶段被 PyInstaller 错误排除

PyInstaller 6.19 在构建时会先探测构建解释器里的 Tcl/Tk 状态。如果探测失败，它会直接把 `tkinter` 从依赖图里排除。

这次事故里，构建环境对 Tcl/Tk 的探测不稳定，结果是：

- 打包日志里会出现类似 `tkinter installation is broken`
- EXE 虽然能产出，但运行时缺少 `tkinter`
- 最终表现为 GUI 起不来，或者弹错误框

### 2. onefile 的“父进程/子进程”模型容易误判

`TranzorExporter.exe` 是 onefile 包。启动时流程不是“一个进程直接起 UI”，而是：

1. 父进程先解压运行时文件
2. 父进程再拉起真正的 GUI 子进程
3. 父进程等待子进程

如果只盯第一次 `Start-Process` 返回的 PID，很容易误以为 EXE 没起来，实际上真正的 GUI 可能已经由第二个同名进程拉起。

## 当前仓库里已经做的保护

### 1. 自定义 pre-find hook，禁止 PyInstaller 擅自踢掉 `tkinter`

文件：

- [hook-tkinter.py](/D:/Downloads_D/Tranzor_Platform/my-tools/pyinstaller_hooks/pre_find_module_path/hook-tkinter.py)

作用：

- 覆盖 PyInstaller 默认的 `hook-tkinter`
- 即使构建机上的 Tcl/Tk 探测异常，也保留标准库 `tkinter` 的分析路径

### 2. 运行时显式设置 Tcl/Tk 目录

文件：

- [pyi_rth_tkinter_fix.py](/D:/Downloads_D/Tranzor_Platform/my-tools/pyi_rth_tkinter_fix.py)

作用：

- 在 EXE 运行时从 `sys._MEIPASS` 中定位 `_tcl_data`、`_tk_data`
- 显式设置 `TCL_LIBRARY`、`TK_LIBRARY`、`TCLLIBPATH`
- 避免 GUI 启动时再去依赖宿主 Python 的 Tcl/Tk 安装状态

### 3. `spec` 文件显式收集 Tcl/Tk 资源

文件：

- [TranzorExporter.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter.spec)

当前 `spec` 会显式打包：

- `tcl86t.dll`
- `tk86t.dll`
- `tcl8.6`
- `tk8.6`
- `tcl8`

### 4. 排除会干扰 onefile 启动的非必要二进制模块

当前 `spec` 已排除：

- `81d243bd2c585b0f4821__mypyc`
- `charset_normalizer.md`
- `charset_normalizer.cd`

这样做的目的是减少 onefile 包在解压和启动阶段的不确定性。

## 标准验证步骤

每次重要改动后，Windows EXE 至少做下面 3 步验证：

### 1. 确认 EXE 真实生成

```powershell
Get-Item .\dist\TranzorExporter.exe
```

### 2. 双击或命令行启动 EXE

```powershell
Start-Process .\dist\TranzorExporter.exe
```

### 3. 不要只看第一个 PID，要看最终窗口

推荐验证命令：

```powershell
Start-Process .\dist\TranzorExporter.exe | Out-Null
Start-Sleep -Seconds 6
Get-Process TranzorExporter -ErrorAction SilentlyContinue |
  Select-Object Id, ProcessName, MainWindowTitle, MainWindowHandle, StartTime
```

通过标准：

- 进程列表里可能会出现两个同名进程
- 真正成功的 GUI 进程应带有窗口标题 `Tranzor Translation Exporter`
- `MainWindowHandle` 应非 0

## 常见错误与对应处理

### 构建成功但 EXE 无法打开

先确认是否真的用的是 `TranzorExporter.spec`，而不是手工拼的 PyInstaller 命令。

### 运行日志里出现 `ModuleNotFoundError: No module named 'tkinter'`

说明构建时没有正确使用仓库内的自定义 hook 或 spec。

优先检查：

- [TranzorExporter.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter.spec) 是否参与构建
- [hook-tkinter.py](/D:/Downloads_D/Tranzor_Platform/my-tools/pyinstaller_hooks/pre_find_module_path/hook-tkinter.py) 是否在仓库中

### EXE 启动后只看到短暂后台进程

不要立刻判定失败。先按上面的进程检查命令确认是否已经拉起 GUI 子进程。

## 团队约定

- Windows 正式包统一从 [build_windows.ps1](/D:/Downloads_D/Tranzor_Platform/my-tools/build_windows.ps1) 或 [TranzorExporter.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter.spec) 构建。
- 修改打包链路时，必须同时更新这份文档。
- 重要功能变更后，除了代码 review，还必须做一次真实 EXE 启动验证，验证目标是“看到主窗口”，不是“命令执行成功”。
