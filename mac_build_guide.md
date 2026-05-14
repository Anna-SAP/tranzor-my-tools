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
- 触发方式：`workflow_dispatch`（手动）、`push` 到 `master`、`pull_request` 进 `master`；都带 path 过滤，仅 Mac 相关源文件/构建脚本变动时才触发
- 打包方式：把 `staging/` 目录（`TranzorExporter.app` + `首次打开必读.txt`）直接交给 `actions/upload-artifact@v4`；GitHub 把目录内容打成一层 zip 作为 artifact
- 产物上传名：`TranzorExporter-Mac`（artifact 下载到本地是 `TranzorExporter-Mac.zip`，解压一次即得 `TranzorExporter-Mac/` 文件夹，内含 `.app` 和 README）

> 为什么不用 DMG：早期版本尝试过用 `create-dmg` 生成 DMG，理由是视觉布局更专业。实测后回退 — 因为 macOS 15 Sequoia 起，未公证 (un-notarized) 的 DMG 在 **挂载阶段** 就会被 Gatekeeper 拦截，比 zip 多出一道关，用户连 DMG 窗口都看不到，反而比纯 zip 体验更差。
>
> 为什么没有内层 ditto zip：早期还试过 `ditto -c -k` 生成内层 zip，再让 upload-artifact 再套一层。这样用户要解压两次。后来去掉内层 zip，直接让 upload-artifact 把 staging/ 内容作为 artifact zip 的根 —— 用户只解压一次即可拿到所有内容。
>
> 关于"解压后没有文件夹"：macOS Finder 的 Archive Utility 在解压含多个顶层项目的 zip 时一定会创建一个以 zip 名命名的文件夹（Finder 自身行为，zip 内部布局无法绕过）。当前是 `.app` + README 两个顶层项目，所以解压会得到 `TranzorExporter-Mac/` 这层包装文件夹。完全去掉只能扔掉 README、单 ship `.app`；为了让用户能马上读到首次启动说明，做了这个取舍。

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

- `TranzorExporter-Mac`（下载到本地是 `TranzorExporter-Mac.zip`）

解压一次后应能拿到一个 `TranzorExporter-Mac/` 文件夹，内部并列两项：

- `TranzorExporter.app` — 主程序
- `首次打开必读.txt` — 首次启动指南（两种解除拦截方式）

### 5. 做一次真实打开验证

不要只看 workflow 成功。

真正的验收标准是：

- 解压 zip 后，能看到 `.app`、`首次打开必读.txt` 两项
- 把 `.app` 拖到 `/Applications` 后，用任一种方式（终端 `xattr` / 系统设置）解除拦截后，`.app` 可以在 macOS 上打开
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
2. GitHub Actions 的 `Build Mac App` 是否成功（PR/push 触发的运行也行，不必非要手动触发）。
3. 下载到的 artifact 解压一次后，是一个 `TranzorExporter-Mac/` 文件夹（不是裸 `.app`，也不是 DMG）。
4. 文件夹里能看到两项：`TranzorExporter.app` / `首次打开必读.txt`。
5. 是否在真实 Mac 上按 `首次打开必读.txt` 的任一种方式解除拦截后打开过一次。
6. 主窗口是否正常显示。
7. 关键标签页是否至少点过一轮。

## Gatekeeper 拦截：现状与升级路径

### 现状（无 Apple Developer ID）

当前 workflow 走的是 **ad-hoc 签名 + zip 内置 README** 的路线。这条路线已经把无 Apple Developer ID 情况下能优化的体验做到位了：

- 内置"首次打开必读.txt"：纯文本，TextEdit 打开 .txt 不会触发 Gatekeeper，用户可以随时阅读，里面写好两种解除拦截方式（终端 `xattr` / 系统设置）
- `.app` 做了 ad-hoc 签名（带 `--options=runtime`），避免出现"应用已损坏"的二次错误

**为什么不用 DMG**（曾经的实验路径，已回退）：

- macOS 15 Sequoia 起，未公证的 DMG 在 mount 阶段就会被 Gatekeeper 拦截
- 这意味着 DMG 路线对用户来说要"放行 DMG 一次 + 放行 .app 一次"，**比纯 zip 多了一道关**
- zip 解压本身不需要 Gatekeeper 放行，README 是纯文本可以直接读，比 DMG 路线少一次首次拦截
- 这套权衡在 PR #8 的反复迭代里验证过 — 详见相关 commit 历史

