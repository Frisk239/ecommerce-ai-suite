"""第 46 刀：clip_recordings 表 + clip_candidates.recording_id（直播切片真链路）。

源录像不是中台对象（ADR 0014 同候选：切片模块自有），只是拣选时 ffmpeg 的
输入源与溯源锚：label（上传文件名）/object_key（recordings/ 前缀）/size_bytes/
created_at。**不进检索、不能发布、不进治理台**——故不加任何资产语义列。

clip_candidates.recording_id nullable FK：上传即把「尚无源录像」的 pending 候选
绑到这份录像（裁决 2）；NULL=无上传，拣选退回既有时间码文本路径（裁决 3）。

down 删列再删表——录像字节留在对象存储成为孤儿（同 0009/0020 纪律：非中台
对象无数据迁移负担）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clip_recordings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "clip_candidates",
        sa.Column("recording_id", sa.Integer(), sa.ForeignKey("clip_recordings.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clip_candidates", "recording_id")
    op.drop_table("clip_recordings")
