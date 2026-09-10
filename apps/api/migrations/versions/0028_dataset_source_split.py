"""第 55 刀：四份真实数据集在产品面逐个可见（open_dataset 拆细）。

第 50 刀给四份数据集用了两个词（`review_import` / `open_dataset`）——审计刀 10 指出
**Wikidata / OpenFoodFacts / WANDS 三者在界面上同叫「开放数据集」分不出是哪一份**。
本迁移按与 0026 相同（同源、可重复）的稳定形态把 `open_dataset` 进一步拆成
`wikidata` / `openfoodfacts` / `wands`；`open_dataset` 保留为**通用类**（将来又接
数据集时先用它兜底），本迁移后库里不再有该值的行。

只动 `source_kind='open_dataset'` 的行（幂等、可重复跑）；down 原样收回
`open_dataset`。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WIKIDATA_CATEGORIES = (
    "智能手机",
    "笔记本电脑",
    "平板电脑",
    "电视机",
    "洗衣机",
    "图书",
)
DATASET_VALUES = ("wikidata", "openfoodfacts", "wands")


def upgrade() -> None:
    conn = op.get_bind()
    # 资产：OFF 规格资产（0026 按标题形态标的 open_dataset）
    conn.execute(
        sa.text(
            """
            UPDATE assets SET source_kind = 'openfoodfacts'
            WHERE source_kind = 'open_dataset' AND title LIKE '%规格（OFF）'
            """
        )
    )
    # 商品：WANDS 承载商品（按名，先判——它也在「家具」类目里）
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'wands'
            WHERE source_kind = 'open_dataset' AND name = 'WANDS 家具（演示）'
            """
        )
    )
    # 商品：Wikidata（六类目 + 空 spec_schema）
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'wikidata'
            WHERE source_kind = 'open_dataset'
              AND category = ANY(:categories)
              AND spec_schema = '{}'::jsonb
            """
        ),
        {"categories": list(WIKIDATA_CATEGORIES)},
    )
    # 商品：OFF（食品 + 单字段净含量模板）
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'openfoodfacts'
            WHERE source_kind = 'open_dataset'
              AND category = '食品'
              AND spec_schema = '{"净含量": {"required": true}}'::jsonb
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE assets SET source_kind = 'open_dataset' WHERE source_kind = 'openfoodfacts'"
        )
    )
    placeholders = ", ".join(f":v{i}" for i in range(len(DATASET_VALUES)))
    conn.execute(
        sa.text(
            f"UPDATE products SET source_kind = 'open_dataset' WHERE source_kind IN ({placeholders})"
        ),
        {f"v{i}": value for i, value in enumerate(DATASET_VALUES)},
    )
