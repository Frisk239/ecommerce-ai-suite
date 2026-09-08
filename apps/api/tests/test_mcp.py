"""MCP 连接层测试（ADR 0032/0001/0020/0013/0017）。

- 单元：read_version_text（正常 / 非 UTF-8 / 对象缺失）。
- 鉴权：Bearer 闸门 fail-closed——无 header / 错 token / 空 token 配置全 401。
  闸门测试不起 lifespan（401 在 MCP 子应用外层中间件就返回，不碰 DB）。
- 协议集成（需 SUITE_TEST_DATABASE_URL，独立 suite_mcp_test 库）：官方 SDK
  client 经 httpx ASGITransport 直打挂载后的 app（不真起端口）；lifespan 用
  app.router.lifespan_context 手动进（session manager 与请求必须同 loop）。
  覆盖：恰好四工具且无 publish；检索只命中当前已发布指针版；get 默认当前版/
  历史已发布版/待人洗与已接入拒绝；register 落治理队列且 source_kind=
  mcp_registered；export 含正文全文。
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from suite_api.main import create_app
from suite_api.models import Asset, AssetVersion
from suite_api.services.asset_view import VersionTextError, read_version_text
from suite_api.settings import Settings
from suite_platform.storage import LocalDirectoryStorage

_REQUIRED_ENV = "SUITE_TEST_DATABASE_URL"
_TOKEN = "test-mcp-bearer"
# Host 需带端口才落在 SDK 默认 DNS-rebinding 允许清单（localhost:*）内
_BASE = "http://localhost:8000"
_ENDPOINT = f"{_BASE}/mcp/"

_V1_DOC = "钛钢保温杯产品说明\n净含量：480ml\n材质：316不锈钢。"
_SECRET_DOC = "内部机密参数表：实验室批次数据，仅待人洗可见。"
_DEMO_REGISTER = "MCP 集成测试登记文本：外部 Agent 通道写入，等待治理台人洗。"

McpEnv = tuple[object, Settings, Path]  # (app, settings, storage_root)


def _payload(result) -> object:
    """call_tool 结果本地解包：structuredContent 优先（FastMCP 包在 result 键），否则拼 text。"""
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


# ---------- 单元：read_version_text ----------


def test_read_version_text_roundtrip(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    storage.put_bytes("k1", "净含量：550毫升".encode())
    version = AssetVersion(object_key="k1")  # 不落库：读取只依赖对象键
    assert read_version_text(None, storage, version) == "净含量：550毫升"


def test_read_version_text_rejects_non_utf8(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    storage.put_bytes("k2", b"\xff\xfe\x00g\x00b")
    version = AssetVersion(object_key="k2")
    with pytest.raises(VersionTextError) as excinfo:
        read_version_text(None, storage, version)
    assert "UTF-8" in str(excinfo.value)


def test_read_version_text_missing_object(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    version = AssetVersion(object_key="gone")
    with pytest.raises(VersionTextError) as excinfo:
        read_version_text(None, storage, version)
    assert "对象缺失" in str(excinfo.value)


# ---------- 鉴权：Bearer 闸门（无 DB / 无 lifespan，401 在子应用外层返回） ----------


@pytest.fixture()
def gate_app(tmp_path: Path):
    settings = Settings(
        database_url="postgresql://unreachable:unreachable@localhost:1/none",
        storage_root=tmp_path,
        mcp_bearer_token=_TOKEN,
    )
    return create_app(settings), settings


def _init_request() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}


def test_gate_rejects_missing_header(gate_app) -> None:
    app, _ = gate_app

    async def scenario() -> int:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
            resp = await client.post("/mcp/", json=_init_request())
            return resp.status_code

    assert asyncio.run(scenario()) == 401


def test_gate_rejects_wrong_token(gate_app) -> None:
    app, _ = gate_app

    async def scenario() -> int:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE,
            headers={"Authorization": "Bearer not-the-token"},
        ) as client:
            resp = await client.post("/mcp/", json=_init_request())
            return resp.status_code

    assert asyncio.run(scenario()) == 401


def test_gate_rejects_everything_when_token_config_empty(gate_app) -> None:
    # ADR 0032：token 未配置/空 -> 全 401（fail-closed），即使请求带对了原值
    app, settings = gate_app

    async def scenario() -> int:
        settings.mcp_bearer_token = ""
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=_BASE,
                headers={"Authorization": f"Bearer {_TOKEN}"},
            ) as client:
                resp = await client.post("/mcp/", json=_init_request())
                return resp.status_code
        finally:
            settings.mcp_bearer_token = _TOKEN

    assert asyncio.run(scenario()) == 401


# ---------- 协议集成（官方 SDK client + ASGITransport，不真起端口） ----------


@pytest.fixture(scope="module")
def mcp_env(tmp_path_factory: Path) -> McpEnv:
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5432/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = "suite_mcp_test"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    storage_root = tmp_path_factory.mktemp("mcp_objects")
    settings = Settings(database_url=db_url, storage_root=storage_root, mcp_bearer_token=_TOKEN)
    app = create_app(settings)
    yield app, settings, storage_root
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _client_factory(app):
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE,
            follow_redirects=True,
            headers=headers,
            timeout=timeout or httpx.Timeout(30),
        )

    return factory


def _mcp_session(app, settings):
    """SDK client 的 httpx 注入工厂：全链路走 ASGITransport，不开端口。"""
    factory = _client_factory(app)
    return streamablehttp_client(
        _ENDPOINT, headers={"Authorization": f"Bearer {settings.mcp_bearer_token}"}, httpx_client_factory=factory
    )


def _run_with_lifespan(app, scenario):
    async def wrapped():
        # mounted 子应用的 lifespan 不执行：host 的 lifespan 手动进，
        # 保证 engine/迁移/种子与 MCP session manager 和请求同 loop
        async with app.router.lifespan_context(app):
            return await scenario()

    return asyncio.run(wrapped())


def test_mcp_full_readonly_and_register_flow(mcp_env: McpEnv) -> None:
    app, settings, _storage_root = mcp_env

    async def scenario() -> dict:
        out: dict = {}
        # -- 主服务零回归：挂载不改变 /health 与 /api/* 的行为 --
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as probe:
            out["health"] = (await probe.get("/health")).status_code
            out["api_assets_anon"] = (await probe.get("/api/assets")).status_code

        # -- 治理台准备数据：已发布 A（v1 真发布）、待人洗 B（从未发布） --
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as api:
            login = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            assert login.status_code == 200
            api.cookies.update(login.cookies)

            up_a = await api.post(
                "/api/assets/register",
                files={"file": ("cup.txt", _V1_DOC.encode("utf-8"), "text/plain")},
                data={"title": "保温杯规格"},
            )
            assert up_a.status_code == 201, up_a.text
            asset_a = up_a.json()["id"]
            # 不挂商品 -> 无规格必填，机洗成功后直接可发布
            pub = await api.post(f"/api/assets/{asset_a}/publish")
            assert pub.status_code == 200, pub.text

            up_b = await api.post(
                "/api/assets/register",
                files={"file": ("secret.txt", _SECRET_DOC.encode("utf-8"), "text/plain")},
                data={"title": "内部机密"},
            )
            assert up_b.status_code == 201, up_b.text
            asset_b = up_b.json()["id"]

            # 真开修订 → 发布 v2 → 再开修订留下未发布 v3（未发布拒绝用）
            rev = await api.post(f"/api/assets/{asset_a}/revisions")
            assert rev.status_code == 201, rev.text
            pub2 = await api.post(f"/api/assets/{asset_a}/publish")
            assert pub2.status_code == 200, pub2.text
            rev3 = await api.post(f"/api/assets/{asset_a}/revisions")
            assert rev3.status_code == 201, rev3.text
        out["asset_a"], out["asset_b"] = asset_a, asset_b

        # -- MCP：四工具 --
        async with _mcp_session(app, settings) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = sorted(t.name for t in (await session.list_tools()).tools)
                out["tools"] = tools

                search = await session.call_tool("search_published", {"query": "保温杯净含量"})
                assert not search.isError
                hits = _payload(search)
                out["search_hits"] = hits
                secret_search = await session.call_tool("search_published", {"query": "机密参数"})
                out["secret_search"] = _payload(secret_search)

                got_current = await session.call_tool("get_asset", {"asset_id": asset_a})
                out["get_current"] = _payload(got_current)
                got_v1 = await session.call_tool(
                    "get_asset", {"asset_id": asset_a, "version": 1}
                )
                out["get_v1"] = _payload(got_v1)
                got_v3 = await session.call_tool(
                    "get_asset", {"asset_id": asset_a, "version": 3}
                )
                out["get_v3_is_error"] = got_v3.isError
                out["get_v3_text"] = _payload(got_v3)
                got_b = await session.call_tool("get_asset", {"asset_id": asset_b})
                out["get_b_is_error"] = got_b.isError
                got_b_v1 = await session.call_tool(
                    "get_asset", {"asset_id": asset_b, "version": 1}
                )
                out["get_b_v1_is_error"] = got_b_v1.isError

                reg = await session.call_tool(
                    "register_asset", {"content": _DEMO_REGISTER, "title": "MCP 登记演示"}
                )
                out["register"] = _payload(reg)
                out["register_is_error"] = reg.isError
                reg_empty = await session.call_tool("register_asset", {"content": "  "})
                out["register_empty_is_error"] = reg_empty.isError
                out["register_empty_text"] = _payload(reg_empty)

                exported = await session.call_tool("export_published", {})
                out["export"] = _payload(exported)

        # -- 治理台队列可见 MCP 登记（source_kind=mcp_registered）--
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as api:
            relogin = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            api.cookies.set("suite_session", relogin.cookies["suite_session"])
            listed = (await api.get("/api/assets", params={"status": "pending_review"})).json()
        out["governance_pending"] = [
            a for a in listed if a.get("source_kind") == "mcp_registered"
        ]
        return out

    out = _run_with_lifespan(app, scenario)

    # /health 与 /api/* 零变化（/health 语义与既有测试同口径：DB 可达性如实上报，
    # 这里只验证挂载没有劫持它们——不是 404/401）
    assert out["health"] in (200, 503)
    assert out["api_assets_anon"] == 401

    # 恰好四工具，无 publish
    assert out["tools"] == ["export_published", "get_asset", "register_asset", "search_published"]
    assert all("publish" != t for t in out["tools"])

    # 检索只命中当前已发布指针版（v2），待人洗 B 与 v1 旧块不出现
    hits = out["search_hits"]
    assert hits, "应命中已发布资产"
    assert {h["asset_id"] for h in hits} == {out["asset_a"]}
    assert all(h["version_no"] == 2 for h in hits)
    assert {h["version_no"] for h in hits} == {2}
    first = hits[0]
    assert {"asset_id", "version_no", "title", "chunk", "score"} <= set(first)
    assert out["secret_search"] == []

    # get：默认=当前指针版；历史已发布版可取；v3 未发布修订 / B 已接入拒绝且不泄漏
    assert out["get_current"]["version_no"] == 2
    assert "480ml" in out["get_current"]["content"]
    assert out["get_current"]["source_kind"] == "upload"
    assert out["get_v1"]["version_no"] == 1
    assert "480ml" in out["get_v1"]["content"]
    assert out["get_v3_is_error"] and "已发布" in str(out["get_v3_text"])
    assert "实验室" not in str(out["get_v3_text"])  # 未发布/待人洗内容不泄漏
    assert out["get_b_is_error"] and out["get_b_v1_is_error"]

    # register：落已接入/待人洗 + source_kind=mcp_registered + 治理队列可见
    assert not out["register_is_error"]
    registered = out["register"]
    assert registered["kind"] == "document"
    assert registered["source_kind"] == "mcp_registered"
    assert registered["status"] in ("ingested", "pending_review")
    assert registered["current_published_version_no"] is None
    assert out["register_empty_is_error"]
    assert "正文" in str(out["register_empty_text"])  # 0013 空壳拒绝
    assert any(a["id"] == registered["id"] for a in out["governance_pending"])

    # export：当前已发布（指针版），含正文全文；B / v3 不在
    exported = out["export"]
    a_entries = [e for e in exported if e["asset_id"] == out["asset_a"]]
    assert len(a_entries) == 1
    assert a_entries[0]["version_no"] == 2
    assert a_entries[0]["content"] == _V1_DOC
    assert {e["asset_id"] for e in exported} == {out["asset_a"]}
    assert all("实验室" not in e["content"] for e in exported)
    assert {"asset_id", "version_no", "title", "kind", "source_kind", "content"} <= set(exported[0])


def test_mcp_wrong_token_fails_initialize(mcp_env: McpEnv) -> None:
    app, settings, _ = mcp_env
    settings.mcp_bearer_token = "rotated"
    try:
        factory = _client_factory(app)

        async def scenario() -> str:
            async with streamablehttp_client(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {_TOKEN}"},
                httpx_client_factory=factory,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return "unexpected-success"

        # 旧 token 在 initialize 即被 401 拒（SDK 把后台任务的 HTTPStatusError
        # 包进 anyio ExceptionGroup，测试沿组树找根因）
        with pytest.raises(BaseException) as excinfo:
            asyncio.run(scenario())

        def _has_401(exc: BaseException) -> bool:
            if isinstance(exc, httpx.HTTPStatusError):
                return exc.response.status_code == 401
            return any(_has_401(sub) for sub in getattr(exc, "exceptions", []))

        assert _has_401(excinfo.value)
    finally:
        settings.mcp_bearer_token = _TOKEN


def test_export_published_writes_audit_trace(mcp_env: McpEnv) -> None:
    """export 留痕钉测（第 22 刀/ADR 0041 顺手件）。

    - 成功导出：每份当前已发布资产写一行 audit_log（action="export"，含当时
      版本号），operator 归属系统行「mcp」（血缘时间线里如实显示操作者名）；
    - 血缘写回不混入：writebacks 的 action 只有 publish/rollback，export 行
      只出现在 versions_audit（services/lineage.WRITEBACK_ACTIONS 钉死）；
    - 「mcp」账号永远登不进控制台（password_hash 非合法 bcrypt 串）；
    - 留痕只记元数据（asset_id/version_no），不含导出正文。

    同 host 不能第二次进 lifespan：FastMCP session manager 的 run() 每实例仅
    允许一次（SDK 文档字符串），全链用例已耗尽 mcp_env 的 app——这里重建一个
    app 实例（同库同存储根：export 要能读到前案登记资产的字节）。
    """
    _, settings, storage_root = mcp_env
    app = create_app(
        Settings(
            database_url=settings.database_url,
            storage_root=storage_root,
            mcp_bearer_token=_TOKEN,
        )
    )
    # 前案（全链）的 export 也为资产 A 写过留痕：本用例只盯自己新登记的资产
    async def scenario() -> dict:
        out: dict = {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as api:
            login = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            assert login.status_code == 200
            api.cookies.update(login.cookies)
            # mcp 系统账号不可登录（留痕归属专用）
            mcp_login = await api.post(
                "/api/auth/login", json={"username": "mcp", "password": "!"}
            )
            out["mcp_login"] = mcp_login.status_code

            up = await api.post(
                "/api/assets/register",
                files={
                    "file": (
                        "export-trace.txt",
                        "导出留痕测试：净含量 500ml。".encode(),
                        "text/plain",
                    )
                },
                data={"title": "导出留痕测试"},
            )
            assert up.status_code == 201, up.text
            asset_id = up.json()["id"]
            pub = await api.post(f"/api/assets/{asset_id}/publish")
            assert pub.status_code == 200, pub.text
            out["asset_id"] = asset_id

        async with _mcp_session(app, settings) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                exported = await session.call_tool("export_published", {})
                assert not exported.isError
                entries = _payload(exported)
                mine = [e for e in entries if e["asset_id"] == asset_id]
                out["exported"] = [
                    {"asset_id": e["asset_id"], "version_no": e["version_no"]} for e in mine
                ]
                # 留痕不存正文：导出结果里有 content，audit 表列里没有
                out["exported_has_content"] = "content" in mine[0]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as api:
            login = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            api.cookies.update(login.cookies)
            out["audit"] = (await api.get("/api/audit", params={"assetId": asset_id})).json()
            lineage = await api.get(f"/api/assets/{asset_id}/lineage")
            assert lineage.status_code == 200
            out["lineage"] = lineage.json()
            return out

    out = _run_with_lifespan(app, scenario)

    assert out["mcp_login"] == 401
    assert out["exported"] == [{"asset_id": out["asset_id"], "version_no": 1}]
    assert out["exported_has_content"]  # 导出响应形状不变（正文仍在），留痕行不含
    audit_by_action: dict[str, int] = {}
    for row in out["audit"]:
        audit_by_action[row["action"]] = audit_by_action.get(row["action"], 0) + 1
    assert audit_by_action.get("export") == 1  # 导出一次=一行
    assert audit_by_action.get("publish") == 1  # 既有发布留痕不受影响

    timeline = out["lineage"]["versions_audit"]
    export_events = [e for e in timeline if e["action"] == "export"]
    assert len(export_events) == 1
    assert export_events[0]["version_no"] == 1
    assert export_events[0]["operator"] == "mcp"  # 操作者名如实显示，与真人可分辨
    writeback_actions = {w["action"] for w in out["lineage"]["usages"]["writebacks"]}
    assert "export" not in writeback_actions  # 不混入写回环
    assert writeback_actions <= {"publish", "rollback"}
    # 第 26 刀缺口路③（血缘导出环）：export 留痕从 versions_audit 派生进
    # usages.exports（「export 留痕→lineage 出现」端到端钉死）
    assert out["lineage"]["usages"]["exports"] == [
        {
            "at": export_events[0]["at"],
            "action": "export",
            "version_no": 1,
            "operator": "mcp",
        }
    ]


def test_mcp_title_masked_on_all_three_read_exits(mcp_env: McpEnv) -> None:
    """第 26 刀 P1②：MCP 三处只读出口（search/get/export）的 title 统一过
    redact（切片 title=裸转写截断、素材/上传 title 非净源——出口侧收口）；
    assets.title 原文不动（出口掩、不回写行，0038 修订「字节不动」同口径）。

    同 host 不能第二次进 lifespan（session manager 单跑），重建 app 实例。"""
    _, settings, storage_root = mcp_env
    app = create_app(
        Settings(
            database_url=settings.database_url,
            storage_root=storage_root,
            mcp_bearer_token=_TOKEN,
        )
    )
    title_raw = "保温杯售后咨询 13812345678 号"
    title_masked = "保温杯售后咨询 1********78 号"
    body = f"退款流程说明\n净含量：480ml\n咨询窗口：{title_raw}"

    async def scenario() -> dict:
        out: dict = {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE
        ) as api:
            login = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            assert login.status_code == 200
            api.cookies.update(login.cookies)
            up = await api.post(
                "/api/assets/register",
                files={"file": ("title-mask.txt", body.encode(), "text/plain")},
                data={"title": title_raw},
            )
            assert up.status_code == 201, up.text
            asset_id = up.json()["id"]
            # 登记即落原文（出口掩不在写入侧——与版本字节不动同一纪律）
            out["registered_title"] = up.json()["title"]
            out["asset_id"] = asset_id
            assert (await api.post(f"/api/assets/{asset_id}/publish")).status_code == 200

        async with _mcp_session(app, settings) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                search = await session.call_tool("search_published", {"query": "退款流程净含量"})
                assert not search.isError
                out["search"] = _payload(search)
                got = await session.call_tool("get_asset", {"asset_id": asset_id})
                assert not got.isError
                out["get"] = _payload(got)
                exported = await session.call_tool("export_published", {})
                assert not exported.isError
                out["export"] = _payload(exported)
        return out

    out = _run_with_lifespan(app, scenario)

    assert out["registered_title"] == title_raw  # 登记响应=治理台内部面，不在本刀范围
    asset_id = out["asset_id"]
    titles = [h["title"] for h in out["search"] if h["asset_id"] == asset_id]
    assert titles and all(t == title_masked for t in titles)  # 出口 1：search 掩
    assert out["get"]["title"] == title_masked  # 出口 2：get_asset 掩
    mine_export = [e for e in out["export"] if e["asset_id"] == asset_id]
    assert [e["title"] for e in mine_export] == [title_masked]  # 出口 3：export 掩
    # 三出口响应整体无裸号
    assert "13812345678" not in json.dumps(
        [out["search"], out["get"], out["export"]], ensure_ascii=False
    )
    # 行原文不动（出口掩不回写 assets.title，0038 修订「字节不动」同口径）
    with app.state.session_factory() as db:
        assert db.get(Asset, asset_id).title == title_raw
