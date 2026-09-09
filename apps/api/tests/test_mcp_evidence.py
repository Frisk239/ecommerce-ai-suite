"""MCP 连接层协议证据测试（goal §6.2.5 三断言的 pytest 版，第 38 刀）。

脚本级证据在 scripts/mcp_smoke.py --evidence（smoke 级，不进 CI）；本文件是
同一组断言的 TestClient 级版本，CI 可跑（DB 依赖走既有 skip 模式）：

- E1 工具列表恰四且无 publish——集合相等，未来偷加任何工具（包括 publish）即红。
- E2 未发布不进检索：register_asset 登记带独特标记词的文本（不发布）→
  search_published 查该标记词在登记前后零变化、探针资产与完整标记词永不
  出现（登记前取基线，对冲分词把标记词拆开后命中其它已发布内容的噪音）；
  登记返回即资产视图，status 落已接入/待人洗（均未发布）、
  current_published_version_no 为空；治理台 HTTP GET /api/assets 能看到这条
  mcp_registered 登记（spec Must 1 的治理台视角）。
- E3 活状态工具不暴露（反向断言，0021/0036 口径：订单/库存是活状态，内部
  走中台接口，活状态不进索引也不进连接层）——工具名出现 order/stock 字样即红。

先例与设施（session manager 单跑/手动 lifespan/ASGITransport）同
tests/test_mcp.py；每个测试重建 app 实例（同库同存储根）。
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from suite_api.main import create_app
from suite_api.settings import Settings

_REQUIRED_ENV = "SUITE_TEST_DATABASE_URL"
_TOKEN = "test-mcp-bearer"
_BASE = "http://localhost:8000"
_ENDPOINT = f"{_BASE}/mcp/"

_EXPECTED_TOOLS = {"search_published", "get_asset", "register_asset", "export_published"}
_FORBIDDEN_SUBSTRINGS = ("order", "stock")  # 0021/0036：活状态（订单/库存）不进连接层


def _payload(result: object) -> object:
    """call_tool 结果本地解包：structuredContent 优先（FastMCP 包在 result 键）。"""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if structured is not None:
        return structured
    texts = [b.text for b in result.content if hasattr(b, "text")]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return texts


@pytest.fixture(scope="module")
def evidence_env(tmp_path_factory: Path) -> tuple[Settings, Path]:
    """独立 suite_mcp_evidence_test 库 + 独立存储根；无真 Postgres 则 skip。"""
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5433/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = "suite_mcp_evidence_test"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    storage_root = tmp_path_factory.mktemp("mcp_evidence_objects")
    yield Settings(database_url=db_url, storage_root=storage_root, mcp_bearer_token=_TOKEN), (
        storage_root
    )
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _client_factory(app: object):
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE,
            follow_redirects=True,
            headers=headers,
            timeout=timeout or httpx.Timeout(30),
        )

    return factory


def _mcp_session(app: object, settings: Settings):
    """SDK client 的 httpx 注入工厂：全链路走 ASGITransport，不开端口。"""
    factory = _client_factory(app)
    return streamablehttp_client(
        _ENDPOINT,
        headers={"Authorization": f"Bearer {settings.mcp_bearer_token}"},
        httpx_client_factory=factory,
    )


def _run_with_lifespan(app: object, scenario: object):
    async def wrapped():
        # mounted 子应用的 lifespan 不执行：host 的 lifespan 手动进（同 test_mcp.py）
        async with app.router.lifespan_context(app):
            return await scenario()

    return asyncio.run(wrapped())


def _fresh_app(evidence_env: tuple[Settings, Path]):
    """每测试一个新 app 实例：FastMCP session manager 每实例仅允许一次 lifespan。"""
    settings, storage_root = evidence_env
    return create_app(
        Settings(
            database_url=settings.database_url,
            storage_root=storage_root,
            mcp_bearer_token=_TOKEN,
        )
    )


async def _list_tool_names(session: ClientSession) -> list[str]:
    tools = await session.list_tools()
    return sorted(t.name for t in tools.tools)


def test_evidence_e1_tools_exactly_four_no_publish(evidence_env) -> None:
    """E1：工具列表集合恰等于四工具、且不含 publish——多一个偷加的工具即红。"""
    app = _fresh_app(evidence_env)

    async def scenario() -> list[str]:
        async with _mcp_session(app, evidence_env[0]) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _list_tool_names(session)

    names = _run_with_lifespan(app, scenario)
    assert set(names) == _EXPECTED_TOOLS
    assert len(names) == 4
    assert "publish" not in names


def test_evidence_e2_unpublished_search_empty(evidence_env) -> None:
    """E2：register 只登记不发布 → 登记前后 search_published(标记词) 零差异，
    探针资产与完整标记词永不出现。

    活状态/未发布内容不进检索索引（goal §6.2.5 断言②）；登记前先取基线，
    对冲分词把标记词拆开后命中其它已发布内容的噪音；登记返回即资产视图
    （status=已接入/待人洗，均未发布）；治理台 HTTP GET 能看到这条登记
    （goal §6.2.5 断言③的治理台视角：连接层写入落治理队列，等待人洗与发布）。
    """
    settings, _storage_root = evidence_env
    app = _fresh_app(evidence_env)
    marker = f"evidence-unpublished-{uuid4().hex}"

    async def scenario() -> dict:
        out: dict = {}
        async with _mcp_session(app, settings) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                baseline = await session.call_tool("search_published", {"query": marker})
                assert not baseline.isError
                out["baseline_hits"] = _payload(baseline)

                reg = await session.call_tool(
                    "register_asset",
                    {
                        "content": f"协议证据未发布探针：标记词 {marker}。只登记不发布。",
                        "title": f"evidence probe {marker}",
                    },
                )
                assert not reg.isError
                out["registered"] = _payload(reg)

                probe = await session.call_tool("search_published", {"query": marker})
                assert not probe.isError
                out["probe_hits"] = _payload(probe)

        # 治理台视角（HTTP GET）：登记落在治理队列，操作者可见、未发布
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as api:
            login = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            assert login.status_code == 200
            api.cookies.update(login.cookies)
            listed = (await api.get("/api/assets")).json()
        out["governance"] = [a for a in listed if a["id"] == out["registered"]["id"]]
        return out

    out = _run_with_lifespan(app, scenario)

    # 检索零变化：未发布不进索引；探针资产与完整标记词永不出现
    assert out["probe_hits"] == out["baseline_hits"]
    assert marker not in json.dumps(out["probe_hits"], ensure_ascii=False, default=str)
    assert all(
        hit["asset_id"] != out["registered"]["id"]
        for hit in out["probe_hits"]
        if isinstance(hit, dict)
    )
    # 登记返回即资产视图：已接入/待人洗（均未发布）、来源固定连接层登记
    registered = out["registered"]
    assert registered["status"] in ("ingested", "pending_review")
    assert registered["source_kind"] == "mcp_registered"
    assert registered["current_published_version_no"] is None
    # 治理台 HTTP GET 可见：连接层写入对操作者不黑箱
    assert len(out["governance"]) == 1
    assert out["governance"][0]["status"] == registered["status"]
    assert out["governance"][0]["source_kind"] == "mcp_registered"


def test_evidence_e3_no_live_state_tools(evidence_env) -> None:
    """E3 反向断言（0021/0036 口径）：活状态（订单/库存）不进连接层。

    MCP 无订单/库存工具——goal 口径「活状态不进连接层」：工具列表不得出现
    get_order_status / get_stock / 任何含 order、stock 字样的工具名；订单/
    库存只读查询属客服引擎内部走中台接口（services/order_tools.py、
    services/stock_tools.py），与连接层四工具互斥。"""
    settings, _storage_root = evidence_env
    app = _fresh_app(evidence_env)

    async def scenario() -> list[str]:
        async with _mcp_session(app, settings) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _list_tool_names(session)

    names = _run_with_lifespan(app, scenario)
    leaked = [n for n in names if any(s in n for s in _FORBIDDEN_SUBSTRINGS)]
    assert leaked == []
    assert set(names) == _EXPECTED_TOOLS  # 恰四：search/get/register/export，无订单库存
