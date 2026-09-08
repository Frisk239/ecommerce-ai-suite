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
from suite_api.models import Asset, AssetVersion, AuditLog, Operator
from suite_api.services import registration
from suite_api.services.asset_view import (
    load_products,
    published_version_nos,
    read_version_text,
    to_asset_out,
)
from suite_api.services.machine_wash import QA_FIELD, redact
from suite_api.services.retrieval import retrieve

# get_asset 对「取不到已发布版本」统一口径：不区分资产不存在/存在但未发布/
# 版本存在但从未发布——知道 ID 也探不出哪些是待人洗（ADR 0020 的闸门语义）。
_UNPUBLISHED_MESSAGE = "资产或版本不存在，或从未发布：连接层只能读取已发布版本"

# export 留痕（第 22 刀/ADR 0041）：audit_log.operator_id 是非空 FK operators.id，
# 连接层调用方只有 Bearer token、无逐用户身份——归到这条系统操作者行「mcp」。
# password_hash 置 "!"：不是合法 bcrypt 串，seed.check_password 捕获 ValueError
# 按校验失败处理，此账号永远登不进控制台，只作留痕归属；血缘时间线里如实显示
# 操作者名「mcp」，与治理台真人动作可分辨。
MCP_OPERATOR_USERNAME = "mcp"
_MCP_OPERATOR_UNLOGINABLE_HASH = "!"


def ensure_mcp_operator_id(session) -> int:  # noqa: ANN001 - SQLAlchemy Session 窄用
    """确保系统操作者「mcp」行存在并返回 id（export 留痕的 operator 归属）。"""
    operator = session.scalar(
        select(Operator).where(Operator.username == MCP_OPERATOR_USERNAME)
    )
    if operator is None:
        operator = Operator(
            username=MCP_OPERATOR_USERNAME, password_hash=_MCP_OPERATOR_UNLOGINABLE_HASH
        )
        session.add(operator)
        session.flush()
    return operator.id


def _mask_fields_map(fields: dict) -> dict:
    """0038 修订（第 21 刀评审处置件 1：MCP get_asset=第六出口）：字段映射表
    （extracted_fields / confirmed_fields）出 MCP 响应前统一过 redact——
    字符串 value 与 qa_pairs 每项 q/a；abstained 项与坏形状原样走（防御不
    改写形状）。新写入侧（机洗第 17 刀、人洗出口 2）已掩，这里是读侧对
    历史脏行的兜底收口，幂等无害；掩码不回写存储（字节不动）。
    title 的收口在推导源头（routes/service._first_question，评审处置件 2），
    本处不重复掩。"""

    def _mask_value(name: str, value):  # noqa: ANN001, ANN202 - JSONB 回读三态
        if name == QA_FIELD and isinstance(value, list):
            return [
                {**p, "q": redact(p["q"]), "a": redact(p["a"])}
                if isinstance(p, dict) and isinstance(p.get("q"), str) and isinstance(p.get("a"), str)
                else p
                for p in value
            ]
        return redact(value) if isinstance(value, str) else value

    masked: dict = {}
    for name, entry in fields.items():
        if isinstance(entry, dict) and "value" in entry:
            entry = {**entry, "value": _mask_value(name, entry["value"])}
        masked[name] = entry
    return masked


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
        0038 修订（第 21 刀评审处置件 1）：content 与两张字段映射表以掩码
        形态出边界（MCP 响应=进程边界出口，出口必掩；对象字节不动）。
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
            # 0038 修订（第 21 刀评审处置件 1：MCP get_asset=第六出口）：正文与
            # 两张字段映射表出 MCP 响应前过 redact（掩码不回写字节，对象键/
            # 版本指针不动；与 export_published 同口径）；title 在推导源头收掩
            # （routes/service._first_question），此处直读 assets 行即净。
            return {
                "id": asset.id,
                "title": asset.title,
                "kind": asset.kind,
                "source_kind": asset.source_kind,
                "version_no": asset_version.version_no,
                "object_key": asset_version.object_key,
                "content": redact(content),
                "extracted_fields": _mask_fields_map(dict(asset_version.extracted_fields)),
                "confirmed_fields": _mask_fields_map(dict(asset_version.confirmed_fields)),
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

        0038 修订（第 21 刀，审计刀 4 P0 簇出口 4）：字节不动、出口必掩——
        MCP 响应跨进程边界，导出正文（版本字节原文，未掩区）返回前过
        redact；对象键与存储字节不动。search_published 的 chunk 已在
        retrieve 返回处统一收掩（出口 1 收口点），两条只读出口同口径。

        0041（第 22 刀顺手件）：成功导出的每份资产写一行 audit_log
        （action="export"，含资产与当时版本号；只记元数据，正文不落留痕），
        operator 归属系统操作者「mcp」（见 ensure_mcp_operator_id 的列形状
        说明）。血缘「导出」环从此有料可拼；血缘写回（writebacks）的 action
        集合={publish, rollback}（services/lineage.WRITEBACK_ACTIONS），不
        含 export——不混入，钉测在 tests/test_mcp.py。
        """
        with _db_session() as session:
            storage = ensure_storage(host)
            rows = session.execute(
                select(Asset, AssetVersion)
                .join(AssetVersion, Asset.current_published_version_id == AssetVersion.id)
                .where(Asset.status == "published")
                .order_by(Asset.id)
            ).all()
            exported = [
                {
                    "asset_id": asset.id,
                    "version_no": asset_version.version_no,
                    "title": asset.title,
                    "kind": asset.kind,
                    "source_kind": asset.source_kind,
                    # 0038 修订：出口必掩（见上方 docstring；掩码不回写字节）
                    "content": redact(read_version_text(session, storage, asset_version)),
                }
                for asset, asset_version in rows
            ]
            if exported:
                operator_id = ensure_mcp_operator_id(session)
                session.add_all(
                    AuditLog(
                        operator_id=operator_id,
                        asset_id=entry["asset_id"],
                        version_no=entry["version_no"],
                        action="export",
                    )
                    for entry in exported
                )
                session.commit()
            return exported

    streamable_app = mcp.streamable_http_app()
    # SDK 挂载约定：mounted 子应用 lifespan 不执行，session manager 交 host 代跑
    host.state.mcp_session_manager = mcp.session_manager
    return BearerGateMiddleware(streamable_app, settings)
