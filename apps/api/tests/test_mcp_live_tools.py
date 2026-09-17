"""MCP 活状态只读三工具功能钉测（第 99 刀，ADR 0057）。

活状态（商品/库存/订单）以只读工具进连接层——执行入口是客服 agent loop 的
TOOL_REGISTRY 同一条目（同一份白名单校验 + 同一个执行函数），本文件钉：

- get_product：查有（部分名 LCS 命中+价格/库存/规格摘要字段）、查无（含空名）
  与类目聚合（类目名与口语别名，件数/定价数/价格区间）；spec_values 摘要出口
  过 redact（0038 出口必掩：写回值可能混人工填的联系方式）。
- get_stock：查有（单品）/类目聚合/查无，与客服 get_stock 同一口径（含
  query_stock 的先类目后单品与部分名容错）。
- get_order_status：查有/查无/小写单号归一/坏格式拒绝（注册表 SO-\\d+ 白名单，
  与客服提议步同一道闸）；**脱敏钉**：返回不含顾客联系方式——mock 种子单本无
  PII，测试数据补一条带电话的订单（事件文本 + 联系键），断言出口剥键+文本打码。

协议面（恰七、无 publish、活状态工具描述无写动作字样）钉在
tests/test_mcp_evidence.py；设施（独立库/每测试新 app/手动 lifespan/
ASGITransport）同 tests/test_mcp.py 先例。
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import select

from suite_api.deps import ensure_engine
from suite_api.main import create_app
from suite_api.models import Order, Product
from suite_api.settings import Settings

_REQUIRED_ENV = "SUITE_TEST_DATABASE_URL"
_TOKEN = "test-mcp-bearer"
_BASE = "http://localhost:8000"
_ENDPOINT = f"{_BASE}/mcp/"

_PHONE = "13812345678"
_PHONE_MASKED = "1********78"


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
def live_env(tmp_path_factory: Path) -> tuple[Settings, Path]:
    """独立 suite_mcp_live_test 库 + 独立存储根；无真 Postgres 则 skip。"""
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5433/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = "suite_mcp_live_test"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    storage_root = tmp_path_factory.mktemp("mcp_live_objects")
    yield Settings(database_url=db_url, storage_root=storage_root, mcp_bearer_token=_TOKEN), (
        storage_root
    )
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _fresh_app(live_env: tuple[Settings, Path]):
    """每测试一个新 app 实例：FastMCP session manager 每实例仅允许一次 lifespan。"""
    settings, storage_root = live_env
    return create_app(
        Settings(
            database_url=settings.database_url,
            storage_root=storage_root,
            mcp_bearer_token=_TOKEN,
        )
    )


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
    factory = _client_factory(app)
    return streamablehttp_client(
        _ENDPOINT,
        headers={"Authorization": f"Bearer {settings.mcp_bearer_token}"},
        httpx_client_factory=factory,
    )


def _run_with_lifespan(app: object, scenario: object):
    async def wrapped():
        async with app.router.lifespan_context(app):
            return await scenario()

    return asyncio.run(wrapped())


async def _call(session: ClientSession, name: str, args: dict) -> object:
    result = await session.call_tool(name, args)
    return result


def test_live_product_found_price_and_spec(live_env) -> None:
    """get_product 查有：部分名 LCS 命中、行价/币种/库存字段、spec_values 摘要
    （{字段: 值}，丢治理元数据；字符串值出口过 redact——写回值里的人工
    联系方式不出连接层）。"""

    async def scenario(app) -> dict:
        ensure_engine(app)
        with app.state.session_factory() as db:
            cup = db.scalar(select(Product).where(Product.name == "钛钢保温杯"))
            cup.spec_values = {
                "净含量": {"value": "480ml", "source": "machine"},
                "材质": {"value": "316不锈钢", "source": "operator"},
                "空值": {"value": None, "source": "operator"},
                "客服备注": {"value": f"定制热线{_PHONE}", "source": "operator"},
            }
            db.commit()
        async with _mcp_session(app, live_env[0]) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                hit = await _call(session, "get_product", {"name": "保温杯"})
                miss = await _call(session, "get_product", {"name": "量子泡沫发生器"})
                empty = await _call(session, "get_product", {"name": "  "})
                return {
                    "hit": _payload(hit),
                    "hit_is_error": hit.isError,
                    "miss": _payload(miss),
                    "empty": _payload(empty),
                }

    app = _fresh_app(live_env)
    out = _run_with_lifespan(app, lambda: scenario(app))

    assert not out["hit_is_error"]
    hit = out["hit"]
    # 部分名「保温杯」命中种子单品「钛钢保温杯」（客服 match_product 同口径）
    assert hit["found"] is True
    assert hit["name"] == "钛钢保温杯"
    assert hit["category"] == "器皿"
    assert hit["price_cents"] == 12900  # 第 41 刀类目基准演示价（分）
    assert hit["currency"] == "CNY"
    assert hit["stock"] == 42  # 0037 mock 值，与客服 get_stock 同一列
    # 摘要形状：{字段: 值}；None 值不进摘要；字符串值出口过 redact
    assert hit["spec_values"] == {
        "净含量": "480ml",
        "材质": "316不锈钢",
        "客服备注": f"定制热线{_PHONE_MASKED}",
    }
    assert _PHONE not in json.dumps(hit, ensure_ascii=False)

    # 查无：与 get_order_status 查无同形（{found: False}），空名同形
    assert out["miss"] == {"found": False}
    assert out["empty"] == {"found": False}


def test_live_product_category_aggregate(live_env) -> None:
    """get_product 类目聚合：类目名（器皿）与口语别名（杯子→器皿）都给
    {found, category, total, in_stock, priced, 价格区间}；聚合按库内类目算
    （客服 category_targets 同一候选表）。种子库器皿只有保温杯一件已定价。"""

    async def scenario(app) -> dict:
        async with _mcp_session(app, live_env[0]) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                by_name = await _call(session, "get_product", {"name": "器皿"})
                by_alias = await _call(session, "get_product", {"name": "杯子"})
                return {"by_name": _payload(by_name), "by_alias": _payload(by_alias)}

    app = _fresh_app(live_env)
    out = _run_with_lifespan(app, lambda: scenario(app))

    for aggregate in (out["by_name"], out["by_alias"]):
        assert aggregate["found"] is True
        assert aggregate["category"] == "器皿"
        assert aggregate["total"] == 1  # 种子库器皿=钛钢保温杯一件
        assert aggregate["in_stock"] == 1
        assert aggregate["priced"] == 1
        assert aggregate["currency"] == "CNY"
        assert aggregate["price_from_cents"] == 12900
        assert aggregate["price_to_cents"] == 12900
    # 类目聚合不带单品字段（id/name/stock），两种问法同一形状
    assert out["by_name"] == out["by_alias"]


def test_live_stock_single_category_and_miss(live_env) -> None:
    """get_stock：单品查有（部分名）与查无、类目聚合（别名）——执行入口是客服
    TOOL_REGISTRY 的 get_stock 条目，query_stock 的先类目后单品口径原样生效。"""

    async def scenario(app) -> dict:
        async with _mcp_session(app, live_env[0]) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                single = await _call(session, "get_stock", {"product_name": "保温杯"})
                zero = await _call(session, "get_stock", {"product_name": "瓶装水"})
                category = await _call(session, "get_stock", {"product_name": "杯子"})
                miss = await _call(session, "get_stock", {"product_name": "量子泡沫"})
                return {
                    "single": _payload(single),
                    "zero": _payload(zero),
                    "category": _payload(category),
                    "miss": _payload(miss),
                }

    app = _fresh_app(live_env)
    out = _run_with_lifespan(app, lambda: scenario(app))

    # 单品：部分名命中钛钢保温杯，stock=42（与站内客服工具条同数）
    assert out["single"] == {"found": True, "product_name": "钛钢保温杯", "stock": 42}
    # stock=0 是事实数据（瓶装水演示「暂时无货」），照常返回
    assert out["zero"] == {"found": True, "product_name": "瓶装水", "stock": 0}
    # 类目聚合：别名「杯子」-> 器皿 {total, in_stock, stock_sum}
    assert out["category"] == {
        "found": True,
        "category": "器皿",
        "product_name": "器皿",
        "total": 1,
        "in_stock": 1,
        "stock_sum": 42,
    }
    assert out["miss"] == {"found": False}


def test_live_order_status_found_miss_and_validation(live_env) -> None:
    """get_order_status：查有（SO-1001 种子单）、查无 {found: False}、小写单号
    归一命中、坏格式被注册表白名单拒绝（isError——与客服提议步同一道闸，
    提示注入改不了工具参数资格）。"""

    async def scenario(app) -> dict:
        async with _mcp_session(app, live_env[0]) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                hit = await _call(session, "get_order_status", {"order_no": "SO-1001"})
                lower = await _call(session, "get_order_status", {"order_no": "so-1002"})
                miss = await _call(session, "get_order_status", {"order_no": "SO-9999"})
                bad = await _call(session, "get_order_status", {"order_no": "ABC-1"})
                return {
                    "hit": _payload(hit),
                    "lower": _payload(lower),
                    "miss": _payload(miss),
                    "bad_is_error": bad.isError,
                    "bad_text": _payload(bad),
                }

    app = _fresh_app(live_env)
    out = _run_with_lifespan(app, lambda: scenario(app))

    hit = out["hit"]
    assert hit["found"] is True
    assert hit["order_no"] == "SO-1001"
    assert hit["status"] == "已发货"
    assert {i["name"] for i in hit["items"]} == {"瓶装水", "钛钢保温杯"}
    assert len(hit["events"]) == 2
    # 小写单号归一大写命中（_validate_args 与客服 find_order_no 同口径）
    assert out["lower"]["found"] is True
    assert out["lower"]["order_no"] == "SO-1002"
    # 查无与客服同形：{found: False}，无订单细节泄漏
    assert out["miss"] == {"found": False}
    # 坏格式：注册表 SO-\d+ 白名单拒绝，是工具错误不是查无
    assert out["bad_is_error"]
    assert "order_no" in str(out["bad_text"])


def test_live_order_status_redaction_pin(live_env) -> None:
    """脱敏钉（ADR 0057）：订单 items/events 是 JSONB 自由形状——mock 种子单
    本无 PII，这里补一条带电话的订单（事件文本电话 + 事件/条目联系键），
    断言 MCP 出口：联系键被剥、事件文本电话被打码、裸号码全文不出现。"""

    async def scenario(app) -> dict:
        ensure_engine(app)
        with app.state.session_factory() as db:
            db.add(
                Order(
                    order_no="SO-9901",
                    status="已签收",
                    items=[
                        {"name": "钛钢保温杯", "qty": 1, "contact": _PHONE},
                        {"name": "瓶装水", "qty": 2},
                    ],
                    events=[
                        {
                            "at": "2026-09-10 09:00",
                            "text": f"顾客来电 {_PHONE} 预约送货",
                            "phone": _PHONE,
                            "email": "buyer@example.com",
                        },
                        {"at": "2026-09-10 18:00", "text": "已签收，放前台"},
                    ],
                    placed_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
                )
            )
            db.commit()
        async with _mcp_session(app, live_env[0]) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                got = await _call(session, "get_order_status", {"order_no": "SO-9901"})
                return _payload(got)

    app = _fresh_app(live_env)
    order = _run_with_lifespan(app, lambda: scenario(app))

    assert order["found"] is True
    # 顶层固定形状：无任何联系字段
    assert set(order) == {"found", "order_no", "status", "items", "events"}
    # 联系键被剥除（items/events 条目都不剩 phone/contact/email 键）
    assert all("contact" not in i and "phone" not in i for i in order["items"])
    assert all("phone" not in e and "email" not in e for e in order["events"])
    assert {i["name"] for i in order["items"]} == {"钛钢保温杯", "瓶装水"}
    # 事件文本里的电话被打码（保留前 1 后 2），裸号码全文不出现
    assert order["events"][0]["text"] == f"顾客来电 {_PHONE_MASKED} 预约送货"
    assert order["events"][1]["text"] == "已签收，放前台"
    assert _PHONE not in json.dumps(order, ensure_ascii=False)
