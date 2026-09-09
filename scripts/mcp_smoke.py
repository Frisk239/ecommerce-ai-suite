"""MCP 连接层冒烟脚本（ADR 0032 演示与验收）：官方 SDK client 打 /mcp/。

用法（仓库根，先起 api 与已发布资产）：

    MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py
    MCP_BEARER_TOKEN=... uv run python scripts/mcp_smoke.py 保温杯 1 1
    # 协议证据模式（goal §6.2.5 三断言，供答辩引用）：
    MCP_BEARER_TOKEN=... uv run python scripts/mcp_smoke.py --evidence

环境变量：
- MCP_URL：默认 http://localhost:8000/mcp/
- MCP_BEARER_TOKEN：必填（token 只从 env 读，脚本里不硬编码）

流程：initialize -> list_tools（断言四工具且无 publish）-> 依次调用
search_published / get_asset（当前版与历史版）/ register_asset（演示文本）/
export_published，打印结构化摘要。任一环节失败以非零码退出。

--evidence：在原流程后追加连接层协议证据断言（goal §6.2.5）——
E0 register 落已接入（工具返回即资产视图，status=ingested/pending_review）；
E1 工具列表恰四且无 publish；E2 未发布不进检索（登记带独特标记词、不发布，
search_published 查该标记词必空）；E3 活状态工具不暴露（0021/0036：订单/
库存走中台接口，活状态不进索引也不进连接层）。每断言一行
PASS/FAIL <断言名>: <实际值>；任一 FAIL 以非零码退出。脚本级证据，
不进 CI（pytest 版在 apps/api/apps/api/tests/test_mcp_evidence.py）。
"""

import asyncio
import json
import os
import sys
import time
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


def _unwrapped(result: Any) -> Any:
    """FastMCP 把 dict 返回值包在 structuredContent 的 result 键——剥掉这层。"""
    payload = _tool_text(result)
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


async def main(argv: list[str]) -> int:
    evidence = "--evidence" in argv
    positional = [a for a in argv if a != "--evidence"]
    url = os.environ.get("MCP_URL", "http://localhost:8000/mcp/")
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        print("缺少 MCP_BEARER_TOKEN（从 env 读，脚本不硬编码）", file=sys.stderr)
        return 2
    query = positional[0] if len(positional) > 0 else "保温杯"
    asset_id = int(positional[1]) if len(positional) > 1 else 1
    version = int(positional[2]) if len(positional) > 2 else 1
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

            got_hist = await session.call_tool(
                "get_asset", {"asset_id": asset_id, "version": version}
            )
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

            if evidence:
                return await _evidence_checks(session, names, reg)

    print("smoke OK：initialize / list_tools / 四工具全部通过")
    return 0


async def _evidence_checks(session: ClientSession, names: list[str], reg: Any) -> int:
    """goal §6.2.5 协议证据：冒烟全链之后追加四条断言，结构化摘要逐行输出。"""
    checks: list[tuple[str, bool, str]] = []

    # E0 register→已接入：register 返回即资产视图（services.asset_view.to_asset_out），
    # status 只会是 ingested（机洗失败停）或 pending_review（机洗成功推进）——均未发布。
    registered = _unwrapped(reg)
    status = registered.get("status") if isinstance(registered, dict) else None
    checks.append(
        (
            "E0 register_status_ingested",
            status in ("ingested", "pending_review"),
            f"register_asset 返回 status={status}",
        )
    )

    # E1 工具列表恰四且无 publish（集合相等：未来偷加任何工具——包括
    # publish——都会让集合不等而 FAIL）。
    checks.append(
        (
            "E1 tools_exactly_four_no_publish",
            set(names) == _EXPECTED_TOOLS and "publish" not in names,
            f"工具列表={names}",
        )
    )

    # E2 未发布不进检索：登记一份带独特标记词的演示文本（只登记、不发布），
    # 断言登记前后 search_published(标记词) 结果零变化，且探针资产与完整
    # 标记词永不出现——活状态/未发布内容不进检索索引（登记前先取基线，
    # 对冲分词把标记词拆开后命中其它已发布内容的噪音）。
    marker = f"evidence-unpublished-{int(time.time() * 1000)}"
    baseline = await session.call_tool("search_published", {"query": marker})
    base_hits = None if baseline.isError else _unwrapped(baseline)
    base_hits = base_hits if isinstance(base_hits, list) else None
    reg2 = await session.call_tool(
        "register_asset",
        {
            "content": f"协议证据未发布探针：标记词 {marker}。只登记不发布，未发布不进检索。",
            "title": f"evidence probe {marker}",
        },
    )
    if reg2.isError:
        checks.append(("E2 unpublished_search_empty", False, "register_asset 探针登记失败"))
    else:
        reg2_out = _unwrapped(reg2)
        reg2_id = reg2_out.get("id") if isinstance(reg2_out, dict) else None
        probe = await session.call_tool("search_published", {"query": marker})
        probe_hits = None if probe.isError else _unwrapped(probe)
        probe_hits = probe_hits if isinstance(probe_hits, list) else None
        stable = base_hits is not None and probe_hits == base_hits
        absent = probe_hits is not None and all(
            h.get("asset_id") != reg2_id for h in probe_hits
        )
        clean = marker not in json.dumps(probe_hits or [], ensure_ascii=False, default=str)
        checks.append(
            (
                "E2 unpublished_search_empty",
                stable and absent and clean,
                f"登记前后命中 {len(base_hits or [])}→{len(probe_hits or [])} 条，"
                f"探针资产 id={reg2_id} 未出现（活状态/未发布不进检索）",
            )
        )

    # E3 活状态工具不暴露（0021/0036 口径：订单/库存是活状态，内部走中台接口，
    # 不进索引也不进连接层）——工具名出现 order/stock 任一字样即 FAIL。
    forbidden = [n for n in names if "order" in n or "stock" in n]
    checks.append(
        (
            "E3 no_live_state_tools",
            not forbidden,
            f"订单/库存字样工具={forbidden if forbidden else '无'}",
        )
    )

    print("== 协议证据摘要（goal §6.2.5 三断言 + 活状态反向断言） ==")
    for name, ok, actual in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {actual}")
    if not all(ok for _, ok, _ in checks):
        print("协议证据存在 FAIL", file=sys.stderr)
        return 1
    print("evidence OK：E0/E1/E2/E3 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
