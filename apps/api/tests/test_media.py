"""第 94b 刀媒体引用单测（ADR 0052）：mime 常量表 / 派生三态 / Range 解析 / 日志脱敏。

不连库、不碰存储——媒体端点与引擎的集成面在 test_media_integration.py。
"""

import asyncio
import io
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from suite_api.observability import configure_logging, redact_query_tokens
from suite_api.services.media import (
    MEDIA_MIME_BY_SUFFIX,
    ByteRange,
    RangeNotSatisfiable,
    key_suffix,
    media_citations_for,
    media_mime,
    media_stream_response,
    parse_single_range,
)

# ---------- mime 派生（kind + 对象键后缀） ----------


@pytest.mark.parametrize(
    ("kind", "key", "expected"),
    [
        ("image", "documents/a/b.png", "image/png"),
        ("image", "documents/a/b.JPG", "image/jpeg"),  # 后缀大小写不敏感
        ("image", "documents/a/b.jpeg", "image/jpeg"),
        ("image", "documents/a/b.webp", "image/webp"),
        ("video", "clips/a/b.mp4", "video/mp4"),
        # 非媒体：旧切片的字节是时间码文本（.txt）——不按 kind 冒充 mp4
        ("video", "clips/a/b.txt", None),
        ("video", "clips/a/b", None),
        # kind 与字节家族不符：文档不因键是 .png 就成了媒体
        ("document", "documents/a/b.png", None),
        ("dialogue", "dialogue/a/b.txt", None),
        ("material", "documents/a/b.jpg", None),
        # 图片资产却挂了视频键（防御：家族一致性校验）
        ("image", "clips/a/b.mp4", None),
        ("video", "clips/a/b.png", None),
        (None, "documents/a/b.png", None),
        ("image", None, None),
        ("image", "", None),
    ],
)
def test_media_mime_kind_and_suffix_table(kind: str | None, key: str | None, expected: str | None) -> None:
    assert media_mime(kind, key) == expected


def test_key_suffix_and_table_shape() -> None:
    assert key_suffix("a/b.tar.gz") == "gz"
    assert key_suffix("a/b") == ""
    assert key_suffix(None) == ""
    # 常量表：四种媒体后缀 -> 两个家族（没有第四种图片格式偷偷加进来）
    assert set(MEDIA_MIME_BY_SUFFIX) == {"png", "jpg", "jpeg", "webp", "mp4"}
    assert set(MEDIA_MIME_BY_SUFFIX.values()) == {
        "image/png",
        "image/jpeg",
        "image/webp",
        "video/mp4",
    }


# ---------- 媒体引用派生（三态：image / video / 无媒体） ----------


class _FakeScalarsSession:
    """按调用序返回两批行（先 assets 后 versions），与 media_citations_for 的查询序一致。"""

    def __init__(self, assets: list[Any], versions: list[Any]) -> None:
        self._batches = [assets, versions]
        self._calls = 0

    def scalars(self, _stmt: Any) -> list[Any]:
        batch = self._batches[self._calls]
        self._calls += 1
        return batch


def _assets(*rows: tuple[int, str]) -> list[Any]:
    return [SimpleNamespace(id=asset_id, kind=kind) for asset_id, kind in rows]


def _versions(*rows: tuple[int, int, str]) -> list[Any]:
    return [
        SimpleNamespace(asset_id=asset_id, version_no=version_no, object_key=object_key)
        for asset_id, version_no, object_key in rows
    ]


def test_media_citations_three_states() -> None:
    """派生三态：image 出 image/*、video 出 video/mp4、非媒体（文档/旧文本切片）不出。"""
    db = _FakeScalarsSession(
        _assets((1, "image"), (2, "video"), (3, "document"), (4, "video")),
        _versions(
            (1, 1, "documents/x/a.png"),
            (2, 1, "clips/x/b.mp4"),
            (3, 1, "documents/x/c.txt"),
            (4, 1, "clips/x/d.txt"),  # 旧切片：键是时间码文本，不是可播媒体
        ),
    )
    citations = [
        {"asset_id": 1, "version_no": 1},
        {"asset_id": 2, "version_no": 1},
        {"asset_id": 3, "version_no": 1},
        {"asset_id": 4, "version_no": 1},
    ]
    assert media_citations_for(db, citations) == [
        {"asset_id": 1, "version_no": 1, "mime": "image/png"},
        {"asset_id": 2, "version_no": 1, "mime": "video/mp4"},
    ]


