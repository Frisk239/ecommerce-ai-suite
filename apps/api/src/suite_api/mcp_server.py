"""MCP 连接层（ADR 0032/0001/0020/0013/0017）：官方 SDK 挂同一 FastAPI。

- 用官方 MCP Python SDK 内置的高层服务类（``mcp.server.fastmcp.FastMCP``，
  官方 SDK 自带的那个，不是 jlowin/fastmcp 第三方库）产 Streamable HTTP
  ASGI app，mount 到主 FastAPI 的 ``/mcp`` 前缀，对外端点 ``/mcp/``。
- 鉴权（ADR 0032）：``Authorization: Bearer <MCP_BEARER_TOKEN>``，挂在 MCP
  子应用外层的纯 ASGI 中间件；token 未配置/为空或不匹配一律 401。不读操作者
  会话 cookie——连接层与控制台会话彻底隔离（挂载路径外的一切请求根本
  不进这层中间件，/api/* 与 /health 行为零变化）。
- 四工具（0020：只读已发布；0013：登记必须带正文；无 publish——发布只属于
  操作者治理台动作，ADR 0005）：search_published / get_asset /
  register_asset / export_published，全部复用 services 层与客服同一套
  检索索引（0017）。工具运行时凭 host app 引用走 deps 的同一惰性装配。
- stateless + json_response：本刀无通知/订阅需求，每请求独立会话对四工具
  只读场景最简（也免去外部客户端的会话粘性）。
"""

import secrets
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from suite_api.deps import ensure_engine, ensure_storage
from suite_api.models import Asset, AssetVersion
from suite_api.services import registration
from suite_api.services.asset_view import (
    load_products,
    published_version_nos,
    read_version_text,
    to_asset_out,
)
from suite_api.services.retrieval import retrieve

# get_asset 对「取不到已发布版本」统一口径：不区分资产不存在/存在但未发布/
# 版本存在但从未发布——知道 ID 也探不出哪些是待人洗（ADR 0020 的闸门语义）。
_UNPUBLISHED_MESSAGE = "资产或版本不存在，或从未发布：连接层只能读取已发布版本"


