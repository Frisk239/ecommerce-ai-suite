"""第 79 刀实验脚本：实体亲和重排（entity-affinity rerank）的测量矩阵。

75 刀流程同款「测量在先」：本脚本在演示库上复现 retrieve 全管线（打分/
stale/评论闸/去重），对「实体亲和乘数」的不同应用形态与强度跑 96 条大集
指标，是 ADR 0048 定稿 α=3 的证据（数字见 rag-eval-report 第 79 刀节）。

病根（75 刀结论）：问 A 商品的字段（品牌/净含量/条码…）召回 B 商品的
同文/近同文字段块——字段名 bigram 对问句分数完全一样，值部分不贡献交集，
短块 sqrt 归一微弱胜出，并列时靠 (asset_id, chunk) 稳定出榜。全局来源权重
已被 75 刀证伪（改善 0）；本实验测 **per-query 实体信号**：问句与候选资产
标题的 IDF 加权 bigram 覆盖。

信号（纯词法、零 LLM 零向量、确定性可复现；与生产实现同式）：
    affinity(q, asset) = Σ_{t ∈ terms(q) ∩ terms(title)} idf(t)
                         / Σ_{t ∈ terms(title)} idf(t)
    idf(t) = log(1 + N_assets / (1 + df(t)))，df/N 按已发布资产标题全集算
    （「规格（OFF）」类共享 bigram 天然低 idf，商品名天然高 idf）。

矩阵（形态 × 强度）：
    mult-a{α}  score × (1 + α·affinity)，α ∈ {0.5,1,2,3,4}——定稿 α=3
    tie-r{n}   分数四舍五入 n 位小数后近并列，组内按 affinity 破平
               （对照组：只救同分并列，动不了「更短他品块微弱胜出」主形态）
    baseline   现状（(-score, asset_id, chunk)）

用法（仓库根，连演示库）：
    uv run python scripts/eval/rerank_experiment.py --db postgresql://suite:suite@localhost:5433/suite
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from run_eval import TOP_K, aggregate, format_table, judge_case
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from suite_api.db import to_sqlalchemy_url
from suite_api.models import Asset, AssetVersion, RetrievalChunk
from suite_api.services.retrieval import (
    excludes_review_evidence,
    is_stale,
    query_terms,
    score_chunk,
    title_affinity,
    title_idf,
)
from suite_api.services.synonyms import apply_synonyms

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"

VARIANTS = (
    "baseline",
    "mult-a0.5",
    "mult-a1",
    "mult-a2",
    "mult-a3",
    "mult-a4",
    "tie-r1",
    "tie-r2",
)


def load_candidates(db: Any) -> list[dict[str, Any]]:
    """一次性拉回 retrieve 同口径的全候选行（含 title 供亲和计算）。"""
    from datetime import UTC, datetime

    from suite_api.services.retrieval import STALE_MULTIPLIER, stale_days

    now = datetime.now(UTC)
    rows = db.execute(
        select(
            RetrievalChunk.asset_id,
            RetrievalChunk.version_no,
            RetrievalChunk.chunk,
            Asset.last_verified_at,
            Asset.source_kind,
            Asset.title,
        )
        .join(
            AssetVersion,
            (AssetVersion.asset_id == RetrievalChunk.asset_id)
            & (AssetVersion.version_no == RetrievalChunk.version_no),
        )
        .join(
            Asset,
            (Asset.id == AssetVersion.asset_id)
            & (Asset.current_published_version_id == AssetVersion.id)
            & (Asset.status == "published"),
        )
        .order_by(RetrievalChunk.id)
        .limit(10000)
    ).all()
    days = stale_days()
    return [
        {
            "asset_id": asset_id,
            "version_no": version_no,
            "chunk": chunk,
            "stale_mult": STALE_MULTIPLIER
            if is_stale(verified_at, now=now, days=days)
            else 1.0,
            "source_kind": source_kind,
            "title": title,
        }
        for asset_id, version_no, chunk, verified_at, source_kind, title in rows
    ]


def rank(
    scored: list[dict[str, Any]], aff_of: dict[int, float], variant: str
) -> list[dict[str, Any]]:
    """去重 + 按 variant 排序。scored 行需含 score/asset_id/version_no/chunk。"""
    unique: dict[tuple[int, int, str], dict[str, Any]] = {}
    for hit in scored:
        key = (hit["asset_id"], hit["version_no"], hit["chunk"])
        if key not in unique:
            unique[key] = hit
    hits = list(unique.values())

    def aff(hit: dict[str, Any]) -> float:
        return aff_of.get(hit["asset_id"], 0.0)

    if variant == "baseline":
        hits.sort(key=lambda h: (-h["score"], h["asset_id"], h["chunk"]))
    elif variant.startswith("mult-a"):
        alpha = float(variant[len("mult-a") :])
        for h in hits:
            h["final"] = h["score"] * (1.0 + alpha * aff(h))
        hits.sort(key=lambda h: (-h["final"], h["asset_id"], h["chunk"]))
    elif variant.startswith("tie-r"):
        ndigits = int(variant[len("tie-r") :])
        for h in hits:
            h["final"] = round(h["score"], ndigits)
        # 近并列桶内 affinity 优先；桶外保持分数序，(asset_id, chunk) 兜底稳定
        hits.sort(key=lambda h: (-h["final"], -aff(h), -h["score"], h["asset_id"], h["chunk"]))
    else:
        raise ValueError(variant)
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实体亲和重排实验矩阵")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    args = parser.parse_args(argv)

    from suite_api.services.answer import compose_answer
    from suite_api.services.retrieval import redact

    cases = json.loads(args.golden.read_text(encoding="utf-8"))
    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        candidates = load_candidates(db)
        # 资产级去重后再算 idf（生产 retrieve 同口径：titles 是 asset_id->title
        # 的 dict）——行级列表会把同一资产的 title 按 chunk 数重复计入 df/N
        idf = title_idf(list({c["asset_id"]: c["title"] for c in candidates}.values()))
        n_assets = len({c["asset_id"] for c in candidates})
        meta = {
            asset.id: {"kind": asset.kind, "title": asset.title}
            for asset in db.scalars(
                select(Asset).where(Asset.current_published_version_id.isnot(None))
            )
        }

        rows_by_variant: dict[str, list[dict[str, Any]]] = {v: [] for v in VARIANTS}
        for case in cases:
            question = case["question"]
            terms = query_terms(question) | query_terms(apply_synonyms(question))
            drop_reviews = excludes_review_evidence(question)
            scored = [
                {**c, "score": s}
                for c, s in (
                    (c, score_chunk(terms, c["chunk"]) * c["stale_mult"]) for c in candidates
                )
                if s > 0.0 and not (drop_reviews and c["source_kind"] == "review_import")
            ]
            # 与 retrieve 同口径：chunk 出口掩码（compose_answer 消费）
            for h in scored:
                h["chunk"] = redact(h["chunk"])
            aff_of: dict[int, float] = {}
            for h in scored:
                if h["asset_id"] not in aff_of:
                    aff_of[h["asset_id"]] = title_affinity(terms, h["title"], idf)
            for variant in VARIANTS:
                hits = rank(scored, aff_of, variant)[:TOP_K]
                composed = compose_answer(hits, meta)
                rows_by_variant[variant].append(judge_case(case, hits, composed.kind))

    print(f"golden：{args.golden}（{len(cases)} 条）  候选资产 {n_assets} 个  检索 top-{TOP_K}")
    base_rows = rows_by_variant["baseline"]
    base_top1 = {r["id"]: r["hit_asset_ids"][0] if r["hit_asset_ids"] else None for r in base_rows}
    base_expect = {c["id"]: c.get("expect", {}).get("cite", {}).get("asset_id") for c in cases}
    for variant in VARIANTS:
        rows = rows_by_variant[variant]
        agg = aggregate(rows)
        print(f"\n=== {variant} ===")
        print(format_table(agg, judge_on=False))
        if variant != "baseline":
            changed = []
            for r in rows:
                top1 = r["hit_asset_ids"][0] if r["hit_asset_ids"] else None
                if top1 != base_top1[r["id"]]:
                    want = base_expect[r["id"]]
                    before_ok = base_top1[r["id"]] == want
                    after_ok = top1 == want
                    tag = (
                        "改善"
                        if after_ok and not before_ok
                        else ("变差" if before_ok and not after_ok else "横移")
                    )
                    changed.append(f"{r['id']}: {base_top1[r['id']]} -> {top1} [{tag}]")
            print(f"top-1 变动 {len(changed)} 条：")
            for line in changed:
                print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
