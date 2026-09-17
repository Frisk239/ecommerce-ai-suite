"""第 115 刀（W15a 美化产品图）：material_tasks.image_reference_asset_id。

素材任务的「美化产品图」改为基于**真实商品图**的图像编辑（imggen.edit_image，
商品主体来自原图）——本列记它所基于的那份图片资产（已发布优先解析），是
「配图=真实商品图的美化版」的血缘锚：UI 展示「基于 A-xxxx 美化生成」、治理
可追溯。文字卡流水线（W15b）/文生图背景/跳过态为 NULL。

down：删列（该列只是锚，无下游消费依赖）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "material_tasks",
        sa.Column("image_reference_asset_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_material_tasks_image_reference_asset",
        "material_tasks",
        "assets",
        ["image_reference_asset_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_material_tasks_image_reference_asset", "material_tasks", type_="foreignkey"
    )
    op.drop_column("material_tasks", "image_reference_asset_id")
