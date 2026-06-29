# 安全说明与威胁建模 — l10n-opus-id-search MCP

> 适用：`mcp_servers/opus_search_mcp.py`（公司 MCP Directory 展示名 **l10n-opus-id-search**，
> Service ID `0fuhlovz57gc`）。本文件用于 AppSec 评审（**ASCON-1781**，威胁建模 **ASCON-1782**），
> 同时作为本 MCP 的 SECURITY 说明。结论已逐条对照源码核实。

## TL;DR
只读、离线、stdio 本机运行、无鉴权、**零网络外发**、SQL 全参数化、不暴露任意文件读取。
攻击面接近一个 MCP 的最小值。真正需要管理的不是"能做什么"（几乎什么都不能），而是
"**返回什么**"——内部翻译内容 + 仓库坐标，定级 **Internal/Confidential**，故限定
**本地化团队本机受控客户端**使用。

## 数据分级（评审关键项）
索引 `~/.tranzor_exporter/opus_index.db` 定级 **RingCentral Internal / Confidential**：
- **含**：真实产品 UI 源文 + 全语种最新译文（**可能含 pre-release 功能文案、客户品牌串、EU 语种译文**）、内部 GitLab 路径 / 源文件路径 / MR 元数据。
- **不含**：JWT / API key / 密码 / 任何 PII。

## 数据流与信任边界
```
[信任边界 A：本机进程空间]
 MCP 客户端 ──stdio──▶ opus_search_mcp.py  (FastMCP "opus-search")
 (Claude Desktop/Code/      │ @tool opus_search_translations（9 个检索入参）
  受控 agent)               ▼
                  opus_search.search_index()
                    · 强制 ≥1 收窄条件 · limit ≤ 2000
                    · SQL 全 ? 参数化 · LIKE 元字符转义
                            ▼
                  opus_id_monitor._connect / init_db   （仅 SELECT；首用幂等建表 DDL）
                            ▼
                  ~/.tranzor_exporter/opus_index.db     （本地 SQLite，明文 / WAL）

 ✗ 无出站网络   ✗ 无监听端口   ✗ 不读 db_path 之外的文件
 索引由 opus_id_monitor.sync_* 离线灌入（本 MCP 不触发同步）
 来源：Tranzor MR Pipeline / Scan Task / File Translation
```
- **边界 A（stdio 进程间）**：唯一输入入口 = 9 个检索入参，全部受参数化 SQL 约束。
- **边界 B（代码完整性）**：server 启动即 `sys.path.insert` 并 import 整个 my-tools 仓库
  （`opus_search → opus_id_monitor`）。**真实信任边界 = 谁能写该仓库** → 控制：强制 PR review + 受保护分支。
- **边界 C（数据落盘）**：本地明文 SQLite，无文件级 ACL，依赖本机磁盘加密保护。

## 暴露字段与分级
| 字段 | 内容 | 分级 |
|---|---|---|
| `source_text` / `translated_text` | 真实 UI 源文 + 全语种最新译文（含 pre-release，各截断 8192 字符） | **Confidential** |
| `project_id` / `source_file_path` / `mr_iid` / `task_id` / `release` | GitLab 路径 / 源文件真实相对路径 / 发布管线元数据 | Internal |
| `opus_id` / `alias` / `logical_key` / `first_seen` | 标识与时间戳 | Internal |
| JWT / API key / 密码 / PII | **不存在于 schema** | — |

## 已有控制（可直接引用）
- **只读**：MCP 可达路径仅 `SELECT`；唯一副作用是首用时**幂等建表 DDL**（本地缓存 schema），不改任何 Tranzor 平台数据。
- **零外发**：全代码路径无 `requests/urllib/http/socket/httpx/smtp/webhook/telemetry` 等出站调用，无遥测。
- **参数化 SQL + LIKE 转义**：7 个 WHERE 条件全用 `?` 占位；`_esc()` 转义 `\ % _` 并配 `ESCAPE '\'`。无注入、无通配滥用。
- **收窄 + 上限**：无收窄条件即抛 `ValueError`；`limit = max(1, min(int(limit), 2000))`；先 `GROUP BY opus_id` 限 `limit+1` 再展开，抗资源耗尽。
- **无密钥**：本 MCP 不读任何 token / Authorization / 密钥环境变量（仅离线同步路径才用 JWT，本 MCP 从不调用同步）。
- **stdio 无监听端口**：`command` 模型本机按需拉起、用完即退，无网络监听面。
- **不暴露 `db_path`**：MCP 工具刻意不透传 `db_path`，库路径恒为默认，无任意文件读 / 换库面；`_connect` 仅 `sqlite3.connect`，无 `ATTACH`/`load_extension`。
- **最小依赖**：除 stdlib 外，MCP 可达代码唯一第三方包是 `mcp`（见 [`requirements.txt`](requirements.txt)，已钉 `==1.28.1`）。

## 残余风险与缓解
| 残余风险 | 缓解 |
|---|---|
| 返回机密译文 + 内部坐标，若被广域 LLM 客户端调用会进模型上下文 → pre-release 提前泄露 | 数据定级 + 限本地化团队本机受控客户端，**不进广域 Directory** |
| DB 明文落个人终端，无文件级加密 | 本机磁盘加密（BitLocker）+ 不外泄 + 离职/换机清理 |
| 第三方 `mcp` 供应链 | `requirements.txt` 钉版本；建议 `pip --require-hashes` + `pip-audit` |
| 代码完整性 = 仓库写权限治理 | 强制 PR review + 受保护分支 + CODEOWNERS |
| `.mcp.json` 用裸 `python` + 相对路径 | 已知低风险（本地工具，便于克隆即跑）；如需更严可固定到 venv 解释器绝对路径 |

## 申请的约束（constraints，主动接受）
1. 数据 **Internal/Confidential**，仅本地化团队本机受控 MCP 客户端，**不注册到对外/跨团队广域 Directory**。
2. 保持 **stdio 本机运行**，禁改 HTTP/SSE 监听端口。
3. 本机磁盘加密 + 不外泄 DB + 离职/换机清理。
4. 依赖**钉版本**（`requirements.txt`），更新需经审阅。
5. my-tools 仓库**强制 PR review + 受保护分支**。
