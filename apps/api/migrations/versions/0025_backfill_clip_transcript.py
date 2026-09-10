"""审计刀 9：给存量切片视频资产回填 ``transcript`` 字段（第 46 刀的回填缺口）。

第 46 刀（ADR 0047 §5）把 video 正文源从「对象字节」改成「``transcript`` 字段」，
但没有回填 46 之前登记的切片资产——那批资产的字节是**时间码文本**
（``[00:02:14-00:02:52] 转写…``，ADR 0039 形态）、``extracted_fields`` 里没有
``transcript``。它们今天仍可检索（发布时切的是当时写下的正文块），但**一旦开
修订重新发布**，``index_chunks_for_version`` 走 video 分支取字段得到空正文，
发布成功而正文块静默归零。

回填口径：把对象键那段的**时间码文本**（前缀 + 转写）写进
``extracted_fields["transcript"]``（``source="machine"``）——即第 46 刀两条路径
统一后的形态。只动「kind=video 且 extracted_fields 无 transcript」的版本；
已有字段的行一律不动（不覆盖人洗/机洗成果）。字节不碰（ADR 0038：字节不动）。

识别存量行不用读对象存储（迁移里没有 storage）：第 46 刀之前登记的 video 版本
其对象键一律 ``.txt``，而真 mp4 资产的键是 ``.mp4`` 且必有 transcript 字段——
两个条件叠加即可精确圈定，无需读字节。

down：把本次回填的字段删掉（只删 ``source=="machine"`` 且键为 ``.txt`` 的那些
行——与 upgrade 的圈定口径一致，不误删人工确认过的字段）。
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT v.id, v.object_key
            FROM asset_versions v
            JOIN assets a ON a.id = v.asset_id
            WHERE a.kind = 'video'
              AND v.object_key LIKE '%.txt'
              AND NOT (v.extracted_fields ? 'transcript')
            """
        )
    ).all()
    if not rows:
        return
    updates: list[dict[str, object]] = []
    for version_id, _object_key in rows:
        # 回填值取自对象键？不行——键里没有转写。改从候选表反查（拣选时写下的
        # 回执锚 registered_asset_id 指向资产，候选的 transcript 就是当时的转写），
        # 取不到就跳过（宁缺：不编造正文）。
        candidate = conn.execute(
            sa.text(
                """
                SELECT c.transcript
                FROM clip_candidates c
                JOIN asset_versions v ON v.asset_id = c.registered_asset_id
                WHERE v.id = :version_id
                ORDER BY c.id
                LIMIT 1
                """
            ),
            {"version_id": version_id},
        ).first()
        if candidate is None or not candidate[0]:
            continue
        updates.append({"version_id": version_id, "transcript": candidate[0]})

    for item in updates:
        conn.execute(
            sa.text(
                """
                UPDATE asset_versions
                SET extracted_fields =
                    jsonb_set(
                        COALESCE(extracted_fields, '{}'::jsonb),
                        '{transcript}',
                        CAST(:entry AS jsonb),
                        true
                    )
                WHERE id = :version_id
                """
            ),
            {
                "version_id": item["version_id"],
                "entry": json.dumps(
                    {"value": item["transcript"], "source": "machine"}, ensure_ascii=False
                ),
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE asset_versions
            SET extracted_fields = extracted_fields - 'transcript'
            WHERE object_key LIKE '%.txt'
              AND extracted_fields -> 'transcript' ->> 'source' = 'machine'
              AND asset_id IN (SELECT id FROM assets WHERE kind = 'video')
            """
        )
    )
