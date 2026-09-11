"""第 47 刀可观测单测：correlation id / 结构化日志 / 指标口径 / /metrics 闸。

不连库（metrics 与中间件都不碰 DB）：app 用不可达的 DATABASE_URL 建、不进
lifespan，与 test_mcp.py 的 gate_app 同形。全局 REGISTRY 跨用例累积，故断言
一律用**增量**（读值前先取基线），不比绝对值。
"""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from suite_api.main import create_app
from suite_api.observability import (
    CorrelationIdMiddleware,
    chat_requests_total,
    configure_logging,
    current_correlation_id,
    llm_tokens_total,
    safe_level,
    sanitize_request_id,
    ttft_seconds,
    usage_counts,
)
from suite_api.settings import Settings

_UNREACHABLE = "postgresql://unreachable:unreachable@localhost:1/none"


def _client(metrics_token: str = "") -> TestClient:
    settings = Settings(
        database_url=_UNREACHABLE, storage_root=Path("./data/objects"), metrics_token=metrics_token
    )
    return TestClient(create_app(settings))  # 不进 lifespan：不迁移不连库


def _sample(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


# ---------- correlation id ----------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("abc-123_DEF.456", "abc-123_DEF.456"),  # 合规：原样用
        ("short", None),  # 太短
        ("x" * 65, None),  # 太长
        ("bad id with spaces", None),  # 非法字符
        ("bad\nnewline", None),  # 控制字符（日志伪造面）
        ("", None),  # 空串
    ],
)
def test_sanitize_request_id(given: str, expected: str | None) -> None:
    got = sanitize_request_id(given)
    if expected is None:
        assert got != given
        assert len(got) == 32 and got.isalnum()  # 兜底生成的 uuid4 hex
    else:
        assert got == expected


def test_middleware_echoes_and_generates_correlation_id() -> None:
    client = _client()
    # 合规 id 原样回显
    ok = client.get("/health", headers={"X-Request-Id": "trace-abcdef12"})
    assert ok.headers["x-request-id"] == "trace-abcdef12"
    # 不合规 -> 换一个 32 位 hex（不原样回显客户端给的脏值）
    bad = client.get("/health", headers={"X-Request-Id": "nope"})
    assert bad.headers["x-request-id"] != "nope"
    assert len(bad.headers["x-request-id"]) == 32


def test_correlation_id_is_scoped_to_the_request() -> None:
    """中间件退出后 contextvar 复位（否则线程里下一个请求会继承上一个的 id）。"""
    client = _client()
    client.get("/health", headers={"X-Request-Id": "trace-abcdef12"})
    assert current_correlation_id() == ""


async def _drive_middleware(inner, request_id: str | None) -> list[dict]:
    """手工驱动纯 ASGI 中间件（不起 TestClient，便于在「请求内」打日志）。"""
    headers = [] if request_id is None else [(b"x-request-id", request_id.encode())]
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    app = CorrelationIdMiddleware(inner)
    await app({"type": "http", "headers": headers, "method": "GET", "path": "/x"}, receive, send)
    return sent


def test_correlation_id_lands_in_every_json_log_line(capsys: pytest.CaptureFixture) -> None:
    """验收 7 的机制钉子：中间件绑的 correlation_id 会进**请求内**每条 JSON 日志
    （stdlib 调用也一样），且与响应头同值。"""
    configure_logging("INFO")

    async def inner(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        logging.getLogger("suite_api.test.mw").warning("请求内的一条日志")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = asyncio.run(_drive_middleware(inner, "trace-abcdef12"))

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["correlation_id"] == "trace-abcdef12"
    start = next(m for m in sent if m["type"] == "http.response.start")
    header = dict(start["headers"])[b"x-request-id"].decode()
    assert header == payload["correlation_id"]  # 日志与响应头同源


# ---------- 结构化日志 ----------


def test_configure_logging_renders_stdlib_calls_as_json(capsys: pytest.CaptureFixture) -> None:
    """既有 getLogger(...).warning(...) 一行不改就是 JSON 行（含 level/event）。"""
    configure_logging("INFO")
    logging.getLogger("suite_api.test").warning("切片上传失败: %s", "boom")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "切片上传失败: boom"
    assert payload["level"] == "warning"
    assert payload["logger"] == "suite_api.test"
    assert "timestamp" in payload


def test_log_level_filters(capsys: pytest.CaptureFixture) -> None:
    configure_logging("WARNING")
    logger = logging.getLogger("suite_api.test.level")
    logger.info("不该出现")
    logger.warning("该出现")
    out = capsys.readouterr().out
    assert "不该出现" not in out
    assert json.loads(out.strip().splitlines()[-1])["event"] == "该出现"
    configure_logging("INFO")  # 还原，避免影响后续用例


# ---------- 指标口径（纯函数 / 计数增量） ----------


def test_usage_counts_accepts_dict_and_object_and_none() -> None:
    assert usage_counts({"prompt_tokens": 12, "completion_tokens": 7}) == (12, 7)
    # openai 的 CompletionUsage 是对象形状：getattr 分支
    assert usage_counts(NS(prompt_tokens=3, completion_tokens=4)) == (3, 4)
    assert usage_counts(NS(completion_tokens=4)) == (0, 4)
    assert usage_counts(None) is None
    assert usage_counts({}) is None  # 形状不对：不记（不猜 0）
    assert usage_counts({"prompt_tokens": 5}) == (5, 0)  # 缺一半：有的那个照记


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "abc", "completion_tokens": 1},
        {"prompt_tokens": {"nested": 1}, "completion_tokens": 1},
        {"prompt_tokens": ["x"], "completion_tokens": None},
        NS(prompt_tokens="abc", completion_tokens=1),
    ],
)
def test_usage_counts_never_raises_on_malformed_shape(usage: object) -> None:
    """形状不认识 -> None，**绝不抛**（调用点在 stream_chat 的 finally 里）。"""
    assert usage_counts(usage) is None


