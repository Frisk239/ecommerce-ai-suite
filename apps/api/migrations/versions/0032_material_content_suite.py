"""第 98 刀：material_tasks 内容模板/双闸/配图五列（ADR 0055）。

自媒体内容套件的状态面扩展（全部带 server_default，存量行不回填即默认值）：

- ``template``：内容模板键（station=站内投放文案/xhs=小红书笔记体/
  short_video=短视频口播稿）。存量行回填 'station'——第 17 刀起的既有任务
  本就是站内卖点文案（模板概念的默认形态），不是编造。
- ``qc_llm_passed``：LLM 事实性质检二道闸结果。存量 NULL=未跑到（历史任务
  只有规则闸+人抽检，不倒填 True 冒充「LLM 质检过」）——与规则闸独立记录。
- ``image_status``：配图步状态。存量 'none'（历史任务没有配图请求）。
  取值 none/requested/pending/registered/skipped_no_key/failed——``requested``
  兼作建任务的 with_image 请求标志（无独立列），由 services/material 收口。
- ``image_object_key``：配图字节的对象存储暂存键（material/ 前缀，抽检通过
  前不是资产——同 clip_recordings 先例；登记后删除）。
- ``image_asset_id``：抽检通过登记出的配图图片资产回执锚。

downgrade：删五列即回滚（image_object_key 指向的暂存字节由应用层在登记/
放弃路径清理，迁移不碰对象存储）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "material_tasks",
        sa.Column("template", sa.String(length=20), nullable=False, server_default="station"),
    )
    op.add_column("material_tasks", sa.Column("qc_llm_passed", sa.Boolean(), nullable=True))
    op.add_column(
        "material_tasks",
        sa.Column("image_status", sa.String(length=20), nullable=False, server_default="none"),
    )
    op.add_column(
        "material_tasks", sa.Column("image_object_key", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "material_tasks",
        sa.Column(
            "image_asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("material_tasks", "image_asset_id")
    op.drop_column("material_tasks", "image_object_key")
    op.drop_column("material_tasks", "image_status")
    op.drop_column("material_tasks", "qc_llm_passed")
    op.drop_column("material_tasks", "template")
