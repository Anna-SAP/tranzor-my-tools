# TranzorExporter Mac OS 部署指南

## 快速开始

### 1. 环境要求

- macOS 12 (Monterey) 或更高
- Python 3.10+（推荐通过 Homebrew 安装）
- 网络可访问 `tranzor-platform.int.rclabenv.com`（需 VPN）

### 2. 安装 Python 依赖

```bash
# 安装 Homebrew（如未安装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装 Python（自带 tkinter）
brew install python-tk@3.12

# 安装项目依赖
pip3 install requests openpyxl
```

### 3. 直接运行（开发模式）

```bash
cd my-tools
python3 export_gui.py
```

---

## 打包为 .app

### 1. 安装 PyInstaller

```bash
pip3 install pyinstaller
```

### 2. 构建

```bash
cd my-tools
pyinstaller TranzorExporter_mac.spec
```

生成的 `.app` 位于 `dist/TranzorExporter.app`

### 3. 分发

将 `TranzorExporter.app` 拖入 `/Applications` 或直接双击运行。

> ⚠️ 首次运行可能需要：**系统设置 → 隐私与安全性 → 仍要打开**

---

## 添加应用图标（可选）

1. 准备一个 1024×1024 PNG 图标
2. 转换为 `.icns`：
   ```bash
   mkdir icon.iconset
   sips -z 512 512 icon.png --out icon.iconset/icon_256x256@2x.png
   sips -z 256 256 icon.png --out icon.iconset/icon_256x256.png
   sips -z 128 128 icon.png --out icon.iconset/icon_128x128.png
   iconutil -c icns icon.iconset -o TranzorExporter.icns
   ```
3. 取消 `TranzorExporter_mac.spec` 中 `icon=` 行的注释

---

## 常见问题

| 问题 | 解决 |
|------|------|
| `tkinter` 找不到 | `brew install python-tk@3.12` |
| 网络超时 | 确认 VPN 已连接 |
| 字体显示异常 | 应用已内置 Helvetica Neue 适配 |
| `.app` 被阻止运行 | 系统设置 → 隐私与安全性 → 仍要打开 |
| Apple Silicon (M1/M2) | PyInstaller 自动适配 ARM64 架构 |

---

## 技术说明

本项目原生支持跨平台运行：
- **字体**：Mac 使用 `Helvetica Neue` + `Menlo`，Windows 使用 `Segoe UI` + `Consolas`
- **核心逻辑**：100% Python 标准库 + requests/openpyxl，无平台绑定
- **HTML 报告**：CSS 字体栈首选 `-apple-system`，Mac 上自动使用 San Francisco
