"""最小可观测（第 47 刀）：结构化日志 + 请求关联 id + Prometheus 指标。

三块都在进程内，不引采集栈（无 OTel/Agent/远端写）。口径与取舍见
``docs/progress/observability-intake.md``。

- **correlation id**：纯 ASGI 中间件（**不用** ``BaseHTTPMiddleware``——它把响应
  体再包一层 anyio 流，SSE 长连接在它下面有已知破坏面，而本仓顾客主路径就是
  SSE）。接受客户端的 ``X-Request-Id``（长度 8–64、字符集 ``[A-Za-z0-9._-]``
  合规才用，否则丢弃重生成），写 contextvar 并回显到响应头。
- **日志**：structlog JSON 渲染，经 ``ProcessorFormatter`` 接管 stdlib——既有
  ``logging.getLogger(__name__).warning(...)`` 调用**一行不改**就变 JSON 行，且
  自动带 ``correlation_id``。留 ``trace_id`` 字段口子（本刀不接 OTel）。
- **指标**：instrumentator 的 HTTP RED（在 main 装配）+ 本模块的六个自定义。
  **标签值一律有限集合**（channel/kind/generated/direction/model/result/score/reason/from/to）——绝不把
  session_id / asset_id / 问题文本打进标签，那是指标基数爆炸的经典自杀方式。
"""

from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import structlog
from prometheus_client import CollectorRegistry, Counter, Histogram
from starlette.datastructures import MutableHeaders

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_ID_HEADER = "X-Request-Id"

# 客户端自报 id 的收口：8–64 位、只认 [A-Za-z0-9._-]（日志字段与响应头都进，
# 别让换行/控制字符/超长串进来——日志伪造与头注入都从这来）
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
# 请求进入时刻（perf_counter）：TTFT 的起点。中间件设，引擎读——同一请求任务，
# contextvar 天然可见；直调 run_ask 的测试拿不到起点，于是不记 TTFT（宁缺毋假）。
_request_start: ContextVar[float | None] = ContextVar("request_start", default=None)


def sanitize_request_id(value: str | None) -> str:
    """合规的客户端 id 原样用，否则生成一个（纯函数，便于单测）。"""
    if value is not None and _REQUEST_ID_RE.match(value):
        return value
    return uuid.uuid4().hex


def current_correlation_id() -> str:
    return _correlation_id.get()


def request_elapsed_seconds() -> float | None:
    """距请求进入中间件的秒数；不在请求上下文（直调引擎）时 None。"""
    start = _request_start.get()
    if start is None:
        return None
    return time.perf_counter() - start


