"""嵌入小组件闸与访客 id 集成测试（第 45b 刀；真 PG）。

路线图把 **origin 白名单**定为嵌入的唯一闸（服务端配置，非白名单 403）。本文件钉：

- 不带 `X-Widget-Origin` = 独立访问：行为完全不变（201，且落库无访客 id）；
- 白名单为空（默认未启用）→ 带该头一律 403，文案说清「未启用」；
- 不在白名单 → 403，文案说清「该来源未获授权」；
- 在白名单 → 201；
- 访客 id（`X-Visitor-Id`）随会话落库、操作者会话列表可见；超长截断（不因坏 id 挡死建会话）。

白名单通过 `app.state.settings` 就地改（app 建好即定格，测试里直接改属性最省；
每条用例自己恢复，避免串台）。
"""

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from suite_api.routes.customer import _visitor_id
from suite_api.services.rate_limit import CustomerRateLimits

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"
_ALLOWED = "https://shop.example.com"


@pytest.fixture(autouse=True)
def _fresh_quota_and_allowlist(api: ApiFixture):
    """每条用例：白名单恢复默认（空）+ 换一份新的顾客限流实例。

    限流实例本就可整体替换（services/rate_limit.CustomerRateLimits 的 docstring
    明说「测试可替换」）——本文件要多次建会话（每次用例都算一次 IP 建会话配额），
    不换就撞 5/60s。限流本身的契约由 test_customer_integration 钉，不在这里重复。
    """
    settings = api[0].app.state.settings
    original = settings.widget_allowed_origins
    api[0].app.state.customer_rate_limits = CustomerRateLimits()
    yield
    settings.widget_allowed_origins = original


def _set_allowlist(client: TestClient, value: str) -> None:
    client.app.state.settings.widget_allowed_origins = value


def _create(client: TestClient, **headers: str):
    return client.post("/api/customer/sessions", headers=headers)


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _visitor_in_db(url: str, session_id: int) -> str | None:
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT visitor_id FROM service_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
    return None if row is None else row[0]


def _host_origin_in_db(url: str, session_id: int) -> str | None:
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT host_origin FROM service_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
    return None if row is None else row[0]


# ---------- 独立访问：行为不变 ----------


