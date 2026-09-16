"""OFF 错配订正脚本的「直写库」边界测试（第 100 刀；真 PG）。

``correct_off_mismatch`` 与 93 刀的 ``fixup_asset_sources`` 同族：**脚本直写库**
（UPDATE assets.title + 词表外 action 的 audit 手动备注行）。这里钉住它的边界：

- 只圈**已发布且指针非空且来源 openfoodfacts 且标题带「规格（OFF）」后缀**的行——
  同标题形态的非 OFF 来源、未发布的 OFF 行，一行不动；
- 订正后标题 = 正文「品牌：X」块值 + 后缀，且**指针/块/版本零改动**（块不含标题、
  79 刀亲和实时读 assets.title——改列即改亲和，这是「无需重发布」定案的钉测）；
- audit 手动备注行：action='title_correct'、version_no NULL、种子操作者；
- 幂等：重跑诊断无 mismatch、apply 0 行、audit 不再新增（判据自洽：订正后
  subject=brand 判 match）。

未设 SUITE_TEST_DATABASE_URL 时随 ``api`` 夹具 skip（与其它集成用例同口径）。
"""

import os
import sys
from pathlib import Path

REALDATA_DIR = Path(__file__).resolve().parents[3] / "scripts" / "realdata"
sys.path.insert(0, str(REALDATA_DIR))

import correct_off_mismatch as com  # noqa: E402

_URL_ENV = "SUITE_TEST_DATABASE_URL"


def _seed_rows(url: str) -> None:
    """最小真库种子：三行资产 + 各自指针版本/检索块（psycopg 直插，绕过发布事务）。

    - 901：OFF 已发布、标题错配（M&M white / 块品牌 Fitpiggy）——该订正；
    - 902：OFF 已发布、标题匹配（xxx）——不动；
    - 903：upload 来源、同标题形态——圈外不动（fixup_asset_sources 同款边界）；
    - 904：OFF 待人洗（无指针无块）——圈外不动（没有已发布证据可判）。
    """
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        rows = [
            # (id, status, source_kind, title, brand_chunk)
            (901, "published", "openfoodfacts", "M&M white 规格（OFF）", "品牌：Fitpiggy"),
            (902, "published", "openfoodfacts", "xxx 规格（OFF）", "品牌：xxx"),
            (903, "published", "upload", "偷渡 规格（OFF）", "品牌：别人家"),
            (904, "pending_review", "openfoodfacts", "待人洗 规格（OFF）", None),
        ]
        for asset_id, status, source_kind, title, brand in rows:
            cur.execute(
                "INSERT INTO assets (id, kind, status, source_kind, title)"
                " VALUES (%s, 'document', %s, %s, %s)",
                (asset_id, status, source_kind, title),
            )
            cur.execute(
                "INSERT INTO asset_versions (asset_id, version_no, object_key, published_at)"
                " VALUES (%s, 1, %s, CASE WHEN %s THEN now() ELSE NULL END) RETURNING id",
                (asset_id, f"objects/{asset_id}/v1.txt", status == "published"),
            )
            version_row_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE assets SET current_published_version_id = %s"
                " WHERE id = %s AND status = 'published'",
                (version_row_id, asset_id),
            )
            chunks = ["条码：000000000063", "净含量：80 gram"]
            if brand:
                chunks.append(brand)
            for seq, chunk in enumerate(chunks):
                cur.execute(
                    "INSERT INTO retrieval_chunks (asset_id, version_no, seq, chunk)"
                    " VALUES (%s, 1, %s, %s)",
                    (asset_id, seq, chunk),
                )


def test_off_correction_bounds_audit_and_idempotency(api) -> None:
    client, _ = api
    del client  # 只为拿「真 PG 已就绪 + 迁移种子」的夹具（本用例走脚本函数直连库）
    url = os.environ[_URL_ENV]
    _seed_rows(url)

    from sqlalchemy import create_engine, select, text
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Asset, AssetVersion, AuditLog, RetrievalChunk

    engine = create_engine(to_sqlalchemy_url(url))
    with sessionmaker(bind=engine)() as db:
        rows = com.diagnose(com.load_off_assets(db))
        by_id = {row["asset_id"]: row for row in rows}
        # 圈行：901/902 进诊断（903 非 OFF 来源、904 未发布——不进）
        assert sorted(by_id) == [901, 902]
        assert by_id[901]["verdict"] == com.VERDICT_MISMATCH
        assert by_id[902]["verdict"] == com.VERDICT_MATCH

        changed = com.apply_corrections(db, rows)
        db.commit()
        assert changed == 1

        # 订正后：标题=正文品牌+后缀；指针/版本/块零改动（无需重发布的定案钉测）
        fixed = db.get(Asset, 901)
        assert fixed.title == "Fitpiggy 规格（OFF）"
        assert db.get(Asset, 902).title == "xxx 规格（OFF）"
        assert db.get(Asset, 903).title == "偷渡 规格（OFF）"
        assert db.get(Asset, 904).title == "待人洗 规格（OFF）"
        version = db.get(AssetVersion, fixed.current_published_version_id)
        assert version.version_no == 1 and version.object_key == "objects/901/v1.txt"
        chunks = db.scalars(
            select(RetrievalChunk.chunk).where(RetrievalChunk.asset_id == 901)
        ).all()
        assert "品牌：Fitpiggy" in chunks and len(chunks) == 3

        # audit 手动备注：词表外 action、version_no NULL、种子操作者
        audits = db.execute(
            select(AuditLog).where(AuditLog.asset_id == 901)
        ).scalars().all()
        assert len(audits) == 1
        assert audits[0].action == com.AUDIT_ACTION == "title_correct"
        assert audits[0].version_no is None and audits[0].operator_id is not None

        # 幂等：重跑诊断 901 判 match（subject=brand 自洽）、apply 0 行、audit 不增
        again = com.diagnose(com.load_off_assets(db))
        assert next(r for r in again if r["asset_id"] == 901)["verdict"] == com.VERDICT_MATCH
        assert com.apply_corrections(db, again) == 0
        db.commit()
        count = db.execute(
            text("SELECT count(*) FROM audit_log WHERE asset_id = 901")
        ).scalar_one()
        assert count == 1
    engine.dispose()
