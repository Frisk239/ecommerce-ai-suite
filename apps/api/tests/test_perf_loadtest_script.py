"""`scripts/perf/loadtest.py` 纯函数口径钉子（第 103 刀：分位数/SSE 解析/汇总）。

只测纯函数（percentile / SSEEventAccumulator / Summary / render_summary_line），
不发请求、不碰库——IO 层由真跑压测（docs/research/perf-report.md）覆盖。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "perf"
sys.path.insert(0, str(SCRIPTS_DIR))

import loadtest  # noqa: E402, I001


# ---------- percentile：线性插值口径 ----------


def test_percentile_single_value():
    assert loadtest.percentile([3.5], 0.5) == 3.5
    assert loadtest.percentile([3.5], 0.0) == 3.5
    assert loadtest.percentile([3.5], 1.0) == 3.5


def test_percentile_interpolation():
    # 两值 [1.0, 2.0]：q=0.25 → 1.25（线性插值，非最近邻）
    values = [2.0, 1.0]
    assert loadtest.percentile(values, 0.25) == pytest.approx(1.25)
    assert loadtest.percentile(values, 0.5) == pytest.approx(1.5)
    assert loadtest.percentile(values, 0.75) == pytest.approx(1.75)


def test_percentile_exact_position_no_smear():
    # 四值分位恰落整数位：p50=两中位均值，p95 在 2.85 位插值
    values = [1.0, 2.0, 3.0, 4.0]
    assert loadtest.percentile(values, 0.5) == pytest.approx(2.5)
    assert loadtest.percentile(values, 0.95) == pytest.approx(3.85)


def test_percentile_does_not_mutate_input():
    values = [3.0, 1.0, 2.0]
    loadtest.percentile(values, 0.9)
    assert values == [3.0, 1.0, 2.0]


def test_percentile_empty_and_bad_q():
    with pytest.raises(ValueError):
        loadtest.percentile([], 0.5)
    with pytest.raises(ValueError):
        loadtest.percentile([1.0], 1.5)
    with pytest.raises(ValueError):
        loadtest.percentile([1.0], -0.1)


# ---------- SSEEventAccumulator：块边界/事件名/data 拼接 ----------


def _feed_text(accumulator: loadtest.SSEEventAccumulator, text: str) -> list[tuple[str, str]]:
    events = []
    for line in text.split("\n"):
        event = accumulator.feed_line(line)
        if event is not None:
            events.append(event)
    return events


def test_sse_single_event_block():
    acc = loadtest.SSEEventAccumulator()
    events = _feed_text(acc, 'event: thinking\ndata: {"text": "正在检索…"}\n\n')
    assert events == [("thinking", json.dumps({"text": "正在检索…"}, ensure_ascii=False))]


def test_sse_delta_then_complete_sequence():
    acc = loadtest.SSEEventAccumulator()
    raw = (
        'event: delta\ndata: {"text": "Xperia Ear D"}\n\n'
        'event: delta\ndata: {"text": "uo 的上市年份"}\n\n'
        'event: complete\ndata: {"kind": "answer"}\n\n'
    )
    events = _feed_text(acc, raw)
    assert [name for name, _ in events] == ["delta", "delta", "complete"]
    assert json.loads(events[0][1]) == {"text": "Xperia Ear D"}


def test_sse_leading_and_trailing_blank_lines_ignored():
    acc = loadtest.SSEEventAccumulator()
    events = _feed_text(acc, '\nevent: delta\ndata: {"text": "x"}\n\n\n')
    assert events == [("delta", '{"text": "x"}')]


def test_sse_multiline_data_joined():
    # SSE 规范：多 data 行换行拼接（本仓服务端恒单行，解析器兼容）
    acc = loadtest.SSEEventAccumulator()
    events = _feed_text(acc, 'event: delta\ndata: {"a":\ndata: "b"}\n\n')
    assert events == [("delta", '{"a":\n"b"}')]


def test_sse_comment_and_unknown_fields_ignored():
    acc = loadtest.SSEEventAccumulator()
    events = _feed_text(acc, ': keep-alive\nid: 42\nevent: tool\ndata: {"name": "get_stock"}\n\n')
    assert events == [("tool", '{"name": "get_stock"}')]


def test_sse_event_without_name_falls_back_to_message():
    acc = loadtest.SSEEventAccumulator()
    events = _feed_text(acc, 'data: {"text": "x"}\n\n')
    assert events == [("message", '{"text": "x"}')]


# ---------- Summary：计数口径（429 单列、错误率、RPS、ttft 子集） ----------


def test_summary_counts_and_rates():
    summary = loadtest.Summary(path="refusal", users=10, duration=10.0)
    summary.samples = [
        loadtest.Sample(status=200, elapsed=0.1),
        loadtest.Sample(status=200, elapsed=0.2),
        loadtest.Sample(status=429, elapsed=0.01, retry_after="37"),
        loadtest.Sample(status=-1, elapsed=0.3),
    ]
    assert summary.total == 4
    assert summary.ok == 2
    assert summary.errors == 2
    assert summary.rate_429 == 1
    assert summary.error_rate == pytest.approx(0.5)
    assert summary.rps == pytest.approx(0.4)


def test_summary_ttfts_only_generated_samples():
    summary = loadtest.Summary(path="rag", users=10, duration=10.0)
    summary.samples = [
        loadtest.Sample(status=200, elapsed=5.0, ttft=4.8),
        loadtest.Sample(status=200, elapsed=6.0, ttft=5.9),
        loadtest.Sample(status=429, elapsed=0.01),  # 无 ttft——不进分位
        loadtest.Sample(status=200, elapsed=0.05),  # 非生成/无首 delta：不进
    ]
    assert summary.ttfts() == [4.8, 5.9]


def test_summary_zero_division_safe():
    summary = loadtest.Summary(path="health", users=1, duration=10.0)
    assert summary.error_rate == 0.0
    assert summary.rps == 0.0


def test_render_summary_line_with_and_without_ttft():
    with_ttft = loadtest.Summary(path="rag", users=2, duration=10.0)
    with_ttft.samples = [loadtest.Sample(status=200, elapsed=5.0, ttft=4.0)] * 4
    line = loadtest.render_summary_line(with_ttft)
    assert line.startswith("| rag | 2 | 4 | 0.40 | 5.00s | 5.00s | 5.00s | 0.00% | 0 |")
    assert "ttft p50=4.00s" in line

    no_ttft = loadtest.Summary(path="health", users=2, duration=10.0)
    no_ttft.samples = [loadtest.Sample(status=200, elapsed=0.1)] * 10
    line = loadtest.render_summary_line(no_ttft)
    assert "ttft" not in line
    assert line.startswith("| health | 2 | 10 | 1.00 | 0.10s | 0.10s | 0.10s | 0.00% | 0 |")


def test_render_summary_line_empty_sample():
    empty = loadtest.Summary(path="tool", users=5, duration=10.0, setup_errors=3)
    line = loadtest.render_summary_line(empty)
    assert line == "| tool | 5 | 0 | 0 | - | - | - | - | 0 | setup_errors=3 |"


# ---------- ratelimit 线程纪律（103 刀债收口：daemon + 栅栏超时） ----------


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_run_ratelimit_survives_worker_dying_before_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """钉：单线程**先死**（栅栏前抛非 httpx 异常，如连接池构造失败）不再挂死
    进程——barrier.wait 带超时：幸存线程按 BrokenBarrierError 记 status=-3
    错误样本，run 整体秒级返回（旧形态裸 wait 会永久等待 + 非 daemon 线程
    钉死进程退出）。真发请求的一击路径由真跑压测覆盖（perf-report）。"""
    import threading

    calls = {"n": 0}
    real_client = loadtest._client

    def one_dies_client(base_url, cookies=None):  # noqa: ANN001 - 测试替身窄用
        calls["n"] += 1
        if calls["n"] == 1:  # 首个线程构造 Client 即炸（栅栏之前先死）
            raise RuntimeError("连接池构造失败（模拟先死者）")
        return real_client(base_url, cookies)

    monkeypatch.setattr(loadtest, "_BARRIER_TIMEOUT", 0.2)
    monkeypatch.setattr(loadtest, "_client", one_dies_client)
    summary = loadtest.Summary(path="ratelimit", users=4, duration=0.0)
    done = threading.Event()

    def run() -> None:
        loadtest._run_ratelimit("http://localhost:8000", 4, summary)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=5.0), "单线程先死后 _run_ratelimit 挂死（栅栏无超时回归）"
    # 幸存 3 线程：栅栏破裂记错误样本（status=-3，不是 4xx/5xx 也不是 -1）
    assert [s.status for s in summary.samples] == [-3, -3, -3]
    assert summary.errors == 3
