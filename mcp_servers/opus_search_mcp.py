"""OPUS Search MCP —— my-tools 的「第一个 MCP」。

把已存在的离线检索 :func:`opus_search.search_index` 暴露成一个 MCP 工具，
让任何 MCP 客户端（Claude Desktop / Claude Code / 公司 MCP Directory 里注册的
agent / 未来产品内的 agent_runner）都能"按 OPUS ID / 源文 / 译文 / 产品 检索
翻译"。

为什么拿它当第一个 MCP（对照演进方案 F.1 与 H 节红线）：
    - **只读**：仅 SELECT 本地 SQLite 索引，绝不写、绝不改平台数据（R1）。
    - **离线**：不发任何网络请求、不需要 Tranzor JWT —— 安全评审最容易过。
    - **确定性**：同一份 ``opus_index.db`` + 同一组参数 ⇒ 同样的结果（R3/R4）。
    - **零新逻辑**：只是薄薄包一层现有纯函数，不重写任何业务规则。

传输方式 = **stdio**（对应公司 MCP Directory 的 Import Method = ``command``）：
客户端用一条命令把本脚本拉起来，通过标准输入输出通信，不开端口、无网络暴露面。

本地运行 / 自测::

    pip install mcp
    python mcp_servers/opus_search_mcp.py          # 启动（等待客户端，stdio）
    python mcp_servers/smoke_test.py               # 一键握手自测

依赖说明：MCP server 是**独立进程**，不进 PyInstaller 打的 GUI 单文件 exe，
因此它额外依赖 ``mcp`` 包不会破坏主程序"仅 requests + openpyxl"的极简体质。
"""
from __future__ import annotations

import os
import sys

# 让本脚本无论从哪个工作目录被拉起，都能 import 到仓库根的 opus_search。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mcp.server.fastmcp import FastMCP  # noqa: E402

import opus_search  # noqa: E402  (仓库根的现有模块)

# 服务名 = 公司 MCP Directory 里的 Service Name。
mcp = FastMCP("opus-search")


@mcp.tool()
def opus_search_translations(
    opus_id: str | None = None,
    opus_match: str = "exact",
    product: str | None = None,
    target_language: str | None = None,
    source_contains: str | None = None,
    translation_contains: str | None = None,
    logical_key_contains: str | None = None,
    project_contains: str | None = None,
    limit: int = 50,
) -> dict:
    """在本地全量索引里检索 OPUS 翻译（只读 / 离线 / 确定性）。

    用一个 OPUS ID、一段源文、一段译文、或一个产品别名，立刻拿到命中串的
    **英文源 + 所有目标语言的最新译文**（Bug-Fixing 横向视图）。

    参数:
        opus_id: OPUS ID，配合 ``opus_match`` 使用。
        opus_match: ``exact`` | ``prefix`` | ``contains``，默认 ``exact``。
        product: 产品别名（如 ``uns`` / ``scp`` / ``chc``），精确匹配。
        target_language: 目标语言（如 ``de-DE``）；**单独不足以收窄**。
        source_contains: 英文源子串。
        translation_contains: 任一语言译文子串。
        logical_key_contains: OPUS ID 末段 logical key 子串。
        project_contains: project_id 子串（常为 GitLab 路径）。
        limit: 最多返回多少个 OPUS ID（1–2000）。

    必须提供至少一个收窄条件：``opus_id`` / ``product`` / ``source_contains`` /
    ``translation_contains`` / ``logical_key_contains`` / ``project_contains``。
    只给 ``target_language`` 会因无法收窄而报错（避免在百万行上裸扫）。

    返回:
        ``{"count", "truncated", "results": [{"opus_id", "source_text",
        "translations": [{"target_language", "translated_text"}], ...}]}``
    """
    return opus_search.search_index(
        opus_id=opus_id,
        opus_match=opus_match,
        product=product,
        target_language=target_language,
        source_contains=source_contains,
        translation_contains=translation_contains,
        logical_key_contains=logical_key_contains,
        project_contains=project_contains,
        limit=limit,
    )


if __name__ == "__main__":
    # 默认 stdio transport：交给上层 MCP 客户端用 command 方式拉起。
    mcp.run()
