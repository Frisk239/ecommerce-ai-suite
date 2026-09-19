"""第 123 刀 A/B 逐条诊断（一次性）：golden 每条 top-3 落 JSON，供新旧分词 diff。"""
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, "apps/api/src")
from suite_api.db import to_sqlalchemy_url  # noqa: E402
from suite_api.services.retrieval import retrieve  # noqa: E402

DB = "postgresql://suite:suite@localhost:5433/suite"
GOLDEN = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("scripts/eval/out/golden_large.json")
OUT = Path(sys.argv[1])

cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
engine = create_engine(to_sqlalchemy_url(DB))
rows = []
with sessionmaker(bind=engine)() as db:
    from suite_api.models import Asset, AssetVersion
    from sqlalchemy import select

    id2title = dict(db.execute(select(Asset.id, Asset.title)).all())
    for c in cases:
        if "cite" not in (c.get("expect") or {}):
            continue
        exp = c["expect"]["cite"]
        hits = retrieve(db, c["question"], top_k=5)
        rows.append(
            {
                "id": c["id"],
                "q": c["question"],
                "exp_title": exp.get("cite_asset_title") or id2title.get(exp.get("asset_id")),
                "exp_id": exp.get("asset_id"),
                "top": [
                    {"a": h["asset_id"], "t": id2title.get(h["asset_id"]), "s": round(h["score"], 3)}
                    for h in hits[:3]
                ],
            }
        )
OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(rows)} cite cases -> {OUT}")