**为什么没有"修复-Gatekeeper.command"辅助脚本**（曾经存在，已删除）：

- 该脚本自身从 zip 解出来后也带 quarantine，首次双击仍然会被 Gatekeeper 拦
- 用户需要绕过一次的话，README 里直接一行 `xattr` 命令更快、更透明
- 留着脚本反而误导用户以为有"一键修复"，实际上还是要解除一次拦截 — 索性砍掉

**但请明确一点**：只要没有 Apple 公证，macOS Gatekeeper 在首次启动 `.app` 时一定会拦截一次。
当前方案做的是"**让用户尽量轻松地放行一次**"，而不是"**macOS 默认就信任**"。

### 升级到默认信任（需要 Apple Developer Program $99/年）

如果以后愿意付费走完整路径，需要完成的事情如下：

1. **加入 Apple Developer Program**（个人 $99/年）。
2. 在 Apple Developer 后台生成 **Developer ID Application** 证书，下载并导出为 `.p12` 文件。
3. 创建一个 **App-Specific Password**（或 App Store Connect API Key）用于公证。
4. 在 GitHub 仓库 Settings → Secrets and variables → Actions 添加以下 Secrets：

   | Secret 名称 | 内容 |
   |---|---|
   | `APPLE_CERT_P12_BASE64` | `.p12` 文件的 base64 编码（`base64 -i Certificates.p12`） |
   | `APPLE_CERT_PASSWORD` | 导出 `.p12` 时设置的密码 |
   | `APPLE_ID` | Apple ID 邮箱 |
   | `APPLE_TEAM_ID` | 10 位 Team ID（Developer 后台“Membership”页可见） |
   | `APPLE_APP_PASSWORD` | App-Specific Password |

5. 把 `build-mac.yml` 里的 “Ad-hoc codesign the .app bundle” 步骤替换为三段：

   ```yaml
   - name: Import signing certificate
     env:
       CERT_BASE64: ${{ secrets.APPLE_CERT_P12_BASE64 }}
       CERT_PASSWORD: ${{ secrets.APPLE_CERT_PASSWORD }}
     run: |
       echo "$CERT_BASE64" | base64 --decode > /tmp/cert.p12
       security create-keychain -p actions build.keychain
       security default-keychain -s build.keychain
       security unlock-keychain -p actions build.keychain
       security import /tmp/cert.p12 -k build.keychain \
         -P "$CERT_PASSWORD" -T /usr/bin/codesign
       security set-key-partition-list -S apple-tool:,apple: \
         -s -k actions build.keychain

   - name: Codesign the .app bundle
     run: |
       codesign --force --deep --options=runtime --timestamp \
         --sign "Developer ID Application" \
         dist/TranzorExporter.app

   - name: Notarize the .app and staple
     env:
       APPLE_ID: ${{ secrets.APPLE_ID }}
       APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
       APPLE_APP_PASSWORD: ${{ secrets.APPLE_APP_PASSWORD }}
     run: |
       # 公证需要把 .app 压成 zip 提交
       ditto -c -k --keepParent dist/TranzorExporter.app \
         dist/TranzorExporter.zip
       xcrun notarytool submit dist/TranzorExporter.zip \
         --apple-id "$APPLE_ID" \
         --team-id "$APPLE_TEAM_ID" \
         --password "$APPLE_APP_PASSWORD" \
         --wait
       xcrun stapler staple dist/TranzorExporter.app
   ```

6. 启用公证后，如果想恢复 DMG 视觉布局，可以再回到 `create-dmg`；公证后的 DMG 在 mount 阶段不会被 Gatekeeper 拦，体验恢复成功。
7. 完成公证后即可删除 zip 里的 `首次打开必读.txt` —— 它的存在意义就是为没公证的过渡期服务的。

> 任何对这套链路的改动，建议同时更新本文件与 `TranzorExporter_Mac_UserGuide.md`，
> 让构建侧和用户侧文档始终对齐。

## 一句话原则

Mac app 的稳定，依赖的是“固定 workflow + 固定 spec + 真实启动验证”。

以后无论是谁构建，哪怕换成别的编程工具，只要不绕开这三件事，结果就应该稳定可控。
