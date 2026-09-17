"""存量切块 embedding 回填（第 105 刀，向量基础设施 A1）。

给「当前已发布指针版」的切块补 ``retrieval_chunks.embedding``（迁移 0034 的
vector(1024) 列）——发布补写（routes/assets.publish -> embed_version_chunks）
只覆盖**新发布**，存量块与「发布时未配 key/当时失败」的块由本脚本收口。

口径：
- **只扫当前指针版**（与 retrieve 同款 join：chunk 对位资产版本 = 当前已发布
  指针、status=published、未废弃）——检索语料就是这批块，历史版本的块不回填
  （106 刀也只会查指针版，给死版本备向量是白费）；
- **幂等**：只取 ``embedding IS NULL`` 的行，重跑只补漏（已嵌入的行不碰、
  重复跑零 UPDATE）；
- 批量：64 条/请求（services/embedding.EMBED_BATCH_SIZE 同源），每批一个事务
  ——批失败即中止（已成功批次已落库，重跑从 NULL 断点续上），不吞错硬跑
  （连续失败通常是 key/配额问题，硬跑只会刷日志）；
- 未配 EMBED_API_KEY：诚实退出（fail-closed，同 asr/vlm/imggen 四件套——
  不建客户端、不发请求）。

用法（仓库根目录，先起栈 docker compose up -d）：
    uv run python scripts/realdata/backfill_embeddings.py \
        --db postgresql://suite:suite@localhost:5433/suite
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

SCRIPT_NAME = "backfill_embeddings"

# 与 retrieve 候选集同款的「当前指针版」join（分纹不差，只差 SELECT 面）
_PENDING_SQL = """
SELECT c.id, c.chunk
FROM retrieval_chunks c
JOIN asset_versions v ON v.asset_id = c.asset_id AND v.version_no = c.version_no
JOIN assets a ON a.id = v.asset_id
  AND a.current_published_version_id = v.id
  AND a.status = 'published'
  AND a.discarded_at IS NULL
WHERE c.embedding IS NULL
ORDER BY c.id
"""
_TOTAL_SQL = """
SELECT count(*),
       count(*) FILTER (WHERE c.embedding IS NULL)
FROM retrieval_chunks c
JOIN asset_versions v ON v.asset_id = c.asset_id AND v.version_no = c.version_no
JOIN assets a ON a.id = v.asset_id
  AND a.current_published_version_id = v.id
  AND a.status = 'published'
  AND a.discarded_at IS NULL
"""


def pending_rows(db: Any) -> list[tuple[int, str]]:
    """当前指针版、embedding 为 NULL 的 (块 id, 块文本)，按 id 稳定排序。"""
    from sqlalchemy import text

    return [(int(row[0]), str(row[1])) for row in db.execute(text(_PENDING_SQL)).all()]


def total_and_pending(db: Any) -> tuple[int, int]:
    """(当前指针版块总数, 其中 embedding 为 NULL 的条数)。"""
    from sqlalchemy import text

    row = db.execute(text(_TOTAL_SQL)).one()
    return int(row[0]), int(row[1])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="存量切块 embedding 回填（只补 NULL，幂等）")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db:
        print("需要 --db 或 DATABASE_URL", file=sys.stderr)
        return 2
    from suite_api.services import embedding
    from suite_api.services.retrieval import set_chunk_embeddings

    if not embedding.is_configured():
        print("未配置 EMBED_API_KEY：不建客户端、不发请求（.env 配置后重跑）", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(args.db))
    try:
        with sessionmaker(bind=engine)() as db:
            total, pending = total_and_pending(db)
            print(f"当前指针版切块 {total} 条，其中待回填（embedding IS NULL）{pending} 条")
            if not pending:
                print("无可回填行（幂等：全部已嵌入或库为空）")
                return 0
            rows = pending_rows(db)
            done = 0
            for start in range(0, len(rows), embedding.EMBED_BATCH_SIZE):
                batch = rows[start : start + embedding.EMBED_BATCH_SIZE]
                vectors = embedding.embed_texts([chunk for _id, chunk in batch])
                updated = set_chunk_embeddings(
                    db, list(zip([chunk_id for chunk_id, _chunk in batch], vectors, strict=True))
                )
                db.commit()
                done += len(batch)
                print(f"[{SCRIPT_NAME}] {done}/{len(rows)} 已嵌入（本批 UPDATE {updated} 行）")
            left = pending_rows(db)
            print(f"完成：嵌入 {done} 条，剩余 NULL {len(left)} 条（0=收口）")
            return 0
    except embedding.EmbeddingError as exc:
        print(f"嵌入失败，已成功批次保持落库（重跑从 NULL 断点续上）: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