def test_standalone_visit_is_unaffected(api: ApiFixture) -> None:
    """不带 widget 头 = 独立访问（/customer 直开）：白名单为空也照常签发，
    且没有访客 id（列表里为 None，不是空串）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    _set_allowlist(client, "")  # 未启用嵌入
    resp = _create(client)
    assert resp.status_code == 201
    assert resp.json()["token"]
    assert _visitor_in_db(url, resp.json()["session_id"]) is None


# ---------- 白名单闸 ----------


def test_widget_origin_rejected_when_not_enabled(api: ApiFixture) -> None:
    """白名单为空 = 未启用嵌入：带 X-Widget-Origin 一律 403（文案说清未启用）。"""
    client, _ = api
    _set_allowlist(client, "")
    resp = _create(client, **{"X-Widget-Origin": _ALLOWED})
    assert resp.status_code == 403
    assert "未启用嵌入" in resp.json()["detail"]


def test_widget_origin_rejected_when_not_allowlisted(api: ApiFixture) -> None:
    """不在白名单 → 403，且文案点名来源（这是嵌入的唯一闸）。"""
    client, _ = api
    _set_allowlist(client, _ALLOWED)
    resp = _create(client, **{"X-Widget-Origin": "https://evil.example.net"})
    assert resp.status_code == 403
    assert "未获授权嵌入" in resp.json()["detail"]


def test_standalone_stray_visitor_header_is_ignored(api: ApiFixture) -> None:
    """独立访问（无 X-Widget-Origin）自带 X-Visitor-Id 也不落库。

    访客 id 是「嵌入宿主那边的访客」，独立访问没有宿主——落库就是脏数据。
    （review P2：此前无条件收，会让独立访问也能塞访客 id。）
    """
    client, _ = api
    url = os.environ[_URL_ENV]
    created = _create(client, **{"X-Visitor-Id": "stray-visitor"})
    assert created.status_code == 201
    assert _visitor_in_db(url, created.json()["session_id"]) is None


def test_widget_origin_allowlisted_creates_session(api: ApiFixture) -> None:
    """在白名单 → 201（正常嵌入路径）。尾斜杠归一：白名单与请求都不带 / 也等价。"""
    client, _ = api
    _set_allowlist(client, f"{_ALLOWED}/")
    resp = _create(client, **{"X-Widget-Origin": _ALLOWED})
    assert resp.status_code == 201


# ---------- 访客 id ----------


def test_visitor_id_persisted_and_visible_to_operator(api: ApiFixture) -> None:
    """访客 id 落库并在操作者会话列表可见（否则是死字段）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    _set_allowlist(client, _ALLOWED)
    created = _create(
        client, **{"X-Widget-Origin": _ALLOWED, "X-Visitor-Id": "visitor-abc-123"}
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert _visitor_in_db(url, session_id) == "visitor-abc-123"

    _login(client)
    rows = client.get("/api/service/sessions").json()
    row = next(r for r in rows if r["id"] == session_id)
    assert row["visitor_id"] == "visitor-abc-123"


def test_visitor_id_too_long_is_truncated() -> None:
    """超长 id 截断到 64，不因坏 id 把建会话挡死（它是商家的不透明标识）。

    纯函数直测（不建会话）：截断逻辑在 `_visitor_id`，这里省一次建会话配额。
    """

    class _Req:
        headers = {"X-Visitor-Id": "v" * 100}

    assert _visitor_id(_Req()) == "v" * 64
    assert _visitor_id(type("R", (), {"headers": {}})()) is None
    assert _visitor_id(type("R", (), {"headers": {"X-Visitor-Id": "  "}})()) is None


# ---------- 宿主站点落库（第 54 刀） ----------


def test_host_origin_persisted_and_visible_to_operator(api: ApiFixture) -> None:
    """过闸的宿主来源落 `host_origin` 并在操作者会话列表可见（第 54 刀）。

    商家把 widget 挂在自己多个站点时，只靠访客 id 对账看不出「这条来自哪个站」。
    """
    client, _ = api
    url = os.environ[_URL_ENV]
    _set_allowlist(client, _ALLOWED)
    created = _create(client, **{"X-Widget-Origin": _ALLOWED})
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    # 落库值 = 过闸的**归一值**（小写、去尾斜杠）
    assert _host_origin_in_db(url, session_id) == _ALLOWED.rstrip("/").lower()

    _login(client)
    row = next(r for r in client.get("/api/service/sessions").json() if r["id"] == session_id)
    assert row["host_origin"] == _ALLOWED.rstrip("/").lower()
    # 审计刀 11 P0：**详情也要同源**（此前 SessionDetail 没传该字段 -> 恒回 None，
    # 与「列表与详情同源」的声称不符）
    detail = client.get(f"/api/service/sessions/{session_id}").json()
    assert detail["host_origin"] == _ALLOWED.rstrip("/").lower()


def test_host_origin_is_normalized(api: ApiFixture) -> None:
    """大小写与尾斜杠都按闸的归一值落库（闸怎么判，库里就怎么记）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    _set_allowlist(client, _ALLOWED)
    created = _create(client, **{"X-Widget-Origin": _ALLOWED.upper() + "/"})
    assert created.status_code == 201
    assert _host_origin_in_db(url, created.json()["session_id"]) == _ALLOWED.rstrip("/").lower()


def test_standalone_visit_has_no_host_origin(api: ApiFixture) -> None:
    """独立访问没有宿主：host_origin 为 NULL（不是空串），自带该头也不算（同访客 id 口径）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    _set_allowlist(client, "")
    created = _create(client)
    assert created.status_code == 201
    assert _host_origin_in_db(url, created.json()["session_id"]) is None

    # 独立访问自带 X-Widget-Origin：闸会拦（未启用嵌入）-> 403，不会落库
    _set_allowlist(client, _ALLOWED)
    spoofed = _create(client, **{"X-Widget-Origin": "http://evil.example"})
    assert spoofed.status_code == 403
