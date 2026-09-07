"""MCP 连接层冒烟脚本（ADR 0032 演示与验收）：官方 SDK client 打 /mcp/。

用法（仓库根，先起 api 与已发布资产）：

    MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py
    MCP_BEARER_TOKEN=... uv run python scripts/mcp_smoke.py 保温杯 1 1

环境变量：
- MCP_URL：默认 http://localhost:8000/mcp/
- MCP_BEARER_TOKEN：必填（token 只从 env 读，脚本里不硬编码）

流程：initialize -> list_tools（断言四工具且无 publish）-> 依次调用
search_published / get_asset（当前版与历史版）/ register_asset（演示文本）/
export_published，打印结构化摘要。任一环节失败以非零码退出。
"""

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_EXPECTED_TOOLS = {"search_published", "get_asset", "register_asset", "export_published"}

_DEMO_CONTENT = (
    "MCP 连接层冒烟登记：本段文本由 scripts/mcp_smoke.py 经 register_asset 工具写入，"
    "用于演示外部 Agent 通道。来源固定 mcp_registered，落为已接入，等待治理台人洗与发布。"
)


def _dump(label: str, payload: Any) -> None:
    print(f"== {label} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:2000])


def _tool_text(result: Any) -> Any:
    """call_tool 结果的本地摘要：结构化内容优先；text 块是 JSON 时解析后返回。"""
    if result.structuredContent:
        return result.structuredContent
    texts = [c.text for c in result.content if hasattr(c, "text")]
    joined = "\n".join(texts)
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return joined
    return joined or repr(result.content)


async def main(argv: list[str]) -> int:
    url = os.environ.get("MCP_URL", "http://localhost:8000/mcp/")
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        print("缺少 MCP_BEARER_TOKEN（从 env 读，脚本不硬编码）", file=sys.stderr)
        return 2
    query = argv[1] if len(argv) > 1 else "保温杯"
    asset_id = int(argv[2]) if len(argv) > 2 else 1
    version = int(argv[3]) if len(argv) > 3 else 1
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected: {url} server={init.serverInfo.name} protocol={init.protocolVersion}")

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"tools: {names}")
            if set(names) != _EXPECTED_TOOLS:
                print(f"工具集合不符（期望恰好 {_EXPECTED_TOOLS}，且无 publish）", file=sys.stderr)
                return 1

            search = await session.call_tool("search_published", {"query": query})
            hits = _tool_text(search)
            _dump(f"search_published({query!r})", hits)

            got = await session.call_tool("get_asset", {"asset_id": asset_id})
            _dump(f"get_asset({asset_id}) 当前已发布版", _tool_text(got))

            got_hist = await session.call_tool("get_asset", {"asset_id": asset_id, "version": version})
            _dump(f"get_asset({asset_id}, version={version}) 历史已发布版", _tool_text(got_hist))

            reg = await session.call_tool(
                "register_asset",
                {"content": _DEMO_CONTENT, "title": f"mcp-smoke {query}"},
            )
            _dump("register_asset(演示文本)", _tool_text(reg))

            exported = await session.call_tool("export_published", {})
            _dump("export_published()", _tool_text(exported))

            failures = [r for r in (search, got, got_hist, reg, exported) if r.isError]
            if failures:
                print(f"{len(failures)} 个工具返回错误", file=sys.stderr)
                return 1
    print("smoke OK：initialize / list_tools / 四工具全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