class CorrelationIdMiddleware:
    """纯 ASGI：定关联 id（contextvar + 响应头），不碰 body、不缓冲。

    只做两件事——进请求时读/生成 id 并写 contextvar（日志与业务代码自动继承），
    出响应时在 ``http.response.start`` 里塞回显头。流式响应照常逐块透传。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming: str | None = None
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        correlation_id = sanitize_request_id(incoming)

        id_token = _correlation_id.set(correlation_id)
        start_token = _request_start.set(time.perf_counter())
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.clear_contextvars()
            _request_start.reset(start_token)
            _correlation_id.reset(id_token)


# ---------- 指标（六个自定义；标签值必须有限） ----------

chat_requests_total = Counter(
    "chat_requests_total",
    "发问次数（通道 / 结局 / 是否厂商生成）。generated=false 含模板回退与工具/目录回答。",
    ["channel", "kind", "generated"],
)

ttft_seconds = Histogram(
    "ttft_seconds",
    "请求进入 → 厂商首个生成增量的秒数（含检索/提议/工具步；只有生成路径记录）。",
    ["model"],
    # 上限对齐 llm 的 20s 超时：超时即降级，不会有更长的成功样本
    buckets=(0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0),
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "厂商 usage 的 token 数（厂商没给 usage 就不计——不用字数估算冒充 token）。",
    ["direction", "model"],
)

_USAGE_DIRECTIONS = ("input", "output")

# 切片拣选（审计刀 9）：result 三值有界——ok=真切出 mp4、failed=ffmpeg/时间码失败、
# legacy=无源录像走时间码文本旧路径。切段是外部子进程，失败率必须看得见。
clip_cuts_total = Counter(
    "clip_cuts_total",
    "切片拣选结果（ok=真切 mp4 / failed=切段失败 / legacy=无源录像走旧文本路径）。",
    ["result"],
)

# CSAT（审计刀 9 起；第 71 刀口径订正）：**事件计数**——每次提交（含改评）各计一次，
# 与仪表分布（读行数据的最新值）**不同源**：改评 5→1 会让本指标 {1} +1，而仪表
# 只按该行最新值计一次。看板/告警别把本指标当分布用。
csat_ratings_total = Counter(
    "csat_ratings_total",
    "评分提交事件次数（含改评，按提交分档）——事件口径，非仪表分布口径。",
    ["score"],
)

# 闸回退（第 63 刀，审计刀 7 起记债、审计刀 12 建议 3）：reason 与引擎
# `AskOutcome.fallback_reason` 同源——coverage=忠实度闸把生成降级为证据模板
# （ADR 0044 §二）、no_coverage=模型自述证据未覆盖按拒答收口（第 58 刀）、
# oov=实体存在性闸按拒答收口（第 82 刀：问句点名的实体不在库，不引他品证据）。
# 普通厂商失败降级为 None（不计数）——那不是闸在回退，是网关/厂商问题，
# 混进来会污染「闸回退率」。other 是防御位：引擎将来加了新 reason 而
# 这里没跟上，宁可落 other 也不让陌生值进标签（基数纪律）。
# 会话生命周期迁移（第 84 刀，审计刀 16 B 轴记债）：只记**有界状态值**
# （new/active/ended/registered 的组合上限 12，实际 4 条路径）——绝不带
# session_id（47 刀基数纪律）。没有它「结束率/结束→回流转化」只能靠日志数行。
service_session_transitions_total = Counter(
    "service_session_transitions_total",
    "会话状态迁移次数（new->active 建会话 / active->ended 顾客结束 / active->registered 操作者回流 / ended->registered 结束后回流）。",
    ["from", "to"],
)


def record_session_transition(from_status: str, to_status: str) -> None:
    """记一次会话状态迁移（调用方保证两端值在有界集合内）。

    标签名保留 Prometheus 惯例的 from/to（`from` 是 Python 保留字，只能展开传）。
    """
    service_session_transitions_total.labels(**{"from": from_status, "to": to_status}).inc()


_FALLBACK_REASONS = ("coverage", "no_coverage", "oov")
chat_fallbacks_total = Counter(
    "chat_fallbacks_total",
    "闸回退次数（coverage=忠实度闸降级 / no_coverage=证据未覆盖按拒答收口 / oov=实体不在库按拒答收口 / other=未知原因防御位）。",
    ["channel", "reason"],
)


def record_chat_request(outcome: Any, *, channel: str) -> None:
    """发问结束记一次（channel ∈ customer/operator；kind 取 ComposedAnswer.kind）。

    ``generated`` 区分「厂商真生成」与「模板/工具/目录回答」——顺带把模板回退率
    变成可观测值（此前只能靠日志数行）。``fallback_reason`` 非空时另记
    ``chat_fallbacks_total``（闸回退率；None 不计——见该指标的口径注释）。
    """
    kind = getattr(getattr(outcome, "answer", None), "kind", None) or "unknown"
    generated = bool(getattr(outcome, "generated", False))
    chat_requests_total.labels(
        channel=channel, kind=str(kind), generated="true" if generated else "false"
    ).inc()
    reason = getattr(outcome, "fallback_reason", None)
    if reason is not None:
        bounded = reason if reason in _FALLBACK_REASONS else "other"
        chat_fallbacks_total.labels(channel=channel, reason=bounded).inc()


def usage_counts(usage: Any) -> tuple[int, int] | None:
    """从厂商 usage 取 (输入, 输出) token；取不到返回 None（对象或 dict 两种形状）。

    openai 的 CompletionUsage 与网关的 dict 都吃；缺字段按 0 处理。**形状不认识
    一律返回 None，绝不抛**：调用点在 ``stream_chat`` 的 ``finally`` 里——抛出会
    顶掉原本的异常（把「超时→模板降级」变成 500），观测面不许反过来打断主链路。
    """
    if usage is None:
        return None
    try:
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
        else:
            prompt = getattr(usage, "prompt_tokens", None)
            completion = getattr(usage, "completion_tokens", None)
        if prompt is None and completion is None:
            return None
        return int(prompt or 0), int(completion or 0)
    except (TypeError, ValueError):
        return None  # 厂商换了形状（字符串/嵌套对象）：不记，也不炸主链路


def record_llm_usage(usage: Any, *, model: str) -> None:
    """记一次厂商 usage（缺失即不记；一次调用只该调一次，去重在调用点做）。"""
    counts = usage_counts(usage)
    if counts is None:
        return
    prompt, completion = counts
    for direction, value in zip(_USAGE_DIRECTIONS, (prompt, completion), strict=True):
        if value > 0:
            llm_tokens_total.labels(direction=direction, model=model).inc(value)


def observe_first_token(model: str) -> None:
    """记一次首字延迟（从请求进入算起；不在请求上下文时跳过，见 _request_start）。"""
    elapsed = request_elapsed_seconds()
    if elapsed is None:
        return
    ttft_seconds.labels(model=model).observe(elapsed)


# ---------- 结构化日志 ----------


def record_clip_cut(*, result: str) -> None:
    """拣选结果记一次（result ∈ ok/failed/legacy，调用方给有界值）。"""
    clip_cuts_total.labels(result=result).inc()


def record_csat_rating(score: int) -> None:
    """会话评分记一次（1–5；越界分不该出现，调用点在应用层 422 之后）。"""
    csat_ratings_total.labels(score=str(score)).inc()


def build_metrics_registry() -> CollectorRegistry:
    """每个 app 一份 metrics registry（RED 与六个自定义都在里面）。

    为什么不用默认全局 registry：instrumentator 在「同名指标已存在」时会**静默
    放弃全部默认 instrumentation**（``metrics.latency`` 捕获 duplicate ValueError
    后返回 None，instrumentations 变空）——同进程建第二个 app（集成测试、
    多实例/多 worker 同进程）就再也记不到 HTTP 指标，且不报错。每 app 一份
    registry 让这条路不存在；自定义 Collector 对象同时挂默认 registry 与
    各 app 的 registry（同一对象多 registry 合法，值共享），所以非请求上下文
    里（llm/engine）照常计数。
    """
    registry = CollectorRegistry()
    for collector in (
        chat_requests_total,
        ttft_seconds,
        llm_tokens_total,
        clip_cuts_total,
        csat_ratings_total,
        chat_fallbacks_total,
        service_session_transitions_total,
    ):
        registry.register(collector)
    return registry


def safe_level(level: str) -> int:
    """级别名 -> int；不认识（含拼错/空串）回落 INFO——**启动期不因为这个崩**。"""
    value = logging.getLevelNamesMapping().get(str(level).strip().upper())
    return value if isinstance(value, int) else logging.INFO


def configure_logging(level: str = "INFO") -> None:
    """structlog JSON 接管 stdlib 日志（幂等：每次替换 root handler，不叠加）。

    - 既有的 ``getLogger(__name__).warning(...)`` 调用零改动即变 JSON 行
      （``foreign_pre_chain`` 给 stdlib 记录补 level/logger/timestamp）。
    - uvicorn 自带 handler 会绕过 root（``propagate=False``）→ 清掉并放开传播，
      否则同一进程里一半 JSON 一半裸文本。
    - ``structlog.contextvars.merge_contextvars`` 让每条日志自动带 correlation_id；
      将来接 OTel 只需在 ``shared_processors`` 里加一个 trace_id 处理器（字段位
      在这里，不在各调用点）。

    **进程级副作用**（本仓是单体应用，接受）：替换 root handler、清 uvicorn
    三个 logger 的 handler；作为库嵌进别人进程会改宿主的日志格式。
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(safe_level(level))
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