class BearerGateMiddleware:
    """MCP 子应用外层的 Bearer 闸门（ADR 0032）。

    纯 ASGI 中间件而非 APIRouter dependency：MCP 是 mount 而非路由。token
    从 settings 引用实时读（空 -> 全 401，含配置错误时的 fail-closed）。
    """

    def __init__(self, app: ASGIApp, settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected = self.settings.mcp_bearer_token
        presented = Headers(scope=scope).get("authorization", "")
        scheme, _, token = presented.partition(" ")
        ok = bool(expected) and scheme.lower() == "bearer" and bool(token)
        if ok:
            # compare_digest 防时序侧信道；按字节比（token 理论上可含非 ASCII）
            ok = secrets.compare_digest(
                token.encode("utf-8"), expected.encode("utf-8")
            )
        if not ok:
            detail = "MCP 未授权：缺少或错误的 Bearer token（MCP_BEARER_TOKEN 未配置时同样拒绝）"
            response = JSONResponse(
                {"detail": detail},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_mcp_app(host: FastAPI) -> ASGIApp:
    """构造 MCP 子应用（Bearer 闸门包官方 SDK Streamable HTTP app）。

    - streamable_http_path="/"：SDK 默认端点是 "/mcp/"，mount 在 "/mcp" 前缀
      下取 "/",对外完整 URL 即 ``/mcp/``（与 SDK 默认端点形状对齐）。
    - 挂载后子应用的 lifespan 不会执行：session manager 存到 host.state，
      由 main.py 的 FastAPI lifespan 代跑（SDK 挂载约定）。
    """
    settings = host.state.settings
    mcp = FastMCP(
        "ecommerce-suite",
        instructions=(
            "电商中台对外连接层。只读「当前已发布」与「曾经发布过的历史版本」，"
            "可登记新文档（落为已接入，等待治理台人洗与发布），不能发布。"
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    def _db_session():
        ensure_engine(host)
        return host.state.session_factory()

    @mcp.tool()
    def search_published(query: str) -> list[dict]:
        """检索当前已发布的资产切块（与站内客服同一检索索引，只命中已发布）。

        返回 [{asset_id, version_no, title, chunk, score}]：chunk 是证据片段，
        score 越大越相关；未发布资产（已接入/待人洗）不会出现。空或纯虚词
        查询返回空列表。
        """
        with _db_session() as session:
            hits = retrieve(session, query)
            asset_ids = {hit["asset_id"] for hit in hits}
            titles = (
                {a.id: a.title for a in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))}
                if asset_ids
                else {}
            )
            return [{**hit, "title": titles.get(hit["asset_id"])} for hit in hits]

    @mcp.tool()
    def get_asset(asset_id: int, version: int | None = None) -> dict:
        """取一份已发布资产的正文与元数据。

        version 不传 = 当前已发布版本（权威指针）；传版本号 = 取该历史版本，
        但仅当它发布过。已接入/待人洗/从未发布的版本一律拒绝（错误信息不含
        未发布内容）。返回 {id, title, kind, source_kind, version_no,
        object_key, content, extracted_fields, confirmed_fields}。
        """
        with _db_session() as session:
            asset = session.get(Asset, asset_id)
            if version is None:
                pointer = asset.current_published_version_id if asset is not None else None
                if asset is None or pointer is None:
                    raise ValueError(_UNPUBLISHED_MESSAGE)
                asset_version = session.get(AssetVersion, pointer)
                if asset_version is None:  # pragma: no cover - 指针完整性由发布事务保证
                    raise ValueError(_UNPUBLISHED_MESSAGE)
            else:
                asset_version = session.scalar(
                    select(AssetVersion).where(
                        AssetVersion.asset_id == asset_id, AssetVersion.version_no == version
                    )
                )
                if asset is None or asset_version is None or asset_version.published_at is None:
                    raise ValueError(_UNPUBLISHED_MESSAGE)
            content = read_version_text(session, ensure_storage(host), asset_version)
            return {
                "id": asset.id,
                "title": asset.title,
                "kind": asset.kind,
                "source_kind": asset.source_kind,
                "version_no": asset_version.version_no,
                "object_key": asset_version.object_key,
                "content": content,
                "extracted_fields": dict(asset_version.extracted_fields),
                "confirmed_fields": dict(asset_version.confirmed_fields),
            }

    @mcp.tool()
    def register_asset(content: str, title: str = "", product_id: int | None = None) -> dict:
        """登记一份文本文档进中台（必须带正文，空正文拒绝）。

        登记后资产状态为已接入（或机洗成功后待人洗），出现在治理台队列等待
        操作者人洗与发布；来源固定为连接层登记（mcp_registered）。本工具
        没有任何发布能力。返回登记后的资产视图 {id, title, kind, status,
        source_kind, product, last_error, current_published_version_no}。
        """
        if not content.strip():
            raise ValueError("登记必须带正文：只给标题的空壳登记被拒绝（ADR 0013）")
        try:
            with _db_session() as session:
                asset = registration.register_asset(
                    session,
                    ensure_storage(host),
                    kind="document",
                    title=title or None,
                    content_bytes=content.encode("utf-8"),
                    filename=f"mcp-{uuid4().hex}.txt",
                    product_id=product_id,
                    source_kind="mcp_registered",
                )
                session.commit()
                session.refresh(asset)
                out = to_asset_out(asset, load_products(session, [asset]), published_version_nos(session, [asset]))
                return out.model_dump(mode="json")
        except HTTPException as exc:
            # registration 骨架抛 FastAPI 语义（如挂错商品 404）——转成工具
            # 错误文案，别把 HTTP 状态码语义泄给外部 Agent。
            raise ValueError(f"登记失败：{exc.detail}") from exc

    @mcp.tool()
    def export_published() -> list[dict]:
        """导出全部当前已发布资产（含每份当前已发布版本的正文全文）。

        返回 [{asset_id, version_no, title, kind, source_kind, content}]，
        按资产 ID 升序。只含当前指针指向的已发布版本；待人洗与已接入不导出。
        """
        with _db_session() as session:
            storage = ensure_storage(host)
            rows = session.execute(
                select(Asset, AssetVersion)
                .join(AssetVersion, Asset.current_published_version_id == AssetVersion.id)
                .where(Asset.status == "published")
                .order_by(Asset.id)
            ).all()
            return [
                {
                    "asset_id": asset.id,
                    "version_no": asset_version.version_no,
                    "title": asset.title,
                    "kind": asset.kind,
                    "source_kind": asset.source_kind,
                    "content": read_version_text(session, storage, asset_version),
                }
                for asset, asset_version in rows
            ]

    streamable_app = mcp.streamable_http_app()
    # SDK 挂载约定：mounted 子应用 lifespan 不执行，session manager 交 host 代跑
    host.state.mcp_session_manager = mcp.session_manager
    return BearerGateMiddleware(streamable_app, settings)
