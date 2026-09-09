"""SQLAlchemy 2.0 声明式模型：ADR 0022/0023/0030 表的直接映射，不引入新数据语义。

列集合 = 各 ADR 映射表 + 工程运行所需的最小补充（assets.product_id/title/
last_error：挂商品、登记标题、机洗失败原因——均为任务授权的运行状态列）。
修订/回滚不加第四态或 in_flight 列：未发布版本用部分唯一索引约束（0004）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Operator(Base):
    """0016：单店一种操作者，无角色列。"""

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))


class Product(Base):
    """0002/0019：spec_schema 按类目给规格字段模板，spec_values 是写回值+来源。

    0037：stock 是商品上的字段语义（可空 int，NULL=库存未设置），mock 值由
    种子灌（演示店铺语境）——被库存工具与治理台商品列表只读自查（第 30 刀
    裁决：操作者面只读泄漏；不可编辑），不进检索/顾客面/MCP，不是中台对象
    （0002 库存不升格，与 orders 同口径）。
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    spec_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    spec_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Asset(Base):
    """0002 资产三态；0006 当前已发布版本指针（身份稳定，指针可前移）。"""

    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # 本刀仅 "document"
    # ingested=已接入 / pending_review=待人洗 / published=已发布
    status: Mapped[str] = mapped_column(String(20))
    # 0025 来源=资产进入中台的通道，登记端点语义定值（枚举校验在应用层），
    # 不让调用方自由填报；迁移 0003 对存量回填 'upload'
    source_kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(200))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    # 机洗失败原因（运行状态，非领域新语义）；成功时清空
    last_error: Mapped[str | None] = mapped_column(Text)
    # 0042 废弃标记（治理动作后的隐藏标记，不是第四态）：非空=已废弃，列表
    # 默认过滤；仅「已接入且从未发布」的失败资产可置，行不删、字节已清
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # use_alter：与 asset_versions.asset_id 构成循环外键，迁移里用 ALTER ADD FK
    current_published_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset_versions.id", use_alter=True, name="fk_assets_current_published_version")
    )


