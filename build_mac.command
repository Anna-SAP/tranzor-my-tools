#!/bin/bash
# ============================================
# TranzorExporter Mac 一键构建脚本
# 双击此文件即可自动完成打包
# ============================================

set -e
cd "$(dirname "$0")"

echo "🔧 正在安装依赖..."
pip3 install --user requests openpyxl pyinstaller

echo ""
echo "📦 正在打包 TranzorExporter.app..."
python3 -m PyInstaller TranzorExporter_mac.spec --clean

echo ""
echo "✅ 构建完成！"
echo "📁 应用位置: $(pwd)/dist/TranzorExporter.app"
echo ""
echo "请将 dist/TranzorExporter.app 发送给需要的同事即可。"

# 自动打开 dist 文件夹
open dist/

read -p "按回车键关闭此窗口..."