def test_safe_level_falls_back_to_info() -> None:
    """拼错的 LOG_LEVEL 不该让进程在启动期崩（root.setLevel 会抛 ValueError）。"""
    assert safe_level("DEBUG") == logging.DEBUG
    assert safe_level("warning") == logging.WARNING  # 大小写不敏感
    assert safe_level("verbose") == logging.INFO  # 认不出 -> INFO
    assert safe_level("") == logging.INFO


def test_record_llm_usage_increments_both_directions() -> None:
    labels_in = {"direction": "input", "model": "test-model"}
    labels_out = {"direction": "output", "model": "test-model"}
    before = _sample("llm_tokens_total", labels_in), _sample("llm_tokens_total", labels_out)
    from suite_api.observability import record_llm_usage

    record_llm_usage({"prompt_tokens": 30, "completion_tokens": 11}, model="test-model")
    assert _sample("llm_tokens_total", labels_in) == before[0] + 30
    assert _sample("llm_tokens_total", labels_out) == before[1] + 11


def test_record_llm_usage_skips_when_usage_missing() -> None:
    from suite_api.observability import record_llm_usage

    labels = {"direction": "input", "model": "test-model"}
    before = _sample("llm_tokens_total", labels)
    record_llm_usage(None, model="test-model")
    assert _sample("llm_tokens_total", labels) == before


class _Outcome:
    """run_ask 产出的最小替身：answer.kind / generated / fallback_reason。"""

    def __init__(self, kind: str, generated: bool, fallback_reason: str | None = None) -> None:
        self.answer = type("A", (), {"kind": kind})()
        self.generated = generated
        self.fallback_reason = fallback_reason


def test_record_chat_request_labels_channel_kind_generated() -> None:
    from suite_api.observability import record_chat_request

    labels = {"channel": "customer", "kind": "refusal", "generated": "false"}
    before = _sample("chat_requests_total", labels)
    record_chat_request(_Outcome("refusal", False), channel="customer")
    assert _sample("chat_requests_total", labels) == before + 1


def test_record_chat_request_counts_gate_fallbacks() -> None:
    """第 63 刀（审计刀 7 起记债）：闸回退必须可观测——fallback_reason 非空即计数。

    两种闸各一枚钉子：coverage（忠实度闸降级，ADR 0044 §二）与 no_coverage
    （模型自述证据未覆盖按拒答收口，第 58 刀）。None 不计——普通厂商失败降级
    的 fallback_reason 就是 None，那不是闸在回退，混进来污染闸回退率。
    """
    from suite_api.observability import record_chat_request

    for channel, reason in (("customer", "no_coverage"), ("operator", "coverage")):
        labels = {"channel": channel, "reason": reason}
        before = _sample("chat_fallbacks_total", labels)
        record_chat_request(_Outcome("refusal", False, fallback_reason=reason), channel=channel)
        assert _sample("chat_fallbacks_total", labels) == before + 1, (channel, reason)

    # 普通路径（无闸回退）只进 chat_requests_total，不进闸回退计数。
    # **按指标族全样本求和**才密闭：枚举三个已知 reason 时，「None 被记成
    # 陌生标签值」的坏实现照样通过；collect() 拿到的是计数器当前全部样本。
    from suite_api.observability import chat_fallbacks_total as _counter

    def _fallback_family_value() -> float:
        return sum(sample.value for metric in _counter.collect() for sample in metric.samples)

    plain_before = _fallback_family_value()
    record_chat_request(_Outcome("answer", True), channel="customer")
    assert _fallback_family_value() == plain_before
    # 分母共增：带闸回退的结局也必须把 chat_requests_total 记上一次——
    # 闸回退率的分母是它，函数里两处计数谁也不能吃掉谁
    gate_requests_before = _sample(
        "chat_requests_total", {"channel": "operator", "kind": "refusal", "generated": "false"}
    )
    record_chat_request(_Outcome("refusal", False, fallback_reason="no_coverage"), channel="operator")
    assert (
        _sample("chat_requests_total", {"channel": "operator", "kind": "refusal", "generated": "false"})
        == gate_requests_before + 1
    )


