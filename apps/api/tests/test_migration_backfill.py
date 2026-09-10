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

    command.upgrade(cfg, "head")  # 0018：加列并回填存量行

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
