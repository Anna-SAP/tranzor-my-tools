# Tranzor Bridge — 快速上手

> **5 分钟指南。** 在 TranzorExporter HTML 报告里勾选要修的行 → 直接带着这份清单到 Tranzor Platform 标签页修复，侧栏一条条跟。
> 不用再"复制 key → 切到平台 → 搜索 → 修复 → 再切回来"地循环了。**对上游零侵入。**

---

## 它解决了什么问题

| 改进前 | 改进后 |
|---|---|
| HTML 报告勾出 8 条 → 打开 Tranzor → 复制 `String Key` → 粘到搜索框 → 修 → 再来 7 遍 | 勾完 → 点 `↗ Send to Tranzor` → Tranzor 标签页右侧弹出 8 条侧栏 → `🔍 Find` 自动填搜索框 → 修 → `✓ Fixed` → 进度自动保存 |

---

## 一次性配置（约 2 分钟）

### 第 1 步：装一个 userscript 管理器

| 浏览器 | 推荐 |
|---|---|
| Chrome / Edge / Brave | **Tampermonkey**（Chrome 应用商店） |
| Firefox | **Tampermonkey** 或 **Violentmonkey**（Add-ons） |
| Safari | **Tampermonkey**（App Store） |

### 第 2 步：安装 Tranzor Bridge userscript

1. 打开仓库文件：<https://github.com/Anna-SAP/tranzor-my-tools/blob/master/userscript/tranzor_bridge.user.js>
2. 点右上角 **Raw**
3. Tampermonkey 会自动识别 userscript 头并弹安装对话框 —— 点确认
4. 确认权限里包含：
   - `@match http://tranzor-platform.int.rclabenv.com/*`
   - `@connect 127.0.0.1`

### 第 3 步：验证侧栏挂载成功

1. 连上公司网络 / VPN（和平时用 Tranzor 一样）
2. 浏览器打开 <http://tranzor-platform.int.rclabenv.com>
3. 看右上角 —— 应该出现一条绿色折叠条 **📋 Tranzor Bridge**
4. 点开它；TranzorExporter 没运行时会显示 `Waiting for selections from TranzorExporter…` —— 这是正常的待机状态

以上三步**只做一次**。之后只要 TranzorExporter 桌面应用和 Tranzor Platform 标签页同时开着，就会自动配对。

---

## 日常用法（4 步）

```
1. 启动 TranzorExporter  →  2. 导出 HTML 报告  →  3. 筛选 + 勾选要修的行
                                                          ↓
                      5. 在 Tranzor 侧栏逐条修复  ←  4. 点 ↗ Send to Tranzor
```

### 详细步骤

1. **启动桌面应用**（Windows 双击 `TranzorExporter.exe`，macOS 双击 `TranzorExporter.app`）
   - 控制台会出现一行：`[bridge] listening on http://127.0.0.1:48217 instance_id=…` —— 这是本地桥起来了

2. **照常导出翻译报告**：填 Task ID，选 `All Translations`，格式 `HTML`，点 `▶ Start Export`。HTML 报告会自动在浏览器打开。

3. **筛选 + 勾选**：用顶部的 Filter 面板（按 String Key / Source / Translated 等多维筛）缩小到要修的行，然后勾选行首的复选框。工具栏会显示 `Selected: N`。

4. **点 `↗ Send to Tranzor`**（在 `📦 Export TMX` 旁边的新绿色按钮）
   - 按钮下方提示：`✓ Sent N item(s) via bridge (seq=…). Switching to Tranzor…`
   - Tranzor Platform 标签页自动打开（或聚焦已打开的那个）

5. **在 Tranzor 标签页上用侧栏**（页面右侧）：

   ```
   ┌────────────────────────────────────────┐
   │ 📋 Tranzor Bridge  port 48217 · 3b3db0 │
   ├────────────────────────────────────────┤
   │ Task 216 · zh-TW · 1/8 fixed           │
   │                                        │
   │ ┌──────────────────────────────────┐   │
   │ │ settings.profile.title           │   │
   │ │ zh-TW · LLM Retranslate          │   │
   │ │ [🔍 Find] [✓ Fixed] [⤵ Skip]     │   │
   │ └──────────────────────────────────┘   │
   │ ┌──────────────────────────────────┐   │
   │ │ greet.hello   （删除线）         │   │
   │ │ ✓ Fixed                          │   │
   │ └──────────────────────────────────┘   │
   │ …                                      │
   └────────────────────────────────────────┘
   ```

   - **🔍 Find**：把 String Key 自动填进 Tranzor 的搜索框并模拟回车，平台自身的过滤/列表会跳到对应条目
   - **✓ Fixed**：标记这条已修（加删除线 + 变绿）。再点一次取消
   - **⤵ Skip**：标记跳过（不计入"已修"进度，灰显）
   - **点 key 文本本身**：复制到剪贴板（如果 Find 没正确命中搜索框，可手动粘贴）
   - **点侧栏头部**：折叠/展开侧栏