def test_unknown_fallback_reason_falls_to_bounded_other() -> None:
    """基数纪律：引擎将来加了新 reason 而指标没跟上，落 other 而不是进标签。"""
    from suite_api.observability import record_chat_request

    labels = {"channel": "operator", "reason": "other"}
    before = _sample("chat_fallbacks_total", labels)
    record_chat_request(_Outcome("refusal", False, fallback_reason="brand_new"), channel="operator")
    assert _sample("chat_fallbacks_total", labels) == before + 1


def test_custom_metrics_registered_with_bounded_labels() -> None:
    """标签集合是契约的一部分：多一个 id 类标签就是基数风险。"""
    assert set(chat_requests_total._labelnames) == {"channel", "kind", "generated"}
    assert set(llm_tokens_total._labelnames) == {"direction", "model"}
    assert set(ttft_seconds._labelnames) == {"model"}
    from suite_api.observability import chat_fallbacks_total

    assert set(chat_fallbacks_total._labelnames) == {"channel", "reason"}


# ---------- /metrics 闸 ----------


def test_metrics_endpoint_closed_without_token_configured() -> None:
    """默认栈（METRICS_TOKEN 未配置）= 端点关闭：401，不裸奔。"""
    client = _client(metrics_token="")
    resp = client.get("/metrics")
    assert resp.status_code == 401
    assert "未启用" in resp.json()["detail"]


def test_metrics_endpoint_rejects_wrong_token() -> None:
    client = _client(metrics_token="s3cret-metrics")
    assert client.get("/metrics").status_code == 401
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    # 非 Bearer 方案也拒（不认 cookie/其他 scheme）
    assert (
        client.get("/metrics", headers={"Authorization": "s3cret-metrics"}).status_code == 401
    )


def test_metrics_endpoint_serves_red_and_custom_metrics_with_token() -> None:
    client = _client(metrics_token="s3cret-metrics")
    client.get("/api/auth/me")  # 真业务 handler（未登录 401）——产生一条 RED 样本
    resp = client.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics"})
    assert resp.status_code == 200
    body = resp.text
    # RED 真的是「有样本」而不只是「有 TYPE 行」（/health 被排除，拿它当样本是自欺）
    assert 'handler="/api/auth/me"' in body
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "chat_requests_total",
        "ttft_seconds",
        "llm_tokens_total",
    ):
        assert f"# TYPE {name}" in body, name


def test_health_and_metrics_are_excluded_from_http_metrics() -> None:
    """自抓自我放大 + 探针噪声：两个 handler 都不进 HTTP 指标。"""
    client = _client(metrics_token="s3cret-metrics")
    client.get("/health")
    client.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics"})
    metrics = client.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics"}).text
    assert 'handler="/health"' not in metrics
    assert 'handler="/metrics"' not in metrics


def test_every_app_instance_records_red_metrics() -> None:
    """同进程建多个 app（集成测试/多实例）时，**第二个也要记 HTTP 指标**。

    这条钉的是 per-app registry：用默认全局 registry 时，instrumentator 遇到
    「同名指标已存在」会捕获 duplicate ValueError 后返回 None —— instrumentations
    变空、静默不再记任何 HTTP 指标（本仓主路径不明显，但同进程多 app 必踩）。
    """
    first, second = _client(metrics_token="tok"), _client(metrics_token="tok")
    for client in (first, second):
        client.get("/api/auth/me")
        body = client.get("/metrics", headers={"Authorization": "Bearer tok"}).text
        assert 'handler="/api/auth/me"' in body  # 两个 app 各自的 registry 都有样本


# ---------- 审计刀 9：切片/CSAT 也进观测面 ----------


def test_clip_cut_and_csat_metrics_registered() -> None:
    """46/48 的产品动作要有自有指标（此前只有 HTTP RED 能看出「有人调过端点」）。"""
    client = _client(metrics_token="tok")
    body = client.get("/metrics", headers={"Authorization": "Bearer tok"}).text
    for name in ("clip_cuts_total", "csat_ratings_total"):
        assert f"# TYPE {name}" in body, name
    # 第 63 刀：闸回退指标也在 /metrics 上（否则计数了也抓不到）
    assert "# TYPE chat_fallbacks_total" in body


def test_clip_cut_and_csat_counters_increment() -> None:
    from suite_api.observability import record_clip_cut, record_csat_rating

    labels = {"result": "failed"}
    before = _sample("clip_cuts_total", labels)
    record_clip_cut(result="failed")
    assert _sample("clip_cuts_total", labels) == before + 1

    csat_labels = {"score": "3"}
    csat_before = _sample("csat_ratings_total", csat_labels)
    record_csat_rating(3)
    assert _sample("csat_ratings_total", csat_labels) == csat_before + 1


def test_upload_recording_is_sync_route() -> None:
    """上传源录像必须是**同步**路由（FastAPI 丢线程池）：路由若是 async，200MB 的
    SHA256 与整块落盘会跑在事件循环上，掐住同一循环里的顾客 SSE 流（审计刀 9 P1）。"""
    import inspect

    from suite_api.routes import clips as clips_routes

    assert not inspect.iscoroutinefunction(clips_routes.upload_recording)