def test_media_citations_empty_inputs_and_dedupe() -> None:
    # 无 citations（拒答/工具路径）= 无媒体，恒 []（不查库）
    assert media_citations_for(_FakeScalarsSession([], []), []) == []
    assert media_citations_for(_FakeScalarsSession([], []), None) == []
    # 坏形状不猜（防御）；重复对去重；保 citations 原序
    db = _FakeScalarsSession(_assets((1, "image")), _versions((1, 2, "documents/x/a.jpg")))
    citations = [
        {"asset_id": 1, "version_no": 2},
        {"asset_id": 1, "version_no": 2},
        {"asset_id": "1", "version_no": 2},
        {"asset_id": 1},
    ]
    assert media_citations_for(db, citations) == [
        {"asset_id": 1, "version_no": 2, "mime": "image/jpeg"}
    ]


# ---------- Range 解析（206/416 的判定） ----------


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 100, None),  # 无头：全量 200
        ("", 100, None),
        ("bytes=0-99", 100, ByteRange(0, 99)),
        ("bytes=0-0", 100, ByteRange(0, 0)),
        ("bytes=50-", 100, ByteRange(50, 99)),
        ("bytes=-10", 100, ByteRange(90, 99)),  # 末尾 N 字节
        ("bytes=-500", 100, ByteRange(0, 99)),  # 请求量大于对象：整段
        ("bytes=5-99999", 100, ByteRange(5, 99)),  # 上界收敛
        ("bytes=99-99", 100, ByteRange(99, 99)),
        # 忽略形态（HTTP 允许服务端忽略 Range）：多段/单位不对/字面坏头
        ("bytes=0-9,20-29", 100, None),
        ("items=0-9", 100, None),
        ("bytes=abc", 100, None),
        ("bytes=0 1-2", 100, None),
        ("bytes", 100, None),
    ],
)
def test_parse_single_range_ok(header: str | None, size: int, expected: ByteRange | None) -> None:
    assert parse_single_range(header, size) == expected


@pytest.mark.parametrize(
    ("header", "size"),
    [
        ("bytes=100-", 100),  # start 越界
        ("bytes=100-200", 100),
        ("bytes=0-", 0),  # 空对象：任何区间都不成立
        ("bytes=-10", 0),
        ("bytes=-0", 100),  # 零长后缀：不可满足（RFC 9110 §14.1.2）
        ("bytes=10-5", 100),  # 起止颠倒：不可满足（与 nginx 同判）
    ],
)
def test_parse_single_range_unsatisfiable(header: str, size: int) -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_single_range(header, size)


def test_byte_range_length() -> None:
    assert ByteRange(0, 99).length == 100
    assert ByteRange(90, 99).length == 10


# ---------- 日志脱敏（媒体端点 query 令牌） ----------


def test_redact_query_tokens_masks_only_query_form() -> None:
    event = redact_query_tokens(
        None, "info", {"event": 'GET /api/customer/assets/1/media?token=secret123 HTTP/1.1'}
    )
    assert event["event"] == 'GET /api/customer/assets/1/media?token=*** HTTP/1.1'
    # '&token=' 形态（多参数）同样掩
    multi = redact_query_tokens(None, "info", {"event": "GET /x?a=1&token=abc&b=2"})
    assert multi["event"] == "GET /x?a=1&token=***&b=2"
    # **非 query 形态不动**：写动作凭证是另一种东西（「待确认 token=…」）
    other = redact_query_tokens(None, "info", {"event": "待确认 token=deadbeef"})
    assert other["event"] == "待确认 token=deadbeef"
    # 无 token 的日志原样（处理器必须透明）
    plain = redact_query_tokens(None, "info", {"event": "正常一条日志"})
    assert plain["event"] == "正常一条日志"


