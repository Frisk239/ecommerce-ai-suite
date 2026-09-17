"""轻量并发压测（第 103 刀）：stdlib 线程 + httpx，不引 locust。

## 为什么自制

本仓压测诉求只有三件事：固定并发打一路径 N 秒、分位数延迟、429 计数——
locust 拖 web UI 与消息队列，overkill。std 库 ``threading`` + 已有依赖
``httpx``（同步 Client）即可：每线程一个独立 Client（连接池隔离，无线程
共享争用），主线程掐时长窗。

## 路径（--path）

- ``health``：GET /health——纯框架+DB 连通基线（无鉴权、无业务）。
- ``refusal``：控制台预览通道问固定无证据句（「这个产品支持意念控制吗」）
  ——结局拒答（kind=refusal，模型自述证据未覆盖按拒收口）。注意引擎真实
  行为：这句要过**提议步+生成步两次公网 LLM 调用**才落到拒答模板，延迟
  以 LLM 网关往返为主（快路径问句做不到「无证据」，词表全被快路径截走）。
  SSE 完整读。
- ``tool``：问「显示器有货吗」——库存类目聚合工具路径（快路径分派，**零
  LLM、零检索**，实测 <0.1s）。SSE 完整读。
- ``rag``：问「Xperia Ear Duo 什么时候上市的」——检索命中 + 厂商生成路径
  （**LLM 在线才有效**），TTFT=首个 delta 事件到达。SSE 完整读。
- ``ratelimit``：顾客通道建会话并发一波（不走时长窗，每线程一击）——验证
  IP 建会话限流闸（5/60s）真触发：429 + Retry-After。

引擎路径（refusal/tool/rag）走**控制台预览通道**（operator 登录，无顾客
三闸干扰——限流语义由 ratelimit 场景单独验，压引擎时不能把闸混进来当错误）。

## 口径（报告对照时必读）

- 延迟=请求发起 → SSE 流读完（完整时长）。服务端是「先收全再流」
  （chat_engine docstring：LLM 全文落库后才开始吐 SSE），故客户端 TTFT
  （首个 delta 到达）≈ 完整时长，**大于**埋点 ``ttft_seconds``（服务端在
  LLM 流首块处记，chat_engine.py observe_first_token）——两者口径差异在
  docs/research/perf-report.md 如实声明。
- SSE 事件序：thinking -> [tool] -> delta* -> complete；complete 到达
  才算成功（2xx 但流中断=错误）。

## 用法

    uv run python scripts/perf/loadtest.py --path health --users 10 --duration 60
    uv run python scripts/perf/loadtest.py --path rag --users 30 --duration 60
    uv run python scripts/perf/loadtest.py --path ratelimit --users 40 --duration 5

输出：人读摘要 + 一行 Markdown 表格行（数字机械粘贴进报告）。
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass, field

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"

# 三条固定问句（冒烟验证过路由：refusal=两次 LLM 后拒收口 / tool=库存类目
# 聚合快路径零 LLM / rag=检索命中+厂商生成——见 docs/research/perf-report.md）
QUESTION_REFUSAL = "这个产品支持意念控制吗"
QUESTION_TOOL = "显示器有货吗"
QUESTION_RAG = "Xperia Ear Duo 什么时候上市的"

SESSION_COOKIE = "suite_session"

# 单请求超时：LLM 20s 服务端上限 + 检索/落库/SSE 传输余量
REQUEST_TIMEOUT = 45.0

# ratelimit 场景栅栏等待上限（103 刀债）：防「单线程先死、其余线程裸 wait」
# 把进程钉死——量级与单请求超时对齐；单测把它改小来钉回归
_BARRIER_TIMEOUT = REQUEST_TIMEOUT


# ---------- 纯函数（单测钉口径：test_perf_loadtest_script.py） ----------


def percentile(values: list[float], q: float) -> float:
    """线性插值分位数（numpy 默认口径）；空列表 ValueError。

    q ∈ [0,1]：0=min，1=max，0.5=中位。排序在内做，调用方免排。
    """
    if not values:
        raise ValueError("percentile of empty list")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q out of range: {q}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


@dataclass
class Sample:
    """一次请求的结果：状态码、完整时长、（生成路径）首个 delta 到达时长。"""

    status: int
    elapsed: float
    ttft: float | None = None
    retry_after: str | None = None


@dataclass
class Summary:
    """一场压测的汇总（纯数据，便于单测与渲染分离）。"""

    path: str
    users: int
    duration: float
    samples: list[Sample] = field(default_factory=list)
    setup_errors: int = 0  # 登录/建会话失败（不进请求统计，单列）

    @property
    def total(self) -> int:
        return len(self.samples)

    @property
    def ok(self) -> int:
        return sum(1 for s in self.samples if 200 <= s.status < 300)

    @property
    def errors(self) -> int:
        return self.total - self.ok

    @property
    def rate_429(self) -> int:
        return sum(1 for s in self.samples if s.status == 429)

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total else 0.0

    @property
    def rps(self) -> float:
        return self.total / self.duration if self.duration > 0 else 0.0

    def latencies(self) -> list[float]:
        return [s.elapsed for s in self.samples]

    def ttfts(self) -> list[float]:
        return [s.ttft for s in self.samples if s.ttft is not None]


class SSEEventAccumulator:
    """逐行喂入的 SSE 事件拼装器（纯逻辑，无 IO——单测钉块边界口径）。

    服务端每个事件 = ``event: <name>`` 行 + ``data: <json>`` 行 + 空行。
    feed_line 逐行喂：凑齐一个块（遇空行）返回 ``(event, data_text)``，
    其余时候返回 None。data 多行按 SSE 规范拼接（本仓服务端恒单行）。
    """

    def __init__(self) -> None:
        self._event: str | None = None
        self._data_lines: list[str] = []

    def feed_line(self, line: str) -> tuple[str, str] | None:
        if line == "":
            if self._event is None and not self._data_lines:
                return None  # 连续空行/流开头，无块可吐
            result = (self._event or "message", "\n".join(self._data_lines))
            self._event = None
            self._data_lines = []
            return result
        if line.startswith("event:"):
            self._event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            self._data_lines.append(line.removeprefix("data:").strip())
        # 注释行（:keep-alive）与其他字段（id:/retry:）忽略——本仓不用
        return None


def fmt_seconds(value: float) -> str:
    return f"{value:.2f}s"


def render_summary_line(summary: Summary) -> str:
    """一行 Markdown 表格行（数字机械粘贴进报告，不带任何修饰）。"""
    lat = summary.latencies()
    ttft_part = ""
    ttfts = summary.ttfts()
    if ttfts:
        ttft_part = (
            f" | ttft p50={fmt_seconds(percentile(ttfts, 0.5))}"
            f" p95={fmt_seconds(percentile(ttfts, 0.95))}"
            f" p99={fmt_seconds(percentile(ttfts, 0.99))}"
        )
    if not lat:
        return (
            f"| {summary.path} | {summary.users} | 0 | 0 | - | - | - |"
            f" - | {summary.rate_429} | setup_errors={summary.setup_errors} |"
        )
    return (
        f"| {summary.path} | {summary.users} | {summary.total} | {summary.rps:.2f} |"
        f" {fmt_seconds(percentile(lat, 0.5))} | {fmt_seconds(percentile(lat, 0.95))} |"
        f" {fmt_seconds(percentile(lat, 0.99))} | {summary.error_rate:.2%} |"
        f" {summary.rate_429}{ttft_part} |"
    )


# ---------- 压测执行（IO 层，不做纯计算——计算全在上面的纯函数） ----------


def _client(base_url: str, cookies: dict[str, str] | None = None) -> httpx.Client:
    # trust_env=False：本机 compose 直连，绝不走系统代理（7890 端口代理会劫持
    # localhost 流量，压测数字全废）
    return httpx.Client(
        base_url=base_url,
        timeout=REQUEST_TIMEOUT,
        trust_env=False,
        cookies=cookies,
    )


def login(base_url: str, username: str, password: str) -> dict[str, str]:
    """登录一次拿会话 cookie（登录闸 10/60s/IP——压测只登一次，不触闸）。"""
    with _client(base_url) as client:
        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        resp.raise_for_status()
        token = resp.cookies.get(SESSION_COOKIE)
        if not token:
            raise RuntimeError("登录响应没有会话 cookie")
        return {SESSION_COOKIE: token}


def _create_operator_session(client: httpx.Client) -> int:
    resp = client.post("/api/service/sessions")
    resp.raise_for_status()
    return int(resp.json()["id"])


def _ask_once(
    client: httpx.Client, session_id: int, question: str
) -> Sample:
    """一次发问：完整读 SSE 流；记完整时长与首个 delta 到达（TTFT 口径）。

    成功判据=2xx 且流里见到 complete 事件（先收全再流，complete 是最后一
    个事件——没读到就是流中断/代理截断，按错误样本记 status=-2）。
    """
    start = time.perf_counter()
    ttft: float | None = None
    saw_complete = False
    try:
        with client.stream(
            "POST",
            f"/api/service/sessions/{session_id}/messages",
            json={"content": question},
        ) as resp:
            status = resp.status_code
            if status != 200:
                # 限流/鉴权错误：响应体短，读完取 Retry-After
                resp.read()
                return Sample(
                    status=status,
                    elapsed=time.perf_counter() - start,
                    retry_after=resp.headers.get("retry-after"),
                )
            accumulator = SSEEventAccumulator()
            for line in resp.iter_lines():
                event = accumulator.feed_line(line)
                if event is None:
                    continue
                name, _data = event
                if name == "delta" and ttft is None:
                    ttft = time.perf_counter() - start
                if name == "complete":
                    saw_complete = True
    except httpx.HTTPError:
        return Sample(status=-1, elapsed=time.perf_counter() - start, ttft=ttft)
    if not saw_complete:
        return Sample(status=-2, elapsed=time.perf_counter() - start, ttft=ttft)
    return Sample(status=status, elapsed=time.perf_counter() - start, ttft=ttft)


def _run_engine_path(
    base_url: str,
    question: str,
    users: int,
    duration: float,
    username: str,
    password: str,
    summary: Summary,
) -> None:
    """refusal/tool/rag 共用：登录 -> 每线程一会话 -> 时长窗内循环发问。"""
    cookies = login(base_url, username, password)
    stop = threading.Event()
    threads: list[threading.Thread] = []
    local_samples: list[list[Sample]] = [[] for _ in range(users)]
    setup_errors = [0]

    def worker(index: int) -> None:
        with _client(base_url, cookies) as client:
            try:
                session_id = _create_operator_session(client)
            except httpx.HTTPError:
                setup_errors[0] += 1
                return
            while not stop.is_set():
                local_samples[index].append(_ask_once(client, session_id, question))

    for i in range(users):
        thread = threading.Thread(target=worker, args=(i,), daemon=True)
        threads.append(thread)
    for thread in threads:
        thread.start()
    time.sleep(duration)
    stop.set()
    for thread in threads:
        thread.join(timeout=REQUEST_TIMEOUT + 10)

    for bucket in local_samples:
        summary.samples.extend(bucket)
    summary.setup_errors = setup_errors[0]


def _run_health(base_url: str, users: int, duration: float, summary: Summary) -> None:
    stop = threading.Event()
    threads: list[threading.Thread] = []
    local_samples: list[list[Sample]] = [[] for _ in range(users)]

    def worker(index: int) -> None:
        with _client(base_url) as client:
            while not stop.is_set():
                start = time.perf_counter()
                try:
                    resp = client.get("/health")
                    local_samples[index].append(
                        Sample(status=resp.status_code, elapsed=time.perf_counter() - start)
                    )
                except httpx.HTTPError:
                    local_samples[index].append(
                        Sample(status=-1, elapsed=time.perf_counter() - start)
                    )

    for i in range(users):
        thread = threading.Thread(target=worker, args=(i,), daemon=True)
        threads.append(thread)
    for thread in threads:
        thread.start()
    time.sleep(duration)
    stop.set()
    for thread in threads:
        thread.join(timeout=30)

    for bucket in local_samples:
        summary.samples.extend(bucket)


def _run_ratelimit(base_url: str, users: int, summary: Summary) -> None:
    """顾客通道建会话并发一波：每线程一击 POST /api/customer/sessions。

    不走时长窗（闸是滑动窗口计数，一波并发即验证）；预期同 IP 最多 5 个
    201、其余 429+Retry-After（IP 建会话 5/60s，services/rate_limit）。

    对齐另两 runner 的线程纪律（103 刀债）：daemon=True + barrier.wait 带超时
    ——某线程在开火前先死（连接池构造失败等）时，其余线程的裸 barrier.wait
    会永久挂起把进程钉死；超时计一击 error 样本（status=-3）如实进报告。
    """
    local_samples: list[list[Sample]] = [[] for _ in range(users)]
    barrier = threading.Barrier(users)

    def worker(index: int) -> None:
        start = time.perf_counter()  # 起点含栅栏等待：同伴未到齐超时也是本次的真实耗时
        try:
            with _client(base_url) as client:
                barrier.wait(timeout=_BARRIER_TIMEOUT)  # 全员就绪同时开火——窗口内一击触发闸
                resp = client.post("/api/customer/sessions")
                local_samples[index].append(
                    Sample(
                        status=resp.status_code,
                        elapsed=time.perf_counter() - start,
                        retry_after=resp.headers.get("retry-after"),
                    )
                )
        except threading.BrokenBarrierError:
            # 有同批线程没到齐（先死者打破栅栏/自己等待超时）：按错误样本记账，
            # 不挂死——daemon 线程随主线程退出，报告里 status=-3 可见
            local_samples[index].append(Sample(status=-3, elapsed=time.perf_counter() - start))
        except httpx.HTTPError:
            local_samples[index].append(
                Sample(status=-1, elapsed=time.perf_counter() - start)
            )

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=REQUEST_TIMEOUT + 10)

    for bucket in local_samples:
        summary.samples.extend(bucket)


def run_load(
    path: str,
    *,
    base_url: str,
    users: int,
    duration: float,
    username: str,
    password: str,
) -> Summary:
    summary = Summary(path=path, users=users, duration=duration)
    if path == "health":
        _run_health(base_url, users, duration, summary)
    elif path == "ratelimit":
        _run_ratelimit(base_url, users, summary)
    else:
        questions = {"refusal": QUESTION_REFUSAL, "tool": QUESTION_TOOL, "rag": QUESTION_RAG}
        _run_engine_path(
            base_url, questions[path], users, duration, username, password, summary
        )
    return summary


def print_report(summary: Summary) -> None:
    print(f"=== path={summary.path} users={summary.users} duration={summary.duration:.0f}s ===")
    print(
        f"requests={summary.total} ok={summary.ok} errors={summary.errors}"
        f" ({summary.error_rate:.2%}) 429={summary.rate_429}"
        f" setup_errors={summary.setup_errors}"
    )
    print(f"RPS={summary.rps:.2f}")
    lat = summary.latencies()
    if lat:
        print(
            f"latency: p50={fmt_seconds(percentile(lat, 0.5))}"
            f" p95={fmt_seconds(percentile(lat, 0.95))}"
            f" p99={fmt_seconds(percentile(lat, 0.99))}"
            f" min={fmt_seconds(min(lat))} max={fmt_seconds(max(lat))}"
        )
    ttfts = summary.ttfts()
    if ttfts:
        print(
            f"ttft(首个delta): p50={fmt_seconds(percentile(ttfts, 0.5))}"
            f" p95={fmt_seconds(percentile(ttfts, 0.95))}"
            f" p99={fmt_seconds(percentile(ttfts, 0.99))}"
            f" n={len(ttfts)}"
        )
    if summary.path == "ratelimit":
        # 闸验证实录：按状态码分组列 Retry-After（阈值证据）
        by_status: dict[int, list[str | None]] = {}
        for sample in summary.samples:
            by_status.setdefault(sample.status, []).append(sample.retry_after)
        for status_code, values in sorted(by_status.items()):
            retry_values = sorted({v for v in values if v is not None})
            print(f"status={status_code}: {len(values)} 次 retry_after={retry_values or '-'}")
    print(f"表格行：{render_summary_line(summary)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="轻量并发压测（stdlib 线程 + httpx）")
    parser.add_argument("--path", required=True, choices=["health", "refusal", "tool", "rag", "ratelimit"])
    parser.add_argument("--users", type=int, default=10, help="并发线程数")
    parser.add_argument("--duration", type=float, default=60, help="时长窗秒数（ratelimit 忽略）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default="operator")
    parser.add_argument("--password", default="operator123")
    args = parser.parse_args(argv)

    summary = run_load(
        args.path,
        base_url=args.base_url,
        users=args.users,
        duration=args.duration,
        username=args.username,
        password=args.password,
    )
    print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
