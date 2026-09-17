"""媒体引用与媒体字节（第 94b 刀，ADR 0052）：mime 常量表 + 引用派生 + Range 解析。

三件事都在这里，路由/引擎只消费（单一出处，避免「derive 一套、端点又判一套」）：

- **mime 派生**（``media_mime``）：mime 从 **资产种类 + 对象键后缀** 的常量表取，
  不嗅字节、不听模型。键后缀跟字节走（ADR 0003/0047），故它是「这份字节是什么」
  的可信投影；后缀不在表内（如旧切片的时间码 ``.txt``）-> 不是媒体，不产引用
  （视频资产也可能正文由 transcript 承载，字节根本不是可播媒体）。
- **媒体引用派生**（``media_citations_for``）：以**同一份 citations**（服务端定，
  0007）为输入，按 (asset_id, version_no) 查资产种类与版本对象键，逐条判
  ``media_mime``。模型无决定权，非媒体命中恒 ``[]``（形态固定，不是可选键）。
- **Range 解析**（``parse_single_range``）：单段 ``bytes=`` 语义（206/416 的判定
  在端点层）；多段/畸形头按 HTTP 惯例**忽略**（全量 200），只有语法合法但越界
  才是 416——「忽略」与「拒绝」的边界写死在纯函数里，便于单测钉住。
"""

from dataclasses import dataclass
from typing import Any

from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion

# mime 常量表：后缀 -> mime（键后缀跟字节走，一张表两处用——派生与端点取
# Content-Type 都走这里，两处口径不可能漂移）。四种是产品里可能出现的全部
# 媒体字节：图片三种（94a 上传闸的 MIME 白名单同集）+ 切片 mp4。
MEDIA_MIME_BY_SUFFIX: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "mp4": "video/mp4",
}

# 能出媒体的资产种类：与 mime 家族一致校验（image 只出 image/*，video 只出
# video/*）——防「文档资产挂了 .png 键」这类越界形态冒充媒体。
MEDIA_KINDS: tuple[str, ...] = ("image", "video")


def key_suffix(object_key: str | None) -> str:
    """对象键后缀（小写，无点）；取不到返回空串（不猜）。"""
    if not object_key:
        return ""
    _, dot, suffix = object_key.rpartition(".")
    return suffix.lower() if dot else ""


def media_mime(kind: str | None, object_key: str | None) -> str | None:
    """(资产种类, 对象键) -> mime；不是可服务的媒体返回 None。

    两个条件都要：后缀在常量表内（字节真是媒体）**且** mime 家族与资产种类一致
    （image 对 image/*、video 对 video/*）。旧切片资产键是 ``.txt``（字节是时间码
    文本）——后缀不在表内，天然不产媒体引用，也不可播（不按 kind 冒充 mp4）。
    """
    if kind not in MEDIA_KINDS:
        return None
    mime = MEDIA_MIME_BY_SUFFIX.get(key_suffix(object_key))
    if mime is None or not mime.startswith(f"{kind}/"):
        return None
    return mime


