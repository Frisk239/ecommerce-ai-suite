"""存量回填测试（0002 -> 0003 的 source_kind 回填）：conftest 直接 upgrade head，
不覆盖「旧库升级回填」路径，本文件单独钉死。

路径：独立测试库上 upgrade head -> downgrade -1 回到 0002（assets 还没有
source_kind 列的旧结构）-> 裸 SQL 插一行 assets（存量旧数据）-> upgrade head
-> 该行 source_kind == 'upload'（0003：带 server_default 加 NOT NULL 列即回填
旧行，随后摘掉默认逼新行显式给值）。需真 Postgres（SUITE_TEST_DATABASE_URL），
未设则 skip——与 conftest 同模式；自建独立库（后缀区分）自清理，不与 api
fixture 的 module 级库互踩。

第 30 刀追加：0015 归一化幂等回填（normalized_question 列 + open 行回填 +
归一化重复 open 删重留最小 id + 部分唯一索引重建 + resolved 历史行不动）。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from suite_api.db import to_sqlalchemy_url
from suite_api.services.knowledge_gaps import normalize_question

_REQUIRED_ENV = "SUITE_TEST_DATABASE_URL"
# apps/api 根（src 布局：tests/test_x.py -> tests -> apps/api）
_API_ROOT = Path(__file__).resolve().parents[1]

_LEGACY_ASSET_TITLE = "旧结构存量资产"


def _alembic_config(database_url: str) -> Config:
    """与 main.py _run_migrations 同法：程序化注入 script_location 与连接串。"""
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    return cfg


@pytest.fixture()
def backfill_db_url() -> Iterator[str]:
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5432/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = f"{parts.path.strip('/')}_backfill"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    # 自建独立库：先清场再建，测试结束自清理
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    yield urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def test_upgrade_0003_backfills_legacy_assets_source_kind(backfill_db_url: str) -> None:
    cfg = _alembic_config(backfill_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")  # 回到 0002：assets 尚无 source_kind 列（head 可能已过 0003）

    # 0002 旧结构裸 SQL 插一行存量资产（无 source_kind 可填——列不存在）
    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assets (kind, status, title) VALUES ('document', 'ingested', %s)",
            (_LEGACY_ASSET_TITLE,),
        )

    command.upgrade(cfg, "head")  # 0003：加列并回填存量行

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_kind FROM assets WHERE title = %s", (_LEGACY_ASSET_TITLE,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "upload"


# 第 41 刀（价格列+产品档）：0018 在旧结构 products 行上加列——price_cents
# 保持 NULL（未定价，演示价由 seed 回填）、currency 存量回填 'CNY'；
# audit_log 加 product_id 列、asset_id/version_no 放开 NOT NULL（改价产品档）。
def test_upgrade_0018_adds_price_and_product_audit(backfill_db_url: str) -> None:
    cfg = _alembic_config(backfill_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0017")  # 回到 0017：products 尚无价格列

    # 旧结构裸 SQL 插一行存量商品（无价格列可填——列不存在）
    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (name, category, spec_schema, spec_values)"
            " VALUES ('旧结构存量商品', '食品', '{}', '{}')"
        )

    # **升到 0018 为止**（不是 head）：本用例钉的是 0018 的契约「只加列不写价」；
    # 后来第 50 刀（0026）会按类目基准回填演示价，升到 head 会把这条断言变成
    # 在验 0026（0026 自己的回填另有 test_upgrade_0026_... 专测）
    command.upgrade(cfg, "0018")

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT price_cents, currency FROM products WHERE name = '旧结构存量商品'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] is None  # 存量行保持 NULL=未定价（演示价走 seed 回填）
        assert row[1] == "CNY"  # server_default 加 NOT NULL 列即回填旧行
        # 产品档可写：asset 侧两列 NULL + product_id 指向商品
        cur.execute("SELECT id FROM products WHERE name = '旧结构存量商品'")
        product_id = cur.fetchone()[0]
        cur.execute("INSERT INTO operators (username, password_hash) VALUES ('op41', 'x')")
        cur.execute("SELECT id FROM operators WHERE username = 'op41'")
        operator_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO audit_log (operator_id, asset_id, version_no, action, product_id)"
            " VALUES (%s, NULL, NULL, 'price_change', %s)",
            (operator_id, product_id),
        )
        cur.execute(
            "SELECT action, product_id, asset_id FROM audit_log WHERE action = 'price_change'"
        )
        audit_row = cur.fetchone()
        assert audit_row == ("price_change", product_id, None)


# 第 30 刀（归一化幂等）：0015 回填口径。旧结构（0014）下 question 精确唯一
# 允许「…兑换？」与「…兑换」两条 open 并存（走查 G-0001/G-0003 实证形态）；
# upgrade 后：open 行回填 normalized_question（SQL 与应用层 normalize_question
# 同口径）、归一化重复 open 留 id 最小一条、resolved 历史行不动（不回填）、
# 部分唯一索引换到 normalized_question。
def test_upgrade_0015_backfills_normalized_and_dedupes_open_gaps(backfill_db_url: str) -> None:
    cfg = _alembic_config(backfill_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0014")  # 回到 0014：knowledge_gaps 尚无 normalized_question

    # 旧结构裸 SQL：两条归一化相同（差一个尾问号）的 open + 一条带标点尾巴的
    # resolved（历史行）+ 一条含全角标点的 open（钉 SQL translate 与应用层同口径）
    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO knowledge_gaps (question, status) VALUES (%s, 'open')",
            ("会员积分怎么兑换？",),
        )
        cur.execute(
            "INSERT INTO knowledge_gaps (question, status) VALUES (%s, 'open')",
            ("会员积分怎么兑换",),
        )
        cur.execute(
            "INSERT INTO knowledge_gaps (question, status) VALUES (%s, 'resolved')",
            ("旧版积分规则什么时候生效。",),
        )
        cur.execute(
            "INSERT INTO knowledge_gaps (question, status) VALUES (%s, 'open')",
            ("以旧换新（补贴）怎么领？",),
        )
        cur.execute("SELECT id FROM knowledge_gaps WHERE question = '会员积分怎么兑换？'")
        kept_open_id = cur.fetchone()[0]
        cur.execute("SELECT id, question FROM knowledge_gaps WHERE status = 'resolved'")
        resolved_id, resolved_question = cur.fetchone()

    command.upgrade(cfg, "head")  # 0015：加列 + open 回填 + 删重 + 索引重建

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        # 归一化重复的 open 只留 id 最小一条；留行原问不动、normalized 为归一化值
        cur.execute("SELECT id, question, normalized_question FROM knowledge_gaps ORDER BY id")
        rows = cur.fetchall()
        dup_rows = [r for r in rows if r[2] == "会员积分怎么兑换"]
        assert len(dup_rows) == 1
        assert dup_rows[0][0] == kept_open_id  # 留最小 id（先例 0006 删重）
        assert dup_rows[0][1] == "会员积分怎么兑换？"  # 原问列不动
        # SQL 回填与应用层 normalize_question 同口径（含全角标点转换）
        assert dup_rows[0][2] == normalize_question("会员积分怎么兑换？")
        punct_rows = [r for r in rows if r[1] == "以旧换新（补贴）怎么领？"]
        assert len(punct_rows) == 1
        assert punct_rows[0][2] == normalize_question("以旧换新（补贴）怎么领？")
        # resolved 历史行不动：question 原文、normalized 不回填（NULL）
        resolved_row = next(r for r in rows if r[0] == resolved_id)
        assert resolved_row[1] == resolved_question == "旧版积分规则什么时候生效。"
        assert resolved_row[2] is None
        # 索引口径：0006 的 question 部分唯一已删，0015 的 normalized 部分唯一在位
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_gaps'")
        index_names = {r[0] for r in cur.fetchall()}
        assert "uq_knowledge_gaps_open_question" not in index_names
        assert "uq_knowledge_gaps_open_normalized" in index_names


# 第 50 刀（多来源可见 + 演示价回填）：0026 在旧结构（0025 之前）上按稳定形态回填
# ——评论资产 -> review_import、OFF 规格资产 -> open_dataset、商品来源列 + 类目
# 基准演示价（只写 NULL）。downgrade 把两类来源还原为 upload、价格清回 NULL。
def test_upgrade_0026_backfills_sources_and_demo_prices(backfill_db_url: str) -> None:
    cfg = _alembic_config(backfill_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0025")  # 回到 0025：products 尚无 source_kind；价/来源未回填

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (name, category, spec_schema, spec_values, price_cents)"
            " VALUES ('旧结构评论载体', '图书', '{}', '{}', NULL),"
            "        ('旧结构已定价', '图书', '{}', '{}', 12345),"
            # 种子形态（名在种子表里 + 价恰好等于类目基准）：降级时**不许被清**
            # ——评审 P0：按值匹配的回滚会把 0026 之前就存在的种子价一起清掉
            "        ('瓶装水', '食品', '{}', '{}', 300)"
        )
        cur.execute(
            "INSERT INTO assets (kind, status, source_kind, title)"
            " VALUES ('document', 'pending_review', 'upload', '图书评论 · 这本旧结构书还行'),"
            "        ('document', 'pending_review', 'upload', '旧结构杯 规格（OFF）'),"
            "        ('document', 'pending_review', 'session_backflow', '图书评论 · 回流的别动')"
        )

    # **升到 0026 为止**（不是 head）：本用例钉的是 0026 的回填口径；第 55 刀的
    # 0028 会把 open_dataset 再拆成 openfoodfacts/wikidata/wands（那由 0028 自己的
    # 用例钉——升到 head 会把这条断言变成在验 0028）
    command.upgrade(cfg, "0026")

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT title, source_kind FROM assets WHERE title LIKE '旧结构%' OR title LIKE '%旧结构书%'"
        )
        rows = {title: kind for title, kind in cur.fetchall()}
        assert rows["图书评论 · 这本旧结构书还行"] == "review_import"
        assert rows["旧结构杯 规格（OFF）"] == "open_dataset"
        # 只动 upload：已是 session_backflow 的评论形态资产不被改写
        cur.execute("SELECT source_kind FROM assets WHERE title = '图书评论 · 回流的别动'")
        assert cur.fetchone()[0] == "session_backflow"

        # 商品：NULL 价按类目基准回填（图书 5900）；手改价（12345）不被覆盖
        cur.execute(
            "SELECT name, price_cents, source_kind FROM products WHERE name LIKE '旧结构%'"
        )
        products = {name: (cents, src) for name, cents, src in cur.fetchall()}
        assert products["旧结构评论载体"][0] == 5900
        assert products["旧结构已定价"][0] == 12345  # 不覆盖
        assert products["旧结构评论载体"][1] == "open_dataset"  # 六类目 + 空模板

    # downgrade：来源还原、价格清回（只清正好等于基准价的那批）
    command.downgrade(cfg, "0025")
    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM assets WHERE source_kind IN ('review_import', 'open_dataset')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM products WHERE price_cents = 5900")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM products WHERE price_cents = 12345")
        assert cur.fetchone()[0] == 1  # 手改价原样
        # 种子价在降级后仍在（评审 P0 的钉子：按值匹配会误清它）
        cur.execute("SELECT price_cents FROM products WHERE name = '瓶装水'")
        assert cur.fetchone()[0] == 300


# 第 55 刀（四份数据集逐个可见）：0028 把 0026 标的 open_dataset 按形态拆成
# wikidata / openfoodfacts / wands；down 原样收回。
def test_upgrade_0028_splits_open_dataset_by_shape(backfill_db_url: str) -> None:
    cfg = _alembic_config(backfill_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0026")  # 回到 0026：来源词还没有数据集细分

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (name, category, spec_schema, spec_values, source_kind)"
            " VALUES ('旧Wikidata机', '笔记本电脑', '{}', '{}', 'open_dataset'),"
            "        ('旧OFF食品', '食品', '{\"净含量\": {\"required\": true}}', '{}',"
            "         'open_dataset'),"
            "        ('WANDS 家具（演示）', '家具', '{}', '{}', 'open_dataset'),"
            "        ('手建商品', '家具', '{}', '{}', NULL)"
        )
        cur.execute(
            "INSERT INTO assets (kind, status, source_kind, title)"
            " VALUES ('document', 'pending_review', 'open_dataset', '旧结构杯 规格（OFF）'),"
            "        ('document', 'pending_review', 'upload', '旧结构普通文档')"
        )

    command.upgrade(cfg, "head")

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, source_kind FROM products WHERE name LIKE '旧%' OR name LIKE 'WANDS%'"
            " OR name = '手建商品'"
        )
        kinds = {name: kind for name, kind in cur.fetchall()}
        assert kinds["旧Wikidata机"] == "wikidata"
        assert kinds["旧OFF食品"] == "openfoodfacts"
        assert kinds["WANDS 家具（演示）"] == "wands"
        assert kinds["手建商品"] is None  # 手建不动
        cur.execute("SELECT source_kind FROM assets WHERE title LIKE '旧结构%' ORDER BY id")
        kinds = [row[0] for row in cur.fetchall()]
        assert kinds == ["openfoodfacts", "upload"]  # 只动 open_dataset 的那条

    command.downgrade(cfg, "0027")
    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM products WHERE source_kind IN ('wikidata','openfoodfacts','wands')"
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM products WHERE source_kind = 'open_dataset'")
        assert cur.fetchone()[0] == 3
