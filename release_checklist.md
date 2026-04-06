# TranzorExporter Release Checklist

这是一份面向发布负责人的统一检查清单。

适用范围：

- Windows EXE 发布
- Mac App 发布
- GitHub 代码与构建产物核对

建议用法：

- 每次准备发布时，从上到下完整勾选一次
- 不要跳过“真实打开验证”
- 不要只以“构建成功”作为发布标准

## 1. 发布准备

- [ ] 本次发布目标已明确。
- [ ] 已确认本次需要交付的平台。
  常见情况是同时交付 Windows EXE 和 Mac App。
- [ ] 代码已经过必要 review。
- [ ] 关键功能改动已经做过基本回归。
- [ ] 仓库中的发布相关文档没有明显过期。

## 2. 代码状态检查

- [ ] 需要发布的代码已经提交到 `my-tools` 仓库。
- [ ] 已推送到 GitHub 目标分支。
- [ ] 已确认 GitHub 上的代码就是本次准备发布的版本。
- [ ] 如本次改动涉及打包链路，已同步更新对应文档。

## 3. Windows EXE 检查

参考文档：

- [windows_build_guide.md](/D:/Downloads_D/Tranzor_Platform/my-tools/windows_build_guide.md)
- [build_windows.ps1](/D:/Downloads_D/Tranzor_Platform/my-tools/build_windows.ps1)
- [TranzorExporter.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter.spec)

### 构建前

- [ ] 确认在 Windows 环境中执行构建。
- [ ] 确认正式构建走的是 `build_windows.ps1` 或 `TranzorExporter.spec`。
- [ ] 确认没有临时手工拼 PyInstaller 命令替代正式构建入口。

### 构建

- [ ] Windows EXE 构建成功。
- [ ] 已生成 [TranzorExporter.exe](/D:/Downloads_D/Tranzor_Platform/my-tools/dist/TranzorExporter.exe)。
- [ ] 已记录最新 EXE 的生成时间和大小。

### 验证

- [ ] 已真实启动 EXE。
- [ ] 已确认主窗口标题正常显示。
- [ ] 已确认不是只看到 onefile 的父进程。
- [ ] 已至少点开一次主界面核心标签页。
- [ ] 已确认没有启动即闪退、报错框或空白窗口。

## 4. Mac App 检查

参考文档：

- [mac_build_guide.md](/D:/Downloads_D/Tranzor_Platform/my-tools/mac_build_guide.md)
- [build-mac.yml](/D:/Downloads_D/Tranzor_Platform/my-tools/.github/workflows/build-mac.yml)
- [TranzorExporter_mac.spec](/D:/Downloads_D/Tranzor_Platform/my-tools/TranzorExporter_mac.spec)

### 构建前

- [ ] 已确认 GitHub 上的代码是本次准备发布的版本。
- [ ] 已确认 Mac App 通过 GitHub Actions 构建，而不是依赖某台本地机器的临时手工打包。
- [ ] 已确认 workflow 没有被意外改动。

### GitHub Actions

- [ ] 已手动触发 `Build Mac App` workflow。
- [ ] workflow 运行成功。
- [ ] 没有依赖安装失败或 PyInstaller 构建失败。
- [ ] 已从 Artifacts 下载 `TranzorExporter-Mac`。

### 验证

- [ ] 已确认下载产物中包含 `TranzorExporter.app`。
- [ ] 已在真实 Mac 上尝试打开 `.app`。
- [ ] 已确认主窗口可以正常显示。
- [ ] 已确认关键标签页至少可基本切换。

## 5. 跨平台一致性检查

- [ ] Windows 与 Mac 交付的是同一代码版本。
- [ ] 两个平台的核心功能入口一致。
- [ ] 本次新增功能没有只在一个平台验证。
- [ ] 如本次只发布单平台，已在发布说明中明确标注。

## 6. 质量概览模块专项检查

如果本次发布涉及 `Quality Overview`，额外确认以下事项：

- [ ] KPI 卡片显示的是实际数据而不是占位。
- [ ] 图表标题和图表语义一致。
- [ ] 时间趋势图确实基于时间维度，而不是其它维度伪装。
- [ ] 语言表和低分明细表能正常加载。
- [ ] 低分详情下钻可以打开。

## 7. 发布交付物检查

- [ ] Windows EXE 已放在约定位置。
- [ ] Mac App artifact 已可下载。
- [ ] 如有版本说明，已同步更新。
- [ ] 如需通知团队，已准备好交付链接或下载说明。

## 8. 发布后记录

- [ ] 已记录本次发布对应的 Git commit。
- [ ] 已记录 Windows EXE 构建时间。
- [ ] 已记录 Mac workflow run。
- [ ] 已记录本次发布是否包含打包链路修改。
- [ ] 如本次踩到新问题，已补充到对应构建文档。

## 9. Go / No-Go 标准

满足以下条件才建议发布：

- [ ] Windows EXE 已真实打开验证通过。
- [ ] Mac App 已真实打开验证通过。
- [ ] GitHub 代码版本与交付产物一致。
- [ ] 没有未确认的高优先级问题。

如果下面任一项不满足，建议暂停发布：

- [ ] EXE 或 `.app` 只是“构建成功”，但没有真实打开验证。
- [ ] 交付产物与 GitHub 代码版本无法对应。
- [ ] 关键功能有明显回归但尚未确认影响范围。

## 10. 一句话原则

发布不是“代码合并完成”，而是：

- 代码正确
- 构建正确
- 产物正确
- 真实打开验证通过

四项同时满足，才算真正可以交付。
