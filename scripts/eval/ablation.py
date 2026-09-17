"""第 107b 刀实验脚本：三级检索消融终表（第五阶段收官）。

75/79/106 刀同款「测量在先」的收官版：236 条六分布 golden 在三级配置下各跑
一次全分布三指标，产出**六分布 × 三指标 × 三级**的全指标对照表（简历核心
资产）+ 每级增量归因（L2−L1 / L3−L2 逐条 case 对照）。

三级定义（roadmap 原文「词法→+同义→+向量」的落地修订）：
- **L1 词法+同义**（亲和关/融合关）：CJK bigram 词法管线现役本体（打分 ×
  stale × 评论闸 × 去重稳定降序）+ 查询侧同义词并集（36 刀接线 + 104 刀
  数据驱动收词）。同义词**不单独成级**：104 刀收词是**数据进表**（synonyms
  规则表的对组），与词法口径在数据面不可分割，运行时无从「关」——「词法」
  在当前库上的诚实含义就是「词法+同义」（其 before/after 已由第 104 刀
  逐词对照在档，见 rag-eval-report）；
- **L2 L1+实体亲和重排**（79 刀 α=3 乘数 1+3×title_affinity，融合关）：
  = 106 刀前的生产形态（107a 基线即此级）；
- **L3 L2+稠密稀疏融合**（106 刀加权线性 w=0.5 + TAU 0.60 + lexgate，
  affinity_before）：= 当前生产配置。

实现纪律（任务裁决）：``retrieve()`` 没有 feature 开关（融合/亲和是四出口
单点语义，不留运行时开关）；本脚本**在脚本层复现** retrieve 逻辑——词法
级复用 services 的纯函数（query_terms/score_chunk/is_stale/评论闸/title_
idf/title_affinity/redact，与生产逐位同口径），L3 直接调用**现役生产函数**
``dense_candidates`` + ``fuse_dense_sparse``（不是脚本的复刻品），并有
``verify_production`` 对 236 问逐位复核 L3 == retrieve()（同进程 LRU 命中，
零额外云调用）。

judge 口径与 run_eval 生产一致（compose_answer 定 kind、judge_case 落行、
融合输出截 top-3 再判定——106 刀修正先例）。查询向量走生产 LRU
（``query_vector_for``）：236 问每问只调一次云 API，三级共用。

用法（仓库根，连演示库；需 .env 配 EMBED_API_KEY）：
    uv run python scripts/eval/ablation.py --db postgresql://suite:suite@localhost:5433/suite
    # 追加 --out 写终表工件到 scripts/eval/out/107b-ablation.txt
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fusion_matrix import load_candidates
from run_eval import TOP_K, aggregate, format_table, judge_case
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from suite_api.db import to_sqlalchemy_url
from suite_api.models import Asset
from suite_api.services.retrieval import (
    AFFINITY_ALPHA,
    dense_candidates,
    excludes_review_evidence,
    fuse_dense_sparse,
    query_terms,
    redact,
    retrieve,
    score_chunk,
    title_affinity,
    title_idf,
)
from suite_api.services.synonyms import apply_synonyms

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"
# 红线（107a/106 口径）：positive recall@1 98.8（79/80）、refusal 拒答率 86.7（26/30）
POSITIVE_R1_FLOOR = 79 / 80
REFUSAL_RATE_FLOOR = 26 / 30


# ---------------------------------------------------------------- 三级配置（注入形状）


@dataclass(frozen=True)
class LevelConfig:
    """一级消融配置：两个布尔开关拼出三级（脚本层注入——services 不留运行时开关）。

    - affinity：79 刀实体亲和乘数是否乘在词法分上（L2 起）；
    - fusion：106 刀稠密稀疏融合是否叠加（L3 起，affinity_before——亲和分
      先乘好再进融合，与现役 retrieve 生产形态一致）。
    """

    label: str
    affinity: bool = False
    fusion: bool = False


# 三级终表配置（标签进摘要表与工件；L3 与生产 retrieve 的逐位等值由
# verify_production 复核，见模块 docstring）
LEVELS: tuple[LevelConfig, ...] = (
    LevelConfig("L1-词法+同义"),
    LevelConfig("L2-+亲和重排", affinity=True),
    LevelConfig("L3-+稠密稀疏融合", affinity=True, fusion=True),
)


# ---------------------------------------------------------------- 词法级纯函数


def _key(hit: dict[str, Any]) -> tuple[int, int, str]:
    """去重键（与 retrieve 的 unique 同口径：(asset_id, version_no, chunk)）。"""
    return (hit["asset_id"], hit["version_no"], hit["chunk"])


def _stable_sorted(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """score 降序、并列按 (asset_id, chunk) 稳定（与 retrieve 排序键同款）。"""
    return sorted(hits, key=lambda h: (-h["score"], h["asset_id"], h["chunk"]))


def lexical_stage(
    candidates: list[dict[str, Any]],
    titles_of: dict[int, str],
    idf: dict[str, float],
    question: str,
) -> tuple[list[dict[str, Any]], dict[int, float]]:
    """问句 -> (裸词法命中降序, per-资产亲和映射)——L1 的本体 + L2/L3 的原料。

    词法管线与 retrieve 逐位同口径（纯函数形状，候选行由调用方一次性注入）：
    terms = 原查询 ∪ 同义词归一后（104 刀数据面并集，L1 即含同义）、评论适用域
    闸、score_chunk × stale 乘数、(asset_id, version_no, chunk) 去重、稳定降序、
    出口 redact（打分全程原文块，与生产同口）。亲和只算映射不乘分——乘不乘
    由 LevelConfig 决定（消融点）。
    """
    terms = query_terms(question) | query_terms(apply_synonyms(question))
    drop_reviews = excludes_review_evidence(question)
    scored = [
        {**c, "chunk": redact(c["chunk"]), "score": s}
        for c, s in ((c, score_chunk(terms, c["chunk"]) * c["stale_mult"]) for c in candidates)
        if s > 0.0 and not (drop_reviews and c["source_kind"] == "review_import")
    ]
    unique: dict[tuple[int, int, str], dict[str, Any]] = {}
    for hit in scored:
        unique.setdefault(_key(hit), hit)
    aff_of = {asset_id: title_affinity(terms, title, idf) for asset_id, title in titles_of.items()}
    return _stable_sorted(list(unique.values())), aff_of


def apply_affinity_multiplier(
    hits: list[dict[str, Any]], aff_of: dict[int, float], *, alpha: float = AFFINITY_ALPHA
) -> list[dict[str, Any]]:
    """79 刀乘数（纯函数）：score × (1 + alpha × aff_of[asset_id])（缺省 0=中性面）。"""
    return [
        {**hit, "score": hit["score"] * (1.0 + alpha * aff_of.get(hit["asset_id"], 0.0))}
        for hit in hits
    ]


def level_hits(
    config: LevelConfig,
    bare_hits: list[dict[str, Any]],
    aff_of: dict[int, float],
    vec_hits: list[dict[str, Any]] | None = None,
    *,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:
    """三级配置注入（纯函数，本刀核心）：按 LevelConfig 组装该级检索输出 top-k。

    - L1（affinity=fusion=False）：裸词法分原序截断；
    - L2（affinity=True）：乘亲和完善后重排截断（= 106 前生产形态）；
    - L3（fusion=True）：亲和完善分（affinity_before）进 ``fuse_dense_sparse``
      ——**服务现役融合函数**（TAU/lexgate/NULL 块 fail-open 全在其中），
      L3 与 retrieve() 生产路径同函数同输入；vec_hits=None 时归一化单调，
      名次与 L2 逐位一致（无向量路的诚实退化）。
    词法空手（bare_hits 空）三级全空手出——拒答语义完全由词法路决定。
    """
    lex = apply_affinity_multiplier(bare_hits, aff_of) if config.affinity else list(bare_hits)
    if config.fusion:
        return fuse_dense_sparse(lex, vec_hits)[:top_k]
    return _stable_sorted(lex)[:top_k]


# ---------------------------------------------------------------- 增量归因（纯函数）


def level_diff(
    rows_before: list[dict[str, Any]],
    rows_after: list[dict[str, Any]],
    *,
    key: str,
) -> tuple[list[str], list[str]]:
    """相邻两级 judge 行逐条对照（纯函数）：key 指标变好/变坏的 case id。

    key ∈ {"recall1","recall3","mrr","ndcg3","noise3","refused"}；noise3 变小
    =变好（翻转真值表），其余变大=变好。None（指标不适用）不计。
    """
    before = {r["id"]: r[key] for r in rows_before if r.get(key) is not None}
    after = {r["id"]: r[key] for r in rows_after if r.get(key) is not None}
    gained: list[str] = []
    lost: list[str] = []
    for case_id, value_a in before.items():
        value_b = after.get(case_id)
        if value_b is None or value_a == value_b:
            continue
        improved = (value_a > value_b) if key == "noise3" else (value_a < value_b)
        (gained if improved else lost).append(case_id)
    return gained, lost


# ---------------------------------------------------------------- 主流程


def _hit_columns(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """该级输出 -> judge_case 消费的最小形状（asset/version/chunk/score）。"""
    return [
        {
            "asset_id": hit["asset_id"],
            "version_no": hit["version_no"],
            "chunk": hit["chunk"],
            "score": hit.get("score", 0.0),
        }
        for hit in hits
    ]


def ablation_rows(
    db: Any,
    cases: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    meta: dict[int, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """全量三级跑表 -> ({级标签: judge 行}, {case id: L3 hits}——生产复核用)。

    每问句词法两级原料只算一次；向量路一查（``dense_candidates`` 走生产 LRU
    ——236 问只调一次云 API，三级共用）；L3 的融合即现役生产函数。judge 口径
    与 run_eval 一致：compose_answer 定 kind（空命中=refusal）、judge_case 落行。
    """
    from suite_api.services.answer import compose_answer

    titles_of = {c["asset_id"]: c["title"] for c in candidates}
    idf = title_idf(list(titles_of.values()))
    rows_by_level: dict[str, list[dict[str, Any]]] = {cfg.label: [] for cfg in LEVELS}
    l3_hits: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        question = case["question"]
        bare, aff_of = lexical_stage(candidates, titles_of, idf, question)
        vec_hits = dense_candidates(db, question, drop_reviews=excludes_review_evidence(question))
        for cfg in LEVELS:
            hits = level_hits(cfg, bare, aff_of, vec_hits)
            composed = compose_answer(_hit_columns(hits), meta)
            rows_by_level[cfg.label].append(judge_case(case, _hit_columns(hits), composed.kind))
            if cfg.fusion:
                l3_hits[case["id"]] = _hit_columns(hits)
    return rows_by_level, l3_hits


def verify_production(
    db: Any, cases: list[dict[str, Any]], l3_hits: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """L3 == 生产复核：同问句直调现役 retrieve()，逐位对照 (asset/version/chunk/score)。

    返回不一致的 case id（空 = 逐位一致）。查询向量 LRU 已在跑表时热（同问句
    零云调用）；这层复核是「L3=当前生产配置」的实测钉——脚本层复现词法级若
    与生产有任何口径漂移（闸/去重/排序/redact），这里会点名。
    """
    mismatches: list[str] = []
    for case in cases:
        hits = retrieve(db, case["question"], top_k=TOP_K)
        production = [(h["asset_id"], h["version_no"], h["chunk"], h["score"]) for h in hits]
        ours = [
            (h["asset_id"], h["version_no"], h["chunk"], h["score"]) for h in l3_hits[case["id"]]
        ]
        if production != ours:
            mismatches.append(case["id"])
    return mismatches


def redline(agg: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    """红线检查（fusion_matrix 同款）：positive@1 ≥ 98.8 且 拒答率 ≥ 86.7。"""
    pos_r1 = agg["positive"]["recall1"]
    ref_rate = agg["refusal"]["refusal_rate"]
    ok = bool(
        pos_r1 is not None
        and pos_r1 >= POSITIVE_R1_FLOOR - 1e-9
        and ref_rate is not None
        and ref_rate >= REFUSAL_RATE_FLOOR - 1e-9
    )
    return ok, "OK" if ok else "BROKEN"


def format_levels(aggs: dict[str, dict[str, dict[str, Any]]]) -> str:
    """三级摘要表：level | 六分布@1 | ref% | ovr@1/@3 | MRR/nDCG@3/噪声@3 | 红线。"""
    header = (
        f"{'level':<16}{'pos@1':>7}{'par@1':>7}{'conf@1':>8}{'oov@1':>7}{'sneg@1':>8}"
        f"{'ref%':>7}{'ovr@1':>7}{'ovr@3':>7}{'MRR':>9}{'nDCG@3':>9}{'噪声@3':>9}{'红线':>8}"
    )
    lines = [header]

    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value * 100:.1f}"

    for cfg in LEVELS:
        agg = aggs[cfg.label]
        ok, mark = redline(agg)
        lines.append(
            f"{cfg.label:<16}"
            f"{pct(agg['positive']['recall1']):>7}"
            f"{pct(agg['paraphrase']['recall1']):>7}"
            f"{pct(agg['confusion']['recall1']):>8}"
            f"{pct(agg['oov_syn']['recall1']):>7}"
            f"{pct(agg['sem_neg']['recall1']):>8}"
            f"{pct(agg['refusal']['refusal_rate']):>7}"
            f"{pct(agg['overall']['recall1']):>7}"
            f"{pct(agg['overall']['recall3']):>7}"
            f"{agg['overall']['mrr']:>9.4f}"
            f"{agg['overall']['ndcg3']:>9.4f}"
            f"{pct(agg['overall']['noise3']):>9}"
            f"{mark:>8}"
        )
    return "\n".join(lines)


_DIFF_METRICS = ("recall1", "recall3", "mrr", "ndcg3", "noise3", "refused")


def format_increments(
    aggs: dict[str, dict[str, dict[str, Any]]], rows_by_level: dict[str, list[dict[str, Any]]]
) -> str:
    """每级增量：L2−L1 / L3−L2 的分布级差值（pp）+ 逐条 case 对照。"""
    lines: list[str] = []
    for before_cfg, after_cfg in zip(LEVELS, LEVELS[1:], strict=False):
        agg_a, agg_b = aggs[before_cfg.label], aggs[after_cfg.label]
        rows_a, rows_b = rows_by_level[before_cfg.label], rows_by_level[after_cfg.label]
        lines.append(f"=== {after_cfg.label} − {before_cfg.label} ===")

        def delta(dist: str, metric: str, *, a: dict = agg_a, b: dict = agg_b) -> str:
            va, vb = a[dist][metric], b[dist][metric]
            if va is None or vb is None:
                return "-"
            return f"{(vb - va) * 100:+.1f}"

        for dist in ("positive", "paraphrase", "confusion", "oov_syn", "sem_neg", "overall"):
            lines.append(
                f"  {dist:<11}"
                f"@1 {delta(dist, 'recall1'):>6}pp"
                f"  @3 {delta(dist, 'recall3'):>6}pp"
                f"  MRR {delta(dist, 'mrr'):>6}pp"
                f"  nDCG {delta(dist, 'ndcg3'):>6}pp"
                f"  噪声 {delta(dist, 'noise3'):>6}pp"
            )
        ref_a = agg_a["refusal"]["refusal_rate"]
        ref_b = agg_b["refusal"]["refusal_rate"]
        ref_text = "-" if ref_a is None or ref_b is None else f"{(ref_b - ref_a) * 100:+.1f}pp"
        lines.append(f"  拒答率 {ref_text}")
        for key in _DIFF_METRICS:
            gained, lost = level_diff(rows_a, rows_b, key=key)
            if not gained and not lost:
                continue
            name = key if key != "refused" else "refused(拒答翻转)"
            lines.append(
                f"  {name}: +{len(gained)}{'(' + ','.join(gained) + ')' if gained else ''}"
                f" / -{len(lost)}{'(' + ','.join(lost) + ')' if lost else ''}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三级检索消融终表（第 107b 刀）")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--out", type=Path, default=None, help="终表工件写入路径（三级全表+摘要+增量归因）"
    )
    args = parser.parse_args(argv)

    from suite_api.services import embedding

    if not embedding.is_configured():
        print("未配置 EMBED_API_KEY（.env）——L3 融合需要真查询向量，先配置再跑")
        return 2

    cases = json.loads(args.golden.read_text(encoding="utf-8"))
    engine = create_engine(to_sqlalchemy_url(args.db))
    try:
        with sessionmaker(bind=engine)() as db:
            candidates = load_candidates(db)
            meta = {
                asset.id: {"kind": asset.kind, "title": asset.title}
                for asset in db.scalars(
                    select(Asset).where(Asset.current_published_version_id.isnot(None))
                )
            }
            rows_by_level, l3_hits = ablation_rows(db, cases, candidates, meta)
            mismatches = verify_production(db, cases, l3_hits)
    finally:
        engine.dispose()

    aggs = {cfg.label: aggregate(rows_by_level[cfg.label]) for cfg in LEVELS}
    n_assets = len({c["asset_id"] for c in candidates})
    print(
        f"golden：{args.golden}（{len(cases)} 条）  候选资产 {n_assets} 个  "
        f"候选块 {len(candidates)}  检索 top-{TOP_K}"
    )
    print(format_levels(aggs))
    print(
        f"L3 == 生产复核（retrieve() 逐位对照 {len(cases)} 问）: "
        + ("逐位一致" if not mismatches else f"不一致 {len(mismatches)} 条 {mismatches}")
    )
    print()
    print(format_increments(aggs, rows_by_level))
    for cfg in LEVELS:
        print(f"=== {cfg.label}（六分布全表） ===")
        print(format_table(aggs[cfg.label], judge_on=False))
        print()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            f"# 第 107b 刀三级消融终表（{datetime.now(UTC).date().isoformat()}，"
            f"golden {len(cases)} 条六分布）",
            "",
            format_levels(aggs),
            "",
            "L3 == 生产复核（retrieve() 逐位对照）: "
            + ("逐位一致" if not mismatches else f"不一致 {mismatches}"),
            "",
            format_increments(aggs, rows_by_level),
        ]
        for cfg in LEVELS:
            parts.append(f"=== {cfg.label}（六分布全表） ===")
            parts.append(format_table(aggs[cfg.label], judge_on=False))
            parts.append("")
        args.out.write_text("\n".join(parts), encoding="utf-8")
        print(f"终表工件 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