def test_access_log_line_with_query_token_is_masked_end_to_end() -> None:
    """uvicorn 访问日志（stdlib 记录）经同一条处理链——链上脱敏，调用点零改动。

    断言走 configure_logging **实际装上的那个 root handler**（把它的流换成
    StringIO 读输出），不经 pytest 的 stdout 捕获：后者依赖捕获时机（整仓套件
    里跑与单文件跑可能不同流），而这里要钉的是「链」，不是 pytest 的管道。
    """
    configure_logging("INFO")
    # 接线：uvicorn 自己的 handler 被清掉、propagate=True（且不被先前同进程的
    # alembic fileConfig 留在 disabled/写死级别态）——访问日志必然进 root 链
    uvicorn_access = logging.getLogger("uvicorn.access")
    assert uvicorn_access.handlers == [] and uvicorn_access.propagate is True
    assert uvicorn_access.disabled is False and uvicorn_access.level == logging.NOTSET
    handler = logging.getLogger().handlers[0]
    stream = io.StringIO()
    handler.setStream(stream)
    try:
        uvicorn_access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5555",
            "GET",
            "/api/customer/assets/7/media?token=super-secret-token",
            "1.1",
            206,
        )
    finally:
        handler.setStream(sys.stderr)  # 还原（后续用例的日志照常可读）
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert "token=***" in payload["event"]
    assert "super-secret-token" not in stream.getvalue()
    assert "206" in payload["event"]  # 其余信息量不丢


# ---------- 媒体字节响应装配（第 114 刀 W8：顾客/操作者两面共用的 200/206/416） ----------


class _FakeStreamStorage:
    """iter_bytes 替身：全量/区间一次出（端点语义只依赖字节序与区间，不依赖块大小）。"""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def iter_bytes(self, key: str, *, start: int = 0, end: int | None = None):
        data = self._objects[key]
        yield data[start : (end + 1 if end is not None else None)]


def _drain(response: Any) -> bytes:
    """StreamingResponse 的 body_iterator 收干成字节（Starlette 把同步生成器
    包成异步迭代器，测试里用事件循环收）。"""

    async def _collect() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(_collect())


_Payload = bytes(range(64))


def _respond(range_header: str | None) -> Any:
    return media_stream_response(
        range_header,
        _FakeStreamStorage({"clips/x/a.mp4": _Payload}),
        "clips/x/a.mp4",
        "video/mp4",
        len(_Payload),
    )


def test_media_stream_response_full_without_range() -> None:
    response = _respond(None)
    assert response.status_code == 200
    assert response.media_type == "video/mp4"
    assert response.headers["content-length"] == str(len(_Payload))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert _drain(response) == _Payload


def test_media_stream_response_partial_and_suffix() -> None:
    window = _respond("bytes=4-9")
    assert window.status_code == 206
    assert window.headers["content-range"] == f"bytes 4-9/{len(_Payload)}"
    assert window.headers["content-length"] == "6"
    assert _drain(window) == _Payload[4:10]

    suffix = _respond("bytes=-8")
    assert suffix.status_code == 206
    assert _drain(suffix) == _Payload[-8:]


def test_media_stream_response_ignores_bad_and_multi_ranges() -> None:
    # 多段与语法坏头一律忽略（200 全量）——与端点层行为同判，这里钉住装配层不擅自 206
    for header in ("bytes=0-1,4-5", "items=0-5", "bytes=a-b"):
        response = _respond(header)
        assert response.status_code == 200, header
        assert _drain(response) == _Payload


def test_media_stream_response_416_carries_total_size() -> None:
    too_far = _respond(f"bytes={len(_Payload)}-")
    assert too_far.status_code == 416
    assert too_far.headers["content-range"] == f"bytes */{len(_Payload)}"
