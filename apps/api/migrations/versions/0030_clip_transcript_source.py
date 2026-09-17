"""第 93 刀：clip_candidates.transcript_source + product_id 放开 NOT NULL（ADR 0050）。

``transcript_source``（String(10)，**无 CHECK**——仓库风格同 0023/0029：取值域由
应用层常量（``services/asr.CLOUD/LOCAL/MANUAL``）收口，不加数据库约束）：

- ``cloud`` = 云转写端点产生（本刀起 `POST /api/clips/recordings/{id}/transcribe`）；
- ``local`` = 本地兜底脚本 ``scripts/transcribe_local.py`` 写回（不进 api 镜像）；
- ``manual`` = **非 ASR 通道**产生的转写（人工填写、种子 mock、WANDS 导入自带文本）。

**存量回填口径：一律 'manual'** —— 本列引入之前的所有候选都不是 ASR 通道产生的
（含 WANDS 数据自带英文转写）；来源是既成事实，不做「猜测性升级」。加列带
server_default 'manual'（此后非 ASR 路径的插入自动落该值），再显式 UPDATE 一遍
把两件事都写死（加列回填 + 显式口径，互不依赖）。

``product_id`` 放开 NOT NULL：云转写按录像整段生成候选，句子里没有商品归属——
归属是人/治理动作，不编造（候选照常拣选，登记出的资产 product_id 为空）。
存量的种子/WANDS 候选都有商品，不受影响。

downgrade：删列 + 恢复 product_id NOT NULL——**若库里已有未归属候选则恢复失败**
（属预期，同 0018 口径：不能凭空给候选编一个商品；届时先清这些候选或先补归属）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clip_candidates",
        sa.Column("transcript_source", sa.String(10), nullable=True),
    )
    # 存量口径：非 ASR 通道产出（含 WANDS 自带转写）——一律 manual
    op.execute(
        "UPDATE clip_candidates SET transcript_source = 'manual'"
        " WHERE transcript_source IS NULL"
    )
    op.alter_column(
        "clip_candidates",
        "transcript_source",
        existing_type=sa.String(10),
        nullable=False,
        server_default="manual",
    )
    op.alter_column(
        "clip_candidates", "product_id", existing_type=sa.Integer(), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "clip_candidates", "product_id", existing_type=sa.Integer(), nullable=False
    )
    op.drop_column("clip_candidates", "transcript_source")