class AssetVersion(Base):
    """0006 一次不可变内容快照；0003 每版一把对象键；0009 弃权不冒充。

    行不可变指进入 published 后应用层禁止再 UPDATE（published_at 的写入
    正是「进入 published」这个动作本身）。同一资产同时最多一个未发布
    修订：部分唯一 UNIQUE (asset_id) WHERE published_at IS NULL。
    """

    __tablename__ = "asset_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "version_no", name="uq_asset_versions_asset_version"),
        Index("ix_asset_versions_asset_id", "asset_id"),
        Index(
            "uq_asset_versions_one_unpublished",
            "asset_id",
            unique=True,
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    version_no: Mapped[int] = mapped_column()
    object_key: Mapped[str] = mapped_column(String(500))
    # {字段: {value, source:"machine"}} 或 {字段: {abstained:true}}
    extracted_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    # {字段: {value, source:"human"}}，操作者确认/补填
    confirmed_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """0005/0016：谁/何时/对哪条资产哪一版做了 publish/confirm/rollback。append-only。"""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_asset_id", "asset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    version_no: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(20))  # "publish" | "confirm" | "rollback"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetrievalChunk(Base):
    """ADR 0023：已发布资产版本的切块（CONTEXT「检索索引」派生视图，不是中台对象）。

    只在发布事务内写入（0004/CONTEXT「已发布」词条：发布时切块入索引，
    索引里只有已发布）；发布事务外无写入路径。待人洗/已接入内容绝不出现在此表。
    """

    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        Index("ix_retrieval_chunks_asset_id", "asset_id"),
        Index("ix_retrieval_chunks_asset_id_version_no", "asset_id", "version_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    version_no: Mapped[int] = mapped_column()
    seq: Mapped[int] = mapped_column()  # 同一 (asset_id, version_no) 内的切块序号
    chunk: Mapped[str] = mapped_column(Text)


class ServiceSession(Base):
    """ADR 0023：客服会话=运行态实体，不是第三个中台对象（CONTEXT「会话」Avoid）。

    回流登记后 status=registered 并经 registered_asset_id 指向登记出的
    kind=dialogue 资产；closed_at 在会话终结（本刀=回流登记）时落值。
    """

    __tablename__ = "service_sessions"
    __table_args__ = (
        Index("ix_service_sessions_status", "status"),
        # 0021 顾客通道：令牌非空即顾客会话（不加 origin 列，token 本身就是判据）
        Index("uq_service_sessions_customer_token", "customer_token", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # active -> registered（结束即回流登记，一步完成，无仅结束态）
    status: Mapped[str] = mapped_column(String(20))
    registered_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    # 顾客会话令牌（ADR 0021/0033）：secrets.token_urlsafe(32) 存原文（spec 工程
    # 裁决：单店内部系统，DB 泄露不在威胁模型；hash 则无法按令牌直查）；操作者
    # 预览会话恒为 NULL——非空与否即顾客/操作者会话的唯一判据
    customer_token: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceMessage(Base):
    """ADR 0023 会话消息；引用带版本（0007），拒答/转人工显性（0018）。

    citations 仅 agent 消息携带 ``[{asset_id, version_no}]``；kind 是回答的
    属性（answer/refusal/handoff，0036：handoff=订单工具查无/故障的转人工，
    不连带缺口），customer 消息为 NULL；handoff 与 refusal 同消息
    显性标记（0018：无证据拒答时一并转人工）。同一会话旧消息的引用不随
    后续发布漂移（回放按当时版本，ADR 0023）。
    """

    __tablename__ = "service_messages"
    __table_args__ = (Index("ix_service_messages_session_id", "session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("service_sessions.id"))
    role: Mapped[str] = mapped_column(String(10))  # customer | agent
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    kind: Mapped[str | None] = mapped_column(
        String(10)
    )  # answer | refusal | handoff（仅 agent；handoff=工具失败转人工，0036）
    handoff: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # 0036 订单工具调用记录 {name, arg, result}（仅走工具路径的 agent 消息非空）：
    # 回放完整性——重载会话也要还原灰底工具条（与 gap_id 的「运行时返回」口径
    # 不同：工具条是已发生动作的留档，随消息落列，迁移 0007）
    tool: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    """ADR 0036：订单表=订单工具（get_order_status）的数据源，不是中台对象。

    只被引擎内的订单工具读：不进检索索引、不可登记/发布/引用/导出、不进治理
    台、无 MCP 路径，也不与 products/assets 产生任何外键语义（0002 订单不升
    格）。items=[{name, qty}]、events=[{at, text}]（按时间序）均为工具展示
    数据，种子灌 mock 单（演示店铺语境）。无任何对外 API。
    """

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("order_no", name="uq_orders_order_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32))  # SO-1001（唯一见上）
    status: Mapped[str] = mapped_column(String(20))  # 已发货 | 运输中 | 已签收 …
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialTask(Base):
    """ADR 0038：素材任务=素材中心自有的任务状态机（0012 不是中台对象）。

    status ∈ queued/running/pending_qc/registered/failed——失败不进中台
    （0029）：failed 不登记任何字节，registered 才有 asset_id 指向登记出的
    素材资产。title/content 是生成文案本体（抽检通过前只住本行，不入对象
    存储）；last_error 记规则项/生成失败/人工打回原因，重试时清空。
    """

    __tablename__ = "material_tasks"
    __table_args__ = (Index("ix_material_tasks_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(String(500))
    # 抽检通过登记出的资产（kind=material, source_kind=material_generated）；
    # UI 跳转治理台详情的锚，未登记为 NULL
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClipCandidate(Base):
    """ADR 0014/0039：直播切片候选=切片模块自有的种子 mock，不是中台对象。

    status ∈ pending/registered——单向状态机：拣选才切开字节登记
    （0014），已登记不可再拣选（409）。timecode_start/end 是源录像的时间码
    边界（HH:MM:SS 字符串）；transcript 是 ASR 转写 mock，登记字节 =
    「[start-end] 转写」文本（kind=video，非 mp4，0039——真视频切出留部署刀）。
    source_video_label 是源录像的名称字符串（mock 店铺语境，不存录像字节）。
    registered_asset_id 只在 registered 后指向登记出的视频资产（回执锚，
    UI 跳治理台的锚，同 MaterialTask.asset_id 先例）。
    """

    __tablename__ = "clip_candidates"
    __table_args__ = (Index("ix_clip_candidates_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(String(16))
    timecode_start: Mapped[str] = mapped_column(String(8))
    timecode_end: Mapped[str] = mapped_column(String(8))
    transcript: Mapped[str] = mapped_column(Text)
    source_video_label: Mapped[str] = mapped_column(String(120))
    # 拣选登记出的视频资产（kind=video、source_kind=clip_pick）；未拣选为 NULL
    registered_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoachRecord(Base):
    """ADR 0040：考核记录=销售考核模块自有，不是中台对象（0027 考核不是资产）。

    不能被检索、不能发布、不进治理台、无 MCP 触点。question_key 是题源锚
    {asset_id, version_no, source: qa|transcript, pair_index}（0007 引用口径：
    抽的是当时的已发布版本）；题目本身不落库，由 derive_questions 从已发布
    dialogue 动态推导，记录只存作答时刻的题面/标准答案快照。score 为三维分
    + 评语 {accurate, evidence, tone, comment}，NULL=未评分（LLM 未配置/失败/
    坏输出，无降级——打分是考核的本体），last_error 记原因可重评。
    model_name 是打分时刻的 settings.llm_model 快照（回放显示评分底座）。
    """

    __tablename__ = "coach_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_name: Mapped[str] = mapped_column(String(120))  # 单操作者 v1：受训者=操作者名
    question_key: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    question_text: Mapped[str] = mapped_column(Text)  # 题面快照（题可能随修订消失，回放要稳）
    standard_answer: Mapped[str | None] = mapped_column(Text)  # 转写兜底题无标准答案
    trainee_answer: Mapped[str] = mapped_column(Text)
    score: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_name: Mapped[str | None] = mapped_column(String(120))  # 成功打分时落底座名快照
    last_error: Mapped[str | None] = mapped_column(String(500))  # 未评分原因；重评成功清空
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpsRun(Base):
    """ADR 0041：运营 Agent 编排轨迹=运营模块自有数据，不是中台对象（0012 口径）。

    不能被检索、不能发布、不进治理台、无 MCP 触点。steps 是三步轨迹
    ``[{key,name,status,detail,via}]``（pending|running|done|failed，同步就地
    执行、逐步落库可观察）；output 是投放文案 ``{title,body,refs:[{asset_id,
    version_no}]}``，refs 冻结 compose 时刻的当前已发布指针版本（0007）。
    delivered_at 非空=已投放——那是渠道动作（mock），不改任何资产三态
    （词条「发布」_Avoid_「投放发布」钉死），已投放不可再投放。
    """

    __tablename__ = "ops_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeGap(Base):
    """ADR 0024/0030：知识缺口=无证据拒答留下的待补项，可挂商品。

    不是资产：不能检索、不能发布，也没有手动关闭端点——产生只随 refusal
    （第 30 刀起归一化幂等：同归一化问且 open 复用，services/knowledge_gaps.
    normalize_question），解决只随发布（发布事务内置 resolved）。补文档仍是
    普通登记：登记时 assets.register 带 knowledge_gap_id 即把 resolved_by_asset_id
    指向该资产（原型 fillsGapId 语义，缺口此时仍 open），发布事务内才置
    resolved + resolved_at。
    """

    __tablename__ = "knowledge_gaps"
    __table_args__ = (
        Index("ix_knowledge_gaps_status_created_at", "status", "created_at"),
        # 0024/0030/0031 幂等工程收口（第 30 刀起键=归一化问）：open 同归一化问
        # 唯一；resolved 同文仍可并存。question 原问列只做展示，不进查重键。
        Index(
            "uq_knowledge_gaps_open_normalized",
            "normalized_question",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)  # 顾客原问（strip 后与 customer 消息同文）
    # 归一化问（第 30 刀查重键）：新行恒有值；迁移前的存量 resolved 行保持
    # NULL 不回填（resolved 不参与查重，历史行不动）。
    normalized_question: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'open'")
    )  # open | resolved
    resolved_by_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
