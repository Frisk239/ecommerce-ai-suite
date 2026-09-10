"""第 50 刀：多来源可见 + 演示价回填（products.source_kind 列 + 存量回填）。

## 为什么

演示库有四份真实数据集（Wikidata 91 商品 / OpenFoodFacts 20 商品+20 规格资产 /
在线购物评论 200 资产 / WANDS 30 切片候选），但**四条灌入路径都没在库里记来源**
——来源只活在脚本常量与标题命名里。资产侧于是 232 条全显示「上传」，商品侧连
来源列都没有。同时 115 件商品只有 3 件有价（ADR 0045 只给两个种子字面量），
「多少钱」与目录列举几乎无货可列。

## 做什么

1. 加列 `products.source_kind`（nullable；只读展示，表单不收该字段——来源是
   既成事实，可改就成可造假的溯源）。
2. 按**脚本写死的稳定形态**回填资产来源（只动 `source_kind='upload'` 的行，
   幂等可重跑）：
   - `title ~ '评论 ·'` -> `review_import`（200 条，`load_reviews.py` 的标题形态）
   - `title LIKE '%规格（OFF）'` -> `open_dataset`（20 条，`load_openfoodfacts.py`）
3. 回填商品来源：Wikidata（六类目且 `spec_schema='{}'`）、OFF（类目食品且净含量
   单字段模板）、WANDS 承载商品（按名）-> `open_dataset`；两条种子 -> `seed`。
4. 演示价回填：按**类目基准演示价**（`services/seed.py` 的 `CATEGORY_DEMO_PRICES`，
   本迁移是它的一次性快照）给 `price_cents IS NULL` 的行写价——**不覆盖手改价**，
   与第 41 刀同口径。数字是 **mock 演示价、非真实售价**（README 与 UI 如实标注）。

down 只回滚本次写的：两类资产来源回 `upload`、商品来源列清空、价格清回 NULL
（按「类目基准值」精确匹配，手工改过的价不会被误清）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 类目基准演示价（分）：与 services/seed.py 的 CATEGORY_DEMO_PRICES **同值**
# （迁移是一次性快照；改价请改 seed 表并新开迁移，不要改这里）
DEMO_PRICES = {
    "食品": 300,
    "器皿": 12900,
    "图书": 5900,
    "笔记本电脑": 499900,
    "智能手机": 299900,
    "平板电脑": 199900,
    "电视机": 349900,
    "洗衣机": 219900,
    "家具": 89900,
}

# Wikidata 导入的六类目（fetch_wikidata_products.py 的 CATEGORIES 常量）
WIKIDATA_CATEGORIES = (
    "智能手机",
    "笔记本电脑",
    "平板电脑",
    "电视机",
    "洗衣机",
    "图书",
)


def upgrade() -> None:
    op.add_column("products", sa.Column("source_kind", sa.String(20), nullable=True))
    conn = op.get_bind()

    # ---- 资产来源回填（只动 upload）----
    conn.execute(
        sa.text(
            """
            UPDATE assets SET source_kind = 'review_import'
            WHERE source_kind = 'upload' AND title LIKE '%评论 ·%'
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE assets SET source_kind = 'open_dataset'
            WHERE source_kind = 'upload' AND title LIKE '%规格（OFF）'
            """
        )
    )

    # ---- 商品来源回填 ----
    # 种子（两条具名）优先：先按名打 seed，再按类目打 open_dataset（否则种子会
    # 被类目条件吞进 open_dataset）
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'seed'
            WHERE name IN ('瓶装水', '钛钢保温杯')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'open_dataset'
            WHERE source_kind IS NULL
              AND category = ANY(:categories)
              AND spec_schema = '{}'::jsonb
            """
        ),
        {"categories": list(WIKIDATA_CATEGORIES)},
    )
    # OFF：类目食品 + 单字段净含量模板（20 件）
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'open_dataset'
            WHERE source_kind IS NULL
              AND category = '食品'
              AND spec_schema = '{"净含量": {"required": true}}'::jsonb
            """
        )
    )
    # WANDS 承载商品（load_wands_clips.py 建的演示商品）
    conn.execute(
        sa.text(
            """
            UPDATE products SET source_kind = 'open_dataset'
            WHERE source_kind IS NULL AND name = 'WANDS 家具（演示）'
            """
        )
    )

    # ---- 演示价回填（只写 NULL；不覆盖手改价）----
    for category, cents in DEMO_PRICES.items():
        conn.execute(
            sa.text(
                """
                UPDATE products SET price_cents = :cents
                WHERE category = :category AND price_cents IS NULL
                """
            ),
            {"cents": cents, "category": category},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE assets SET source_kind = 'upload' WHERE source_kind = 'review_import'")
    )
    conn.execute(
        sa.text("UPDATE assets SET source_kind = 'upload' WHERE source_kind = 'open_dataset'")
    )
    # 价格：只清「正好等于类目基准」的那批。**排除 seed**——种子商品在 0026 之前
    # 就有价（300/12900），它们恰好等于基准价，按值匹配会被本回滚误清（评审 P0：
    # 实测降级把两条种子价也清了，只剩 1 行有价）。
    # 残余风险（如实记）：非种子、迁移前就手改成本基准价的行仍会被清——downgrade
    # 无法再区分「本次写的」与「本来就是这个值」，这是单向回填的固有代价。
    for category, cents in DEMO_PRICES.items():
        conn.execute(
            sa.text(
                """
                UPDATE products SET price_cents = NULL
                WHERE category = :category AND price_cents = :cents
                  AND source_kind IS DISTINCT FROM 'seed'
                """
            ),
            {"cents": cents, "category": category},
        )
    op.drop_column("products", "source_kind")
