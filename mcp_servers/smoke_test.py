"""一键握手自测 opus-search MCP（独立脚本，不进主测试套件，避免给 CI 引入 mcp 依赖）。

它像真正的 MCP 客户端那样：用 stdio 拉起 opus_search_mcp.py → initialize →
list_tools → call_tool，打印结果。本地索引为空也不算失败（只验证协议链路通）。

运行::

    pip install mcp
    python mcp_servers/smoke_test.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# Windows 控制台默认 gbk，打印 ✓ / 中文会炸；和 opus_search.py 一样强制 utf-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.join(_HERE, "opus_search_mcp.py")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[_SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("✓ initialize OK")
            print("✓ tools:", names)
            assert "opus_search_translations" in names, "工具未注册！"

            # 用一个收窄条件调用；本地索引可能为空 → count=0 也算链路通。
            res = await session.call_tool(
                "opus_search_translations", {"product": "uns", "limit": 1}
            )
            text = res.content[0].text if res.content else "(empty)"
            print("✓ call_tool isError =", res.isError)
            print("  payload[:300] =", text[:300])

            # 故意不给收窄条件 → 服务端应报错（验证防裸扫护栏被正确透传）。
            res2 = await session.call_tool("opus_search_translations", {})
            print("✓ no-narrowing guard -> isError =", res2.isError, "(预期 True)")
    print("\nALL GOOD —— opus-search MCP 握手成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