def media_citations_for(
    db: Session, citations: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """由 citations（``[{asset_id, version_no}]``）派生媒体引用。

    形状 ``[{asset_id, version_no, mime}]``，**保 citations 原序、去重**——媒体
    附件与引用芯片指向同一批证据（0007：引用=资产 ID+版本号；不另成一套选取）。
    无命中/命中非媒体恒 ``[]``。citations 是服务端定的（模型无引用决定权），
    故媒体引用同样无模型介入面。
    """
    if not citations:
        return []
    pairs: list[tuple[int, int]] = []
    for citation in citations:
        asset_id = citation.get("asset_id")
        version_no = citation.get("version_no")
        if not isinstance(asset_id, int) or not isinstance(version_no, int):
            continue  # 防御：坏形状不猜（正常路径恒为 0007 的 {asset_id, version_no}）
        if (asset_id, version_no) not in pairs:
            pairs.append((asset_id, version_no))
    if not pairs:
        return []
    asset_ids = {pair[0] for pair in pairs}
    kinds = {
        asset.id: asset.kind
        for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    }
    keys = {
        (version.asset_id, version.version_no): version.object_key
        for version in db.scalars(
            select(AssetVersion).where(
                AssetVersion.asset_id.in_(asset_ids),
                AssetVersion.version_no.in_({pair[1] for pair in pairs}),
            )
        )
    }
    out: list[dict[str, Any]] = []
    for asset_id, version_no in pairs:
        mime = media_mime(kinds.get(asset_id), keys.get((asset_id, version_no)))
        if mime is not None:
            out.append({"asset_id": asset_id, "version_no": version_no, "mime": mime})
    return out


# ---------- 单段 Range（RFC 9110 §14.2 的子集） ----------


class RangeNotSatisfiable(Exception):
    """语法合法但无重叠区间的 Range（端点据此回 416 + ``bytes */{size}``）。"""


@dataclass(frozen=True)
class ByteRange:
    """闭区间 ``[start, end]``（两端都在对象长度之内，由解析函数保证）。"""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_single_range(header: str | None, size: int) -> ByteRange | None:
    """解析单段 Range 头；返回 None = **忽略该头**（照常 200 全量）。

    - 无头 / 空 / 非 ``bytes=`` 单位 / 多段（逗号）/ 语法坏（空段、非数字）
      -> None（HTTP 允许服务端忽略 Range，多段我们不做 multipart，
      忽略比半实现诚实）。
    - 语法合法但**无重叠**（start >= size、``-0``、起止颠倒 ``a-b`` 且 b<a；
      空对象任何区间都不成立）-> ``RangeNotSatisfiable``（416）——起止颠倒按
      RFC 9110 §14.1.2「非零后缀/倒置区间不可满足」与主流服务器（nginx）同判。
    - 越界上界按惯例收敛：``bytes=5-99999`` 于 100 字节对象 -> ``[5, 99]``；
      ``bytes=-500`` 于 100 字节对象 -> 整段 ``[0, 99]``。
    """
    if header is None or size < 0:
        return None
    head = header.strip()
    if not head:
        return None
    unit, sep, spec = head.partition("=")
    if not sep or unit.strip().lower() != "bytes":
        return None
    if "," in spec:  # 多段：忽略（不假装支持 multipart/byteranges）
        return None
    if " " in spec.strip():
        return None
    first, dash, last = spec.strip().partition("-")
    if not dash:
        return None  # 没有 '-' 不是区间
    try:
        if first == "":  # "-N"：末尾 N 字节
            length = int(last)
            if length <= 0:
                raise RangeNotSatisfiable
            if size == 0:
                raise RangeNotSatisfiable
            return ByteRange(start=max(0, size - length), end=size - 1)
        start = int(first)
        if start < 0:
            return None
        if last == "":  # "N-"：从 N 到末尾
            end = size - 1
        else:
            end = int(last)
            if end < 0:
                return None
            end = min(end, size - 1)
        if start >= size or end < start:
            raise RangeNotSatisfiable
        return ByteRange(start=start, end=end)
    except ValueError:
        return None  # 非数字（字面坏头）——忽略


# ---------- 媒体字节响应（200/206/416 的公共装配；第 114 刀起两面共用） ----------

# 媒体响应头（全量/区间两分支共用）：Range 能力声明 + 不缓存（URL 可能带顾客
# 令牌，别让中间层/浏览器把「带凭证的 URL 内容」留在共享缓存里）+ nosniff
# （Content-Type 由常量表定死，不许浏览器嗅探改判）。顾客面（customer 路由）
# 与操作者面（assets 路由）的媒体端点同一份——两头漂移会让 <video> 在一个面
# 能 seek 另一个面不能。
MEDIA_BASE_HEADERS = {
    "Accept-Ranges": "bytes",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


def media_stream_response(
    range_header: str | None,
    storage: Any,
    object_key: str,
    mime: str,
    size: int,
) -> Response:
    """对象字节 → 媒体响应：无/坏 Range=200 全量，单段=206，语法合法越界=416。

    ``size`` 由调用方先取好——「对象缺失怎么报」两面口径不同（顾客面统一 404
    不泄漏存在性，操作者面 409 带对象键如实说），本函数只负责「已知的 size 与
    字节」怎么出。字节按区间流式（``storage.iter_bytes`` 分块），不整读进内存
    ——视频可上百 MB。
    """
    try:
        window = parse_single_range(range_header, size)
    except RangeNotSatisfiable:
        # 416 必须带 ``bytes */{size}``，客户端据此知道真实长度（RFC 9110 §15.5.17）
        return Response(
            status_code=416,
            headers={**MEDIA_BASE_HEADERS, "Content-Range": f"bytes */{size}"},
        )
    if window is None:
        return StreamingResponse(
            storage.iter_bytes(object_key),
            media_type=mime,
            headers={**MEDIA_BASE_HEADERS, "Content-Length": str(size)},
        )
    return StreamingResponse(
        storage.iter_bytes(object_key, start=window.start, end=window.end),
        status_code=206,
        media_type=mime,
        headers={
            **MEDIA_BASE_HEADERS,
            "Content-Length": str(window.length),
            "Content-Range": f"bytes {window.start}-{window.end}/{size}",
        },
    )
