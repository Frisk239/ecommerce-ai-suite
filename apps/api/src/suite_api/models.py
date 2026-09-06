"""SQLAlchemy 2.0 声明式模型：ADR 0022 五表的直接映射，不引入新数据语义。

列集合 = ADR 0022 映射表 + 工程运行所需的最小补充（assets.product_id/title/
last_error：挂商品、登记标题、机洗失败原因——均为任务授权的运行状态列）。
修订/回滚不预埋列（ADR 0022 明令禁止 speculative generality）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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
    """0002/0019：spec_schema 按类目给规格字段模板，spec_values 是写回值+来源。"""

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


class Asset(Base):
    """0002 资产三态；0006 当前已发布版本指针（身份稳定，指针可前移）。"""

    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # 本刀仅 "document"
    # ingested=已接入 / pending_review=待人洗 / published=已发布
    status: Mapped[str] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(200))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    # 机洗失败原因（运行状态，非领域新语义）；成功时清空
    last_error: Mapped[str | None] = mapped_column(Text)
    # use_alter：与 asset_versions.asset_id 构成循环外键，迁移里用 ALTER ADD FK
    current_published_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset_versions.id", use_alter=True, name="fk_assets_current_published_version")
    )


class AssetVersion(Base):
    """0006 一次不可变内容快照；0003 每版一把对象键；0009 弃权不冒充。

    行不可变指进入 published 后应用层禁止再 UPDATE（本刀即全部字段，
    published_at 的写入正是「进入 published」这个动作本身）。
    """

    __tablename__ = "asset_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "version_no", name="uq_asset_versions_asset_version"),
        Index("ix_asset_versions_asset_id", "asset_id"),
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
    """0005/0016：谁/何时/对哪条资产哪一版做了 publish/confirm。append-only。"""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_asset_id", "asset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    version_no: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(20))  # "publish" | "confirm"
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
    __table_args__ = (Index("ix_service_sessions_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # active -> registered（结束即回流登记，一步完成，无仅结束态）
    status: Mapped[str] = mapped_column(String(20))
    registered_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceMessage(Base):
    """ADR 0023 会话消息；引用带版本（0007），拒答/转人工显性（0018）。

    citations 仅 agent 消息携带 ``[{asset_id, version_no}]``；kind 是回答的
    属性（answer/refusal），customer 消息为 NULL；handoff 与 refusal 同消息
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
    kind: Mapped[str | None] = mapped_column(String(10))  # answer | refusal（仅 agent）
    handoff: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
