# mcp_servers —— my-tools 的 MCP 服务器

把 my-tools 里**已存在的纯逻辑**薄薄包一层，暴露成标准 [MCP](https://modelcontextprotocol.io)
工具，供 Claude Desktop / Claude Code / 公司 **RingCentral MCP Directory**
(`https://mcp.int.rclabenv.com`) 里注册的 agent 调用。

> 设计原则（对照演进方案 H 节红线）：**只读、离线优先、确定性、零新业务逻辑、
> stdio 无网络暴露面。** MCP server 是独立进程，**不进** PyInstaller 打的 GUI 单文件
> exe，所以它额外依赖 `mcp` 包不会破坏主程序"仅 requests + openpyxl"的极简体质。

## 当前清单

| Server | 文件 | 工具 | 封装的现有模块 | 传输 | 鉴权 |
|---|---|---|---|---|---|
| `opus-search` | `opus_search_mcp.py` | `opus_search_translations` | `opus_search.search_index` | stdio (`command`) | 无（纯本地 SQLite 只读） |

> 后续按演进方案 F.1 扩展：`xtm_mcp.py` / `tranzor_mcp.py` / `gitlab_mcp.py`（均薄封装现有 client）。

## 本地运行 / 自测

```bash
pip install mcp                              # 一次性，装 MCP Python SDK
python mcp_servers/opus_search_mcp.py        # 启动（stdio，等待客户端）
python mcp_servers/smoke_test.py             # 一键握手自测（initialize→list→call）
```

`smoke_test.py` 通过 = 协议链路正常。本地 `opus_index.db` 为空时 `count=0` 也算通过
（它只验证链路，不验证数据）。索引由桌面端的 OPUS ID Monitor 同步生成。

## 在 Claude Code / Claude Desktop 里用

仓库根的 [`.mcp.json`](../.mcp.json) 已注册本 server，在仓库目录启动 Claude Code 即自动加载：

```json
{
  "mcpServers": {
    "opus-search": { "command": "python", "args": ["mcp_servers/opus_search_mcp.py"] }
  }
}
```

Claude Desktop 用绝对路径：
`"args": ["C:\\Users\\susu82\\Tranzor-Platform\\my-tools\\mcp_servers\\opus_search_mcp.py"]`。

## 注册进公司 MCP Directory（`mcp.int.rclabenv.com`）

> 以下字段依据 MCP Service Management 页面截图整理；具体以 **New** 对话框实际字段为准。

1. **先本地验证**：`smoke_test.py` 通过。
2. **Directory → Management → 点【New】**（或【Batch Upload】+【Template】批量）。
3. 填写：
   - **Service Name**：`opus-search`
   - **Import Method**：`command`（本地 stdio，最省事，无需开端口/托管；
     参考目录里同为 `command` 的 *CMP Official MCP* / *kibana-mcp*）
   - **启动命令**：`python C:\Users\susu82\Tranzor-Platform\my-tools\mcp_servers\opus_search_mcp.py`
     （`command` 类型的 Import Content 列显示为 `-`，命令本身在 New 表单/详情里填）
   - **描述 / 标签**：如 `localization` / `opus` / `translation` / `read-only`
4. **安全评审**：建一个 **ASCON** Jira ticket（页面 Security Review Status 与 Jira Ticket 绑定，
   状态走 Approved / Rejected / Approved with constraints）。本 server 是
   **只读 + 离线 + 无鉴权 + 不外发数据**，是最容易过评审的形态——在 ticket 里讲清这四点。
5. Approved 后即出现在目录，可被 Star / Collect。

### 为什么第一个 MCP 选 `command`（stdio）而不是 `sse` / `streamable`
`sse` / `streamable` 需要把 server 托管成常驻 HTTP 服务、开端口、暴露 URL
（如 Botman 的 `https://mcp-botman.int.rclabenv.com/mcp`、Ultron 的 `http://10.32.52.71:300x/sse`），
多一层网络暴露面与运维。`command` 让客户端按需在**本机**拉起进程、用完即退，
零端口、零托管、零网络暴露——对一个跑在审查员本机、只读本地索引的工具是最优解。
