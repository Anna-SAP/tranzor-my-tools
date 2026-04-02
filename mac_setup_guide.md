# Tranzor 导出器 — macOS 安装指南

> 仅需一次配置，之后每次使用只需双击。

---

## 一次性配置（约 5 分钟）

### 1. 安装 Python

打开 **终端**（Launchpad → 搜索 "Terminal"），粘贴以下命令：

```bash
# 检查是否已安装 Python 3
python3 --version
```

如果显示 `Python 3.x.x`，跳到第 2 步。否则：

```bash
# 安装 Homebrew（Mac 包管理器）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装 Python
brew install python
```

### 2. 安装依赖

```bash
pip3 install requests openpyxl
```

### 3. 获取工具文件

从共享网盘下载以下文件，放到同一个文件夹（如 `~/Desktop/tranzor-tools/`）：

- [export_gui.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_gui.py)
- [export_changes.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_changes.py)
- [export_translations.py](file:///d:/Downloads_D/Tranzor_Platform/mytools/export_translations.py)

### 4. 创建双击启动器

在终端中运行：

```bash
cat > ~/Desktop/Tranzor导出器.command << 'EOF'
#!/bin/bash
cd "$(dirname "$0")/../tranzor-tools" 2>/dev/null || cd ~/Desktop/tranzor-tools
python3 export_gui.py
EOF
chmod +x ~/Desktop/Tranzor导出器.command
```

> 桌面会出现 **Tranzor导出器.command** 文件，双击即可启动。

---

## 日常使用

1. 双击桌面上的 **Tranzor导出器**
2. 在界面中设置 Task ID 和输出格式
3. 点击 **▶ 开始导出**
4. 完成后点击 **📂 打开报告**

操作方式与 Windows 版完全一致。

---

## 可选：打包为 .app（免 Python 环境）

如果想分发给完全不愿配置环境的 Mac 用户，可在任意 Mac 上执行：

```bash
pip3 install pyinstaller
cd ~/Desktop/tranzor-tools
pyinstaller --onefile --windowed --name "Tranzor导出器" \
  --add-data "export_changes.py:." \
  --add-data "export_translations.py:." export_gui.py
```

生成的 `dist/Tranzor导出器` 可直接分发，双击运行，无需安装 Python。

---

## 常见问题

| 问题 | 解决方法 |
|------|----------|
| `python3: command not found` | 先安装 Python（见第 1 步） |
| `No module named 'requests'` | 运行 `pip3 install requests openpyxl` |
| 双击 .command 提示"无法打开" | 右键 → 打开，或在系统偏好设置中允许运行 |
| GUI 窗口显示异常 | macOS 可能需安装 `python-tk`：`brew install python-tk` |
