"""操作者仪表集成测试（第 43 刀；真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

覆盖：未登录 401；7 日零填充形状（恰好 7 条、UTC 升序、含今天）；造数据后逐项
对计数（拒答 -> refusals/gaps_opened/daily 当日 +1，且拒答不混进 handoffs）；
无带引用回答时 citation_rate 为 None（不除零）；被踩资产进 feedback_assets。
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.routes.customer import triage_asset_ids

ApiFixture = tuple[TestClient, Path]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _overview(client: TestClient) -> dict[str, Any]:
    resp = client.get("/api/stats/overview")
    assert resp.status_code == 200
    return resp.json()


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _customer_ask(
    client: TestClient, session_id: int, token: str, question: str
) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": question},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _publish(client: TestClient, content: bytes, title: str) -> int:
    """登记 + 发布一份无商品资产（无规格必填，发布无字段闸门）。"""
    existing = [a for a in client.get("/api/assets").json() if a.get("title") == title]
    if existing:
        return existing[0]["id"]
    resp = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", content, "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# ---------- 鉴权 ----------


def test_stats_overview_requires_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.get("/api/stats/overview").status_code == 401


# ---------- 形状：7 日零填充、UTC 升序、含今天 ----------


def test_stats_overview_shape_zero_filled(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    body = _overview(client)

    assert body["window_days"] == 7
    days = body["daily"]
    assert len(days) == 7
    dates = [d["date"] for d in days]
    assert dates == sorted(dates)  # 升序
    assert _today() in dates  # 含今天（UTC 天）
    # 连续自然日：相邻差恰好一天
    parsed = [datetime.fromisoformat(d).date() for d in dates]
    assert all((parsed[i + 1] - parsed[i]).days == 1 for i in range(6))
    for day in days:
        assert set(day) == {"date", "sessions", "refusals", "thumbs_down"}
        assert all(isinstance(day[k], int) for k in ("sessions", "refusals", "thumbs_down"))

    # 引用覆盖率：分子为 0 必须是 None（不除零），否则恒等于 分子/分母。
    # 写成等价断言（而非「若分子为 0 则…」）——两种情形都要被钉住，不依赖用例顺序。
    assert (body["citation_rate_last_7d"] is None) == (
        body["answers_with_citations_last_7d"] == 0
    )
    if body["answers_last_7d"] > 0:
        assert body["citation_rate_last_7d"] == (
            body["answers_with_citations_last_7d"] / body["answers_last_7d"]
        )


# ---------- 计数：拒答与转人工分开、缺口新开、当日归桶 ----------


def test_refusal_counts_and_daily_bucket(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    before = _overview(client)

    sid = client.post("/api/service/sessions").json()["id"]
    # 独有问法：保证归一化后不命中既有 open 缺口（否则 gaps_opened 不增）
    events = _ask(client, sid, "第四十三刀仪表专用问法：海鸥牌台灯的灯罩怎么拆装？")
    complete = events[-1][1]
    assert complete["kind"] == "refusal"

    after = _overview(client)
    assert after["refusals_last_7d"] == before["refusals_last_7d"] + 1
    assert after["gaps_opened_last_7d"] == before["gaps_opened_last_7d"] + 1
    # 拒答不混进转人工（Owner 裁决 1：两个数分开报）
    assert after["handoffs_last_7d"] == before["handoffs_last_7d"]

    today = next(d for d in after["daily"] if d["date"] == _today())
    before_today = next(d for d in before["daily"] if d["date"] == _today())
    assert today["refusals"] == before_today["refusals"] + 1
    assert after["badges"]["open_gaps"] == before["badges"]["open_gaps"] + 1


# ---------- 反馈汇总：被踩资产 top 3 与引用覆盖率 ----------


def test_thumbs_down_asset_summary(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id = _publish(
        client,
        "海鸥台灯产品说明\n灯罩拆装：逆时针旋转灯罩即可取下。".encode(),
        "海鸥台灯说明（第 43 刀仪表）",
    )
    before = _overview(client)

    # 顾客通道发问 -> 有引用回答 -> 「没有帮助」
    client.cookies.clear()
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]
    events = _customer_ask(client, sid, token, "海鸥台灯的灯罩怎么拆？")
    complete = events[-1][1]
    assert complete["citations"] != []
    cited_asset_ids = {c["asset_id"] for c in complete["citations"]}
    assert asset_id in cited_asset_ids

    feedback = client.post(
        f"/api/customer/sessions/{sid}/messages/{complete['message_id']}/feedback",
        json={"helpful": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert feedback.status_code == 200

    _login(client)
    after = _overview(client)
    assert after["thumbs_down_last_7d"] == before["thumbs_down_last_7d"] + 1
    assert after["answers_last_7d"] == before["answers_last_7d"] + 1
    assert after["citation_rate_last_7d"] == (
        after["answers_with_citations_last_7d"] / after["answers_last_7d"]
    )
    today = next(d for d in after["daily"] if d["date"] == _today())
    before_today = next(d for d in before["daily"] if d["date"] == _today())
    assert today["thumbs_down"] == before_today["thumbs_down"] + 1

    hit = next((a for a in after["feedback_assets"] if a["asset_id"] == asset_id), None)
    assert hit is not None
    assert hit["count"] >= 1
    assert hit["title"] == "海鸥台灯说明（第 43 刀仪表）"
    assert len(after["feedback_assets"]) <= 3  # 行摘要 top 3，不是全量


# ---------- 角标：形状 + 工单 pending 随真实工单增减 ----------


def test_badges_track_open_ticket_and_keep_shape(api: ApiFixture) -> None:
    """角标三个数（裁决 5 的前置）：形状恒为三个非负整数；`open_tickets` 随真实
    工单增减（走第 42 刀闭环的真实路径）。`pending_qc` 在此只做形状断言——造一条
    pending_qc 要走素材生成链路、依赖模型底座，不适合当统计口径的验证手段。
    """
    client, _ = api
    _login(client)
    before = _overview(client)
    badges = before["badges"]
    assert set(badges) == {"open_gaps", "pending_qc", "open_tickets"}
    assert all(isinstance(v, int) and v >= 0 for v in badges.values())

    # 顾客显式转人工 -> 该会话建 pending 工单 -> open_tickets +1、handoffs +1
    client.cookies.clear()
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]
    events = _customer_ask(client, sid, token, "我要转人工")
    assert events[-1][1]["kind"] == "handoff"

    _login(client)
    after = _overview(client)
    assert after["badges"]["open_tickets"] == before["badges"]["open_tickets"] + 1
    assert after["handoffs_last_7d"] == before["handoffs_last_7d"] + 1


# ---------- 共享解析加固（第 43 刀：stats 读路径复用后坏行不许 500） ----------


def test_triage_asset_ids_tolerates_missing_and_null() -> None:
    assert triage_asset_ids(
        [
            {"asset_id": 3},
            {"asset_id": None},  # 旧实现 int(None) -> TypeError
            {},
            "not-a-dict",
            {"asset_id": 1},
            {"asset_id": 3},
            {"asset_id": True},  # bool 是 int 子类，排除（同 lineage 口径）
        ]
    ) == [1, 3]