6. **在 Tranzor 上正常修复译文**，修完点 `✓ Fixed` 跟踪进度。逐条往下走，关闭标签页之后再打开，进度会被恢复（按 envelope ID 持久化）

---

## 故障排查

| 现象 | 含义 | 处理 |
|---|---|---|
| Send 按钮显示 `⚠ Bridge unavailable… Copied to clipboard.` | 桌面应用没开，或挂掉留下 stale port.json | 确认 TranzorExporter 在运行。然后在 Tranzor 侧栏底部的 textarea 里 `Ctrl+Shift+V`，envelope 会从剪贴板载入 |
| 侧栏始终显示 "no bridge" | 端口段被占满（≥10 个实例，或别的程序占了 48217–48226） | 重启 TranzorExporter；持续失败请看控制台是否输出 `BridgePortBusy`。剪贴板与 URL hash 降级通道照常可用 |
| `🔍 Find` 没反应 | Tranzor 的搜索框 selector 与默认匹配规则不一致 | 点 key 文本复制到剪贴板，手动粘贴到 Tranzor 的筛选框；如果是稳定问题可开 issue，让 userscript 学习新的 selector |
| 点完 Send 后侧栏却是空的 | userscript 还没拿到 token —— 看一下地址栏是不是有 `#tzbridge_token=…` | 如果有，刷新一次页面；如果没有，回报告里再点一次 Send，token 会在下次 Send 时一次性配对 |
| Send 按钮置灰 | 没勾选任何行，或所有勾选行被当前 filter 隐藏 | 勾选可见行；`Selected: N` 一旦 ≥ 1，按钮就会启用 |
| 新一次 Send 把之前的清单覆盖了 | 单槽收件箱（设计如此）：每次 Send 替换上一份 fix-list | 修完上一批再发下一批；或者放心 —— `已修/跳过`状态按 envelope ID 独立持久化，覆盖之后仍可恢复 |

---

## 工作原理（一段话）

桌面应用启动时在 `127.0.0.1:48217`（或往后第一个空闲端口，最多到 48226）拉起一个微型 HTTP 服务，用一段 32 字节随机 token 保护。HTML 报告会把端口和 token 直接嵌进自身的 JS 常量里，所以工具栏 `↗ Send to Tranzor` 按钮可以直接 POST 选中的行给桥。Tampermonkey userscript 跑在 Tranzor 平台标签页上，每 3 秒轮询桥的 `/pull` 接口拿最新 envelope 并渲染侧栏。Token 通过一次性的 URL hash（`#tzbridge_token=…`）传给 userscript，进 Tampermonkey 存储后立刻 `history.replaceState` 抹掉。**一切都在你本机上**：桥只听 loopback、严格 Origin allowlist，除了 `null`/`file://`（HTML 报告）和 Tranzor 平台域名之外任何来源都返回 403。

---

## 隐私与安全速览

- **仅 loopback**：绑定 `127.0.0.1`，不绑 `0.0.0.0` —— 局域网上任何人都看不到这个服务
- **每次启动换 token**：关闭重开桌面应用就会轮换 token；老报告的 token 失效后会自动降级到剪贴板通道
- **不替你调上游 API**：userscript 在 Tranzor 平台标签页内运行，复用你已经登录的 session cookie；桥本身永远不会替你调 Tranzor 的接口
- **发现文件**：`~/.tranzor_bridge/port.json`（POSIX `chmod 600`）启动时写入、关闭时清理

---

## 相关文档

- `TranzorExporter_QuickStart-zh.md` —— 桌面应用本体的快速上手（若没有中文版，请参考英文版 `TranzorExporter_QuickStart.md`）
- `tranzor_bridge.py` —— 桥服务源码（~250 行，纯标准库）
- `userscript/tranzor_bridge.user.js` —— userscript 源码
- `ROADMAP.md` 第 130 行 —— v0.2 计划（按 key 精准定位 DOM、`/ack` 进度回传、URL deep-link 探测）
