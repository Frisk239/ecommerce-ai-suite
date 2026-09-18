"""MCP 连接层（ADR 0032/0001/0020/0013/0017；第 99 刀 ADR 0057 活状态只读）。

- 用官方 MCP Python SDK 内置的高层服务类（``mcp.server.fastmcp.FastMCP``，
  官方 SDK 自带的那个，不是 jlowin/fastmcp 第三方库）产 Streamable HTTP
  ASGI app，mount 到主 FastAPI 的 ``/mcp`` 前缀，对外端点 ``/mcp/``。
- 鉴权（ADR 0032）：``Authorization: Bearer <MCP_BEARER_TOKEN>``，挂在 MCP
  子应用外层的纯 ASGI 中间件；token 未配置/为空或不匹配一律 401。不读操作者
  会话 cookie——连接层与控制台会话彻底隔离（挂载路径外的一切请求根本
  不进这层中间件，/api/* 与 /health 行为零变化）。
- 七工具（0020：只读已发布；0013：登记必须带正文；无 publish——发布只属于
  操作者治理台动作，ADR 0005）：知识四件 search_published / get_asset /
  register_asset / export_published 复用 services 层与客服同一套检索索引
  （0017）；活状态三件 get_product / get_stock / get_order_status（0057，
  第 99 刀）**执行入口就是客服 agent loop 的 TOOL_REGISTRY**——同一份参数
  白名单校验与执行函数，一致性由复用保证而非对齐维护。活状态三件只读
  （无写动作），订单结果出口过 ``_egress_order_result`` 脱敏（0057）。
  工具运行时凭 host app 引用走 deps 的同一惰性装配。
- stateless + json_response：本刀无通知/订阅需求，每请求独立会话对七工具
  只读场景最简（也免去外部客户端的会话粘性）。
"""

import secrets
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from suite_api.deps import ensure_engine, ensure_storage
from suite_api.models import Asset, AssetVersion, AuditLog, KnowledgeGap, Operator, Product
from suite_api.services import registration
from suite_api.services.agent_tools import TOOL_REGISTRY, _validate_args
from suite_api.services.asset_view import (
    VersionTextError,
    load_products,
    published_version_nos,
    read_version_text,
    to_asset_out,
)
from suite_api.services.catalog_tools import category_targets
from suite_api.services.knowledge_gaps import load_attachable_gap
from suite_api.services.machine_wash import QA_FIELD, redact
from suite_api.services.media import media_mime
from suite_api.services.retrieval import retrieve
from suite_api.services.stock_tools import match_product

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
    """确保系统操作者「mcp」行存在并返回 id（export 留痕的 operator 归属）。

    第 26 刀（审计刀 5 P1⑦）：check-then-insert 的并发窗口由 operators.username
    唯一约束兜底——首插撞 IntegrityError 时 SAVEPOINT 回滚插入再查已有行
    （先例 services/knowledge_gaps.record_refusal_gap）；不可 session.rollback()：
    会把同一次 export 尚未提交的留痕行一并丢掉。"""
    operator = session.scalar(select(Operator).where(Operator.username == MCP_OPERATOR_USERNAME))
    if operator is None:
        operator = Operator(
            username=MCP_OPERATOR_USERNAME, password_hash=_MCP_OPERATOR_UNLOGINABLE_HASH
        )
        try:
            with session.begin_nested():
                session.add(operator)
                session.flush()
        except IntegrityError:
            # SAVEPOINT 已回滚插入；未 expunge 则后续 SELECT 的 autoflush 会再插一次
            if operator in session:
                session.expunge(operator)
            operator = session.scalar(
                select(Operator).where(Operator.username == MCP_OPERATOR_USERNAME)
            )
            if operator is None:  # pragma: no cover - 唯一冲突者必已提交
                raise
    return operator.id


def _mask_title(title: str | None) -> str | None:
    """0038 修订补全（第 26 刀，审计刀 5 P1②）：MCP 响应 title 出口统一过
    redact。回流 title 已在源头收掩（routes/service._first_question，21 刀处置
    件 2），但**切片登记 title=transcript[:60] 裸转写**（services/clips.py）、
    素材/上传 title 非净源——出口侧统一兜底（双保险取一：源头逐个掩改动面大，
    出口一处收口与 content/chunk 同口径；redact 幂等，净 title 原样通过）。"""
    return redact(title) if title else title


