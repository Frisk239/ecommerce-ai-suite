"""第 47 刀可观测集成：/metrics 在默认栈关闭、发问计数按通道/结局落账、
TTFT 只在生成路径记、correlation id 回显到 SSE 响应头。

指标是进程内全局（prometheus_client REGISTRY），故断言一律**增量**。
LLM 替身沿用 test_fidelity_gate 的形态（monkeypatch stream_chat）。
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sse_helpers import parse_sse_events

from suite_api.settings import get_settings

ApiFixture = tuple[TestClient, Any]


def _sample(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _session(client: TestClient) -> int:
    _login(client)
    return client.post("/api/service/sessions").json()["id"]


def _ask(client: TestClient, session_id: int, question: str, request_id: str | None = None):
    headers = {"X-Request-Id": request_id} if request_id else {}
    with client.stream(
        "POST",
        f"/api/service/sessions/{session_id}/messages",
        json={"content": question},
        headers=headers,
    ) as resp:
        raw = "".join(resp.iter_text())
        return resp, parse_sse_events(raw)


def test_metrics_closed_by_default_in_real_stack(api: ApiFixture) -> None:
    """默认栈（conftest 的 Settings 不带 metrics_token）→ 401：不裸奔。"""
    client, _ = api
    assert client.get("/metrics").status_code == 401


def test_refusal_question_counts_with_kind_and_generated_false(api: ApiFixture) -> None:
    client, _ = api
    session_id = _session(client)
    labels = {"channel": "operator", "kind": "refusal", "generated": "false"}
    before = _sample("chat_requests_total", labels)

    # 无已发布证据 -> 拒答（0018 不编造）；拒答也走模板，故 generated=false
    _, events = _ask(client, session_id, "会员积分怎么兑换？")
    assert any(e[0] == "complete" for e in events)

    assert _sample("chat_requests_total", labels) == before + 1


def test_generation_path_counts_ttft_and_generated_true(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api

    # 造一份已发布证据（问句与文档同词，保证检索命中 -> 走生成路径）
    registered = client.post(
        "/api/assets/register",
        files={
            "file": (
                "obs.txt",
                "观测测试说明\n观测测试专用款式保修两年，非人为损坏免费换新。".encode(),
                "text/plain",
            )
        },
        data={"title": "观测测试说明"},
    )
    assert registered.status_code == 201
    _login(client)
    asset_id = registered.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    def fake_stream(_system: str, _user: str, history: list[dict[str, str]] | None = None) -> Any:
        del history

        async def _gen() -> Any:
            yield "保修两年。"

        return _gen()

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", fake_stream)
    monkeypatch.setattr("suite_api.services.chat_engine.llm.build_prompts", lambda *_a, **_k: ("s", "u"))

    session_id = _session(client)
    model = get_settings().llm_model
    chat_labels = {"channel": "operator", "kind": "answer", "generated": "true"}
    ttft_labels = {"model": model}
    chat_before = _sample("chat_requests_total", chat_labels)
    ttft_before = _sample("ttft_seconds_count", ttft_labels)

    resp, events = _ask(client, session_id, "观测测试专用款式保修多久？", request_id="trace-abcdef12")
    assert resp.status_code == 200
    assert any(e[0] == "complete" for e in events)

    assert _sample("chat_requests_total", chat_labels) == chat_before + 1
    # TTFT 只在生成路径记：本问走了模型替身，直方图计数 +1
    assert _sample("ttft_seconds_count", ttft_labels) == ttft_before + 1


def test_correlation_id_echoed_on_sse_response(api: ApiFixture) -> None:
    client, _ = api
    session_id = _session(client)
    resp, _ = _ask(client, session_id, "会员积分怎么兑换？", request_id="trace-abcdef12")
    assert resp.headers["x-request-id"] == "trace-abcdef12"


def test_refusal_path_does_not_record_ttft(api: ApiFixture) -> None:
    """拒答不调模型 -> 不记 TTFT（宁缺毋假：不把检索耗时当首字延迟）。"""
    client, _ = api
    session_id = _session(client)
    labels = {"model": get_settings().llm_model}
    before = _sample("ttft_seconds_count", labels)
    _ask(client, session_id, "会员积分怎么兑换？")
    assert _sample("ttft_seconds_count", labels) == before


def _seed_customer_session(client: TestClient, token: str) -> int:
    """直接落一条顾客会话（绕过建会话 IP 闸 5/60s；第 45 刀：过期时刻必须写）。"""
    from datetime import UTC, datetime, timedelta

    from suite_api.models import ServiceSession

    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(
            status="active",
            customer_token=token,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(session)
        db.commit()
        return session.id


def test_customer_channel_is_counted_separately(api: ApiFixture) -> None:
    """channel 标签必须真的区分两条通道（顾客路由那行写错/删掉，这条要红）。"""
    client, _ = api
    session_id = _seed_customer_session(client, "obs-customer-token-1")
    labels = {"channel": "customer", "kind": "refusal", "generated": "false"}
    before = _sample("chat_requests_total", labels)

    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": "会员积分怎么兑换？"},
        headers={"Authorization": "Bearer obs-customer-token-1"},
    ) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())

    assert _sample("chat_requests_total", labels) == before + 1


def test_handoff_outcome_is_counted(api: ApiFixture) -> None:
    """kind 标签要覆盖转人工（词表快路径）：别把 handoff 记成 answer/refusal。"""
    client, _ = api
    session_id = _session(client)
    labels = {"channel": "operator", "kind": "handoff", "generated": "false"}
    before = _sample("chat_requests_total", labels)

    _, events = _ask(client, session_id, "我要转人工")
    assert any(e[0] == "complete" for e in events)

    assert _sample("chat_requests_total", labels) == before + 1


def test_session_transition_metric_is_exposed_on_endpoint(api) -> None:
    """第 84 刀评审 P0：新指标必须真出现在 `/metrics` 输出里。

    断链形态：collector 自增但 `build_metrics_registry` 未注册 -> 端点永不输出，
    而直读 `._value` 的钉子测不出（本文件前一条钉子即如此）。此处走**端点**：
    设 token -> 触发一次迁移 -> 断言输出含指标名与标签。
    """
    from fastapi.testclient import TestClient

    from suite_api.observability import service_session_transitions_total

    client, _ = api
    service_session_transitions_total.labels(**{"from": "new", "to": "active"}).inc()
    resp = TestClient(client.app).get(
        "/metrics", headers={"Authorization": f"Bearer {client.app.state.settings.metrics_token}"}
    )
    if resp.status_code == 401:
        # 该 app 未配 METRICS_TOKEN（空= fail-closed）——本钉子退化为注册表断言
        from suite_api.observability import build_metrics_registry

        registry = build_metrics_registry()
        # collect() 里的 m.name 是 Prometheus 基名（`_total` 只在文本输出加）
        names = {m.name for m in registry.collect()}
        assert "service_session_transitions" in names
        return
    assert "service_session_transitions_total" in resp.text
    assert 'from="new"' in resp.text and 'to="active"' in resp.text