def _mask_fields_map(fields: dict) -> dict:
    """0038 修订（第 21 刀评审处置件 1：MCP get_asset=第六出口）：字段映射表
    （extracted_fields / confirmed_fields）出 MCP 响应前统一过 redact——
    字符串 value 与 qa_pairs 每项 q/a；abstained 项与坏形状原样走（防御不
    改写形状）。新写入侧（机洗第 17 刀、人洗出口 2）已掩，这里是读侧对
    历史脏行的兜底收口，幂等无害；掩码不回写存储（字节不动）。
    title 出口收掩见 _mask_title（三工具统一，第 26 刀）。"""

    def _mask_value(name: str, value):  # noqa: ANN001, ANN202 - JSONB 回读三态
        if name == QA_FIELD and isinstance(value, list):
            return [
                {**p, "q": redact(p["q"]), "a": redact(p["a"])}
                if isinstance(p, dict)
                and isinstance(p.get("q"), str)
                and isinstance(p.get("a"), str)
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


# ---------- 活状态只读三工具（第 99 刀，ADR 0057）----------

# 订单 items/events 是 JSONB 自由形状：真实部署里快递/客服回执常把顾客电话/
# 邮箱直接塞进事件文本或条目键。当前 mock 单无 PII（seed 只有商品名与轨迹
# 文案），但 MCP 出口是跨进程边界——出口剥联系方式键 + 自由文本过 redact
#（0038「出口必掩」同纪律，幂等无害）。键名按小写比较（剥除是结构动作，
# 不是掩码：联系方式不该以任何形态出连接层，ADR 0057 升级路径见该 ADR）。
_ORDER_CONTACT_KEYS = frozenset(
    {"phone", "tel", "mobile", "email", "contact", "contact_phone", "contact_email"}
)


def _egress_order_entry(entry: object) -> object:
    """订单 items/events 单条出口形状：剥联系方式键、剩余字符串值过 redact。

    防御不改写形状：非 dict 条目原样走（0038 同精神——掩码出口不重排结构，
    外部 Agent 拿到的是与站内客服同构的数据，只少联系字段）。"""
    if not isinstance(entry, dict):
        return entry
    kept = {k: v for k, v in entry.items() if str(k).lower() not in _ORDER_CONTACT_KEYS}
    return {k: redact(v) if isinstance(v, str) else v for k, v in kept.items()}


def _egress_order_result(result: dict) -> dict:
    """get_order_status 的 MCP 出口脱敏（0057）：查无/故障形状原样走；命中单
    剥顶层与 items/events 内的联系方式键，自由文本（事件轨迹/商品名）过
    redact——顾客联系方式不出连接层。客服站内路径不走这层（操作者面另有
    出口掩口径），这里是连接层出口的边界收口。"""
    if not result.get("found") or result.get("error"):
        return result
    top = _egress_order_entry(result)
    if not isinstance(top, dict):  # pragma: no cover - 顶层恒为 dict，防御分支
        return result
    return {
        **top,
        "items": [_egress_order_entry(i) for i in result.get("items", [])],
        "events": [_egress_order_entry(e) for e in result.get("events", [])],
    }


def _spec_summary(product: Product) -> dict:
    """spec_values 出口摘要：``{字段: 值}``（丢来源/时间等治理元数据，外部
    Agent 只要事实值）；字符串值过 redact（0038：写回值可能混人工填的
    联系方式，出口必掩；幂等，净值原样通过）。"""
    summary: dict = {}
    for field, entry in dict(product.spec_values).items():
        if isinstance(entry, dict) and entry.get("value") is not None:
            value = entry["value"]
            summary[str(field)] = redact(value) if isinstance(value, str) else value
    return summary


def _category_product_summary(category: str, products: list[Product]) -> dict:
    """类目聚合视图（get_product 按类目名/别名查时）：件数、有货件数、已定价
    件数与价格区间。混币种不出区间（与 catalog_tools 类目报价同口径：跨币种
    比大小无意义，宁可少给也不给假区间）。"""
    members = [p for p in products if p.category == category]
    priced = [p for p in members if p.price_cents is not None]
    summary: dict = {
        "found": True,
        "category": category,
        "total": len(members),
        "in_stock": sum(1 for p in members if (p.stock or 0) > 0),
        "priced": len(priced),
    }
    currencies = {p.currency for p in priced}
    if priced and len(currencies) == 1:
        summary["currency"] = sorted(currencies)[0]
        summary["price_from_cents"] = min(p.price_cents for p in priced)
        summary["price_to_cents"] = max(p.price_cents for p in priced)
    return summary


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
            ok = secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
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
            "另有三件活状态只读工具（商品/库存/订单）——与站内客服同一套查询"
            "函数，同样只读：能查商品行价、库存与订单物流，不能改任何数据。"
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    def _db_session():
        ensure_engine(host)
        return host.state.session_factory()

    def _run_registry_tool(name: str, args: dict[str, str], question: str) -> dict:
        """活状态工具执行入口：客服 agent loop 的 TOOL_REGISTRY 同一条目——
        同一份参数白名单校验（``_validate_args``：白名单外键/坏格式一律拒绝）
        加同一个执行函数（get_stock 的纯度闸、订单号归一全在注册表侧，连接层
        不自建第二套校验——0057：一致性由复用保证）。参数不合法时以工具错误
        文案拒绝，与 register_asset 的 HTTP 语义转文案同精神。"""
        cleaned, reason = _validate_args(TOOL_REGISTRY[name], args)
        if reason is not None:
            raise ValueError(reason)
        with _db_session() as session:
            return TOOL_REGISTRY[name].run(session, cleaned, question)

    @mcp.tool()
    def search_published(query: str) -> list[dict]:
        """检索当前已发布的资产切块（与站内客服同一检索索引，只命中已发布）。

        返回 [{asset_id, version_no, title, chunk, score}]：chunk 是证据片段，
        score 越大越相关；未发布资产（已接入/待人洗）不会出现。空或纯虚词
        查询返回空列表。0038 修订补全（第 26 刀 P1②）：title 与 chunk 同为
        跨边界文本，出口统一过 redact（见 _mask_title）。
        """
        with _db_session() as session:
            hits = retrieve(session, query)
            asset_ids = {hit["asset_id"] for hit in hits}
            titles = (
                {
                    a.id: a.title
                    for a in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
                }
                if asset_ids
                else {}
            )
            return [{**hit, "title": _mask_title(titles.get(hit["asset_id"]))} for hit in hits]

    @mcp.tool()
    def get_asset(asset_id: int, version: int | None = None) -> dict:
        """取一份已发布资产的正文与元数据。

        version 不传 = 当前已发布版本（权威指针）；传版本号 = 取该历史版本，
        但仅当它发布过。已接入/待人洗/从未发布的版本一律拒绝（错误信息不含
        未发布内容）。返回 {id, title, kind, source_kind, version_no,
        content, extracted_fields, confirmed_fields, has_media}（第 120 刀起
        object_key 不再返回——键不出门；has_media=True 的资产可用
        get_asset_media 取媒体字节）。
        0038 修订（第 21 刀评审处置件 1）：content 与两张字段映射表以掩码
        形态出边界（MCP 响应=进程边界出口，出口必掩；对象字节不动）。
        """
        with _db_session() as session:
            asset = session.get(Asset, asset_id)
            if asset is not None and asset.discarded_at is not None:
                # 第 83 刀：废弃资产（0042 标记隐藏）不出现在证据面——与检索同口径
                raise ValueError(_UNPUBLISHED_MESSAGE)
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
            # 版本指针不动；与 export_published 同口径）；title 出口统一过
            # _mask_title（第 26 刀 P1②——切片 title=裸转写截断，源头非净源）。
            return {
                "id": asset.id,
                "title": _mask_title(asset.title),
                "kind": asset.kind,
                "source_kind": asset.source_kind,
                "version_no": asset_version.version_no,
                # 第 120 刀：object_key 不再出门（0052「键不出门」纪律——对象键是
                # 存储内部坐标，外部 Agent 拿到没有用途还扩泄漏面；图片/视频资产
                # 的媒体字节走 get_asset_media（base64，多模态 Agent 直接可消费）。
                "content": redact(content),
                "extracted_fields": _mask_fields_map(dict(asset_version.extracted_fields)),
                "confirmed_fields": _mask_fields_map(dict(asset_version.confirmed_fields)),
                "has_media": media_mime(asset.kind, asset_version.object_key) is not None,
            }

    @mcp.tool()
    def register_asset(
        content: str,
        title: str = "",
        product_id: int | None = None,
        knowledge_gap_id: int | None = None,
    ) -> dict:
        """登记一份文本文档进中台（必须带正文，空正文拒绝）。

        登记后资产状态为已接入（或机洗成功后待人洗），出现在治理台队列等待
        操作者人洗与发布；来源固定为连接层登记（mcp_registered）。本工具
        没有任何发布能力。knowledge_gap_id（可选，第 120 刀）=「补这份缺口」：
        挂上后操作者发布该资产时缺口自动解决（0024 同治理台语义；缺口须存在
        且 open 且未挂其他补文档）。返回登记后的资产视图 {id, title, kind,
        status, source_kind, product, last_error, current_published_version_no}。
        """
        if not content.strip():
            raise ValueError("登记必须带正文：只给标题的空壳登记被拒绝（ADR 0013）")
        try:
            with _db_session() as session:
                gap = None
                if knowledge_gap_id is not None:
                    gap = load_attachable_gap(session, knowledge_gap_id)
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
                if gap is not None:
                    gap.resolved_by_asset_id = asset.id
                session.commit()
                session.refresh(asset)
                out = to_asset_out(
                    asset, load_products(session, [asset]), published_version_nos(session, [asset])
                )
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
                .where(Asset.status == "published", Asset.discarded_at.is_(None))
                .order_by(Asset.id)
            ).all()
            exported = []
            for asset, asset_version in rows:
                try:
                    # 0038 修订：出口必掩（见上方 docstring；掩码不回写字节）
                    content = redact(read_version_text(session, storage, asset_version))
                except VersionTextError as exc:
                    # 第 94a 刀评审修：单份「图片无描述/视频无转写」不得带崩全量导出
                    # （46 刀 P0 同形状）——该资产照常出现在列表，正文给可行动的占用位。
                    content = f"（此版本暂无可读正文：{exc}）"
                exported.append(
                    {
                        "asset_id": asset.id,
                        "version_no": asset_version.version_no,
                        # 第 26 刀 P1②：title 与正文同过出口掩（三工具统一口径）
                        "title": _mask_title(asset.title),
                        "kind": asset.kind,
                        "source_kind": asset.source_kind,
                        "content": content,
                    }
                )
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

    # ---- 活状态只读三工具（第 99 刀，ADR 0057；客服同一套函数，无写动作）----
    # get_order_status / get_stock 执行入口=客服 TOOL_REGISTRY 的同名条目
    # （services/agent_tools._run_get_order_status / _run_get_stock）；get_product
    # 匹配复用客服目录同款 stock_tools.match_product + catalog_tools.
    # category_targets（LCS 部分名/类目与口语别名）。商品/订单是工具数据源
    # 不是中台对象（0002）：三工具读的是商品行与订单行的活状态，不经检索
    # 索引、不进治理台，与站内客服查到的是同一份数据同一套口径。

    @mcp.tool()
    def get_order_status(order_no: str) -> dict:
        """查一个订单的当前状态与物流轨迹（活状态只读，与站内客服同一查询）。

        order_no 格式 SO-数字（如 SO-1001，大小写不敏感自动归一）。命中返回
        {found, order_no, status, items, events}；查无 {found: False}。返回
        不含顾客联系方式：items/events 里的联系字段被剥除、自由文本过出口
        打码（ADR 0057 脱敏边界）。本工具只读。
        """
        result = _run_registry_tool("get_order_status", {"order_no": order_no}, "")
        return _egress_order_result(result)

    @mcp.tool()
    def get_stock(product_name: str) -> dict:
        """查一件商品（或一类商品）的当前库存（活状态只读，与站内客服同一查询）。

        product_name 是商品名（允许部分名，如「保温杯」命中「钛钢保温杯」）；
        传类目名或口语别名（如「笔记本电脑」「手机」）返回该类目聚合。命中
        单品 {found, product_name, stock}（stock=null 即未设置）；类目聚合
        {found, category, total, in_stock, stock_sum}；未匹配 {found: False}。
        本工具只读。
        """
        return _run_registry_tool("get_stock", {"product_name": product_name}, product_name)

    @mcp.tool()
    def get_product(name: str) -> dict:
        """按名查一件商品的行档案：价格、库存与已写回规格摘要（活状态只读）。

        匹配与站内客服目录同款：先类目（类目名或口语别名，如「笔记本电脑」
        「手机」，返回类目聚合 {found, category, total, in_stock, priced,
        price_from_cents, price_to_cents, currency}），后单品（部分名容错，
        「保温杯」命中「钛钢保温杯」，返回 {found, id, name, category,
        price_cents, currency, stock, spec_values}；spec_values 是 {字段: 值}
        摘要）。价格是商品行事实（可能为 null=未定价）。未匹配 {found:
        False}。本工具只读。
        """
        cleaned = (name or "").strip()
        if not cleaned:
            return {"found": False}
        with _db_session() as session:
            products = list(session.scalars(select(Product).order_by(Product.id)))
            # 先类目后单品（与客服目录报价/库存路径同序：商品名里含完整类目名
            # 时先走聚合，不被单件吞掉——审计刀 11 P1 的口径）。工具参数是裸
            # 名不是问句，无纯度闸：类目命中取精确等值（别名/类目名字面）。
            for token, category in category_targets({p.category for p in products}):
                if cleaned == token:
                    return _category_product_summary(category, products)
            product = match_product(cleaned, products)
            if product is None:
                return {"found": False}
            return {
                "found": True,
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "price_cents": product.price_cents,
                "currency": product.currency,
                "stock": product.stock,
                "spec_values": _spec_summary(product),
            }

    @mcp.tool()
    def list_knowledge_gaps(status: str = "open", limit: int = 20) -> list[dict]:
        """列出知识缺口（第 120 刀，运营飞轮入口，只读）。

        缺口=顾客被拒答后排队等补口径的待办（0024）。status=open（默认）看
        待补、resolved 看已解决；按被问热度降序。返回 [{gap_id, question,
        hit_count, product_id, status, created_at, resolved_by_asset_id}]——
        question 是顾客原问（出口已打码）。用法：看缺口 → register_asset(
        content=补的口径文档, knowledge_gap_id=该缺口) → 操作者在治理台人洗
        发布（发布时缺口自动解决）。发布权不在工具面（0001/0005）。
        """
        if status not in ("open", "resolved"):
            raise ValueError("status 只认 open/resolved")
        limit = max(1, min(limit, 50))
        with _db_session() as session:
            rows = session.execute(
                select(KnowledgeGap)
                .where(
                    (KnowledgeGap.status == status)
                    if status == "open"
                    else KnowledgeGap.status != "open"
                )
                .order_by(KnowledgeGap.hit_count.desc(), KnowledgeGap.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "gap_id": gap.id,
                    "question": redact(gap.question),
                    "hit_count": gap.hit_count,
                    "product_id": gap.product_id,
                    "status": gap.status,
                    "created_at": gap.created_at.isoformat() if gap.created_at else None,
                    "resolved_by_asset_id": gap.resolved_by_asset_id,
                }
                for (gap,) in rows
            ]

    @mcp.tool()
    def get_asset_media(asset_id: int, version: int | None = None) -> dict:
        """取一份已发布媒体资产的字节（第 120 刀，多模态消费面，只读）。

        图片/视频资产的媒体内容以 base64 返回（多模态 Agent 直接可消费）：
        {asset_id, version_no, mime, size_bytes, data_base64}。版本口径同
        get_asset（不传=当前已发布指针版，传版本号=历史已发布版）；非媒体
        资产或未发布一律拒绝（错误不含未发布内容）。文本资产请用 get_asset。
        """
        import base64

        with _db_session() as session:
            asset = session.get(Asset, asset_id)
            if asset is not None and asset.discarded_at is not None:
                raise ValueError(_UNPUBLISHED_MESSAGE)
            if version is None:
                pointer = asset.current_published_version_id if asset is not None else None
                if asset is None or pointer is None:
                    raise ValueError(_UNPUBLISHED_MESSAGE)
                asset_version = session.get(AssetVersion, pointer)
            else:
                asset_version = session.scalar(
                    select(AssetVersion).where(
                        AssetVersion.asset_id == asset_id,
                        AssetVersion.version_no == version,
                    )
                )
            if asset is None or asset_version is None or asset_version.published_at is None:
                raise ValueError(_UNPUBLISHED_MESSAGE)
            mime = media_mime(asset.kind, asset_version.object_key)
            if mime is None:
                raise ValueError("该资产不是可取字节的媒体（图片/视频）——文本正文请用 get_asset")
            media = ensure_storage(host).get_bytes(asset_version.object_key)
            return {
                "asset_id": asset.id,
                "version_no": asset_version.version_no,
                "mime": mime,
                "size_bytes": len(media),
                "data_base64": base64.b64encode(media).decode("ascii"),
            }

    streamable_app = mcp.streamable_http_app()
    # SDK 挂载约定：mounted 子应用 lifespan 不执行，session manager 交 host 代跑
    host.state.mcp_session_manager = mcp.session_manager
    return BearerGateMiddleware(streamable_app, settings)
