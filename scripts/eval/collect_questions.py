"""问法回流半自动（第 101 刀）：演示库 service_messages 抽顾客问句 -> 候选清单。

评测扩容的上游（roadmap 101：「会话表导出真实问法：去重+按分布预分类，人审后
期望值手工定——机器不给自己出题打分」）。本脚本只做「抽问+去重+预分类提示+
当前引擎行为」，**不定期望**：每条候选的 expect 由人（或人授权的代理）依据
实跑核验逐条手工定，才许进 golden。

流程：
1. 抽问：service_messages 里 role='customer' 的消息，取同会话**下一条 agent
   消息**的 kind/citations（当前引擎对这句话的行为）；kind 非空的问句才收
   （agent 回复存在且形态确定——kind 空即回复缺失/中断，无从预分类）。
2. 去重：normalize_question 归一（去首尾空白+尾问号）为去重键，保留最高频
   原形；is_probe_question 排除探针特征（演示库实况：「第N问：」序列、英文
   工程探针、乱码串）。
3. 预分类提示（classify_candidate，简单启发，只提示不定案）：
   问句点名 ≥2 个已发布资产名 -> 混淆候选；引擎 answer 且带引用 -> 正例/同义
   候选；引擎 refusal/handoff -> 拒答或表外同义候选；其余（工具面）不在大集。
4. 引擎现状：对每个唯一问句实跑 retrieve（同 run_eval 口径）记 top-3 资产，
   人审据此定期望锚。

抽问/去重/预分类全为纯函数（normalize_question / is_probe_question /
name_hits / classify_candidate / merge_stats），离线单测见
apps/api/tests/test_collect_questions.py。

用法（仓库根目录）：
    uv run python scripts/eval/collect_questions.py --db postgresql://suite:suite@localhost:5433/suite
    uv run python scripts/eval/collect_questions.py --out scripts/eval/out/questions-candidates-101.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from suite_api.services.retrieval import retrieve

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_OUT = SCRIPT_DIR / "out" / "questions-candidates-101.json"
ENGINE_TOP_K = 3

# 探针特征（演示库实况，正则元组——测试/压测脚本留下的非顾客问句形态）：
# - 「第N问：」序列（网关压测探针，批量净含量问句）
# - thermos capacity（英文工程探针）
# - zxqw / tiuu / 838383（乱码键盘串）
PROBE_PATTERNS: tuple[str, ...] = (
    r"^第\d+问[：:]",
    r"thermos\s+capacity",
    r"\bzxqw\b|\btiuu\b|838383",
)

# 预分类提示的目标分布（与 golden 五分布同词表；工具面不在大集故不出现）
SUGGESTABLE = ("positive", "paraphrase", "confusion", "refusal", "oov_syn")


# ---------------------------------------------------------------- 纯函数：抽问/去重/预分类


def normalize_question(question: str) -> str:
    """去重键：去首尾空白、去尾问号（中英文）。原形保留在候选里，键只用于合并。"""
    q = question.strip()
    while q and q[-1] in "？?":
        q = q[:-1].rstrip()
    return q


def is_probe_question(question: str) -> bool:
    """探针特征判定（正则元组逐条 search，命中即探针）。"""
    return any(re.search(pattern, question) for pattern in PROBE_PATTERNS)


def name_hits(question: str, names: Sequence[str]) -> list[str]:
    """问句点中的已发布资产名（长名优先，同一位置不重复计短名）。

    names 为空/问句不含任何名字 -> 空列表；长度 ≤1 的名字不成判定（「书」「杯」
    这类单字命中太泛）。
    """
    hits: list[str] = []
    for name in sorted({n.strip() for n in names if len(n.strip()) >= 2}, key=len, reverse=True):
        if name in question and not any(name in hit or hit in name for hit in hits):
            hits.append(name)
    return hits


def classify_candidate(
    question: str,
    *,
    agent_kind: str | None,
    agent_cited: bool,
    names: Sequence[str],
) -> list[str]:
    """预分类提示（简单启发，只给建议不定案）：

    - 问句点名 ≥2 个资产名 -> 混淆候选（跨商品词法竞争的主形态）；
    - 引擎 answer 且带引用 -> 正例/同义候选（正例基底或同义改写的种子）；
    - 引擎 refusal/handoff -> 拒答候选或表外同义候选（表外词也会零命中）；
    - answer 无引用（工具面：订单/库存/目录回落）-> 空列表，不在大集。
    """
    if len(name_hits(question, names)) >= 2:
        return ["confusion"]
    if agent_kind == "answer":
        return ["positive", "paraphrase"] if agent_cited else []
    if agent_kind in ("refusal", "handoff"):
        return ["refusal", "oov_syn"]
    return []


def merge_stats(
    occurrences: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """同一归一问句的多条记录合一（纯函数）：计数、代表原形（最高频，同频取先现）、
    引擎行为取**最近一次**（越晚越接近当前引擎/当前库）。

    occurrence 形状 {question, kind, cited}（kind/cited 为该次下一条 agent 消息的
    形态；kind 空表示该次无 agent 回复，不计入行为统计）。
    """
    key = normalize_question(occurrences[0]["question"])
    forms: Counter[str] = Counter()
    behaviors: list[tuple[str, bool]] = []  # (kind, cited) 按时序
    for occ in occurrences:
        if normalize_question(occ["question"]) != key:
            raise ValueError("merge_stats 只接受同键 occurrence")
        forms[occ["question"].strip()] += 1
        if occ.get("kind"):
            behaviors.append((occ["kind"], bool(occ.get("cited"))))
    kind, cited = behaviors[-1] if behaviors else (None, False)
    return {
        "question": forms.most_common(1)[0][0],
        "count": sum(forms.values()),
        "agent_kind": kind,
        "agent_cited": cited,
        "agent_refused_ratio": (
            round(sum(1 for k, _ in behaviors if k != "answer") / len(behaviors), 4)
            if behaviors
            else None
        ),
    }


# ---------------------------------------------------------------- DB 读取 + 引擎现状


def load_question_occurrences(db: Any) -> list[dict[str, Any]]:
    """顾客消息 × 同会话下一条 agent 消息的 kind/citations（一次查询内存配对）。

    kind 非空的配对才算有效 occurrence（下一条 agent 消息无 kind=回复缺失，
    预分类无从谈起）；探针问句在此排除。
    """
    from sqlalchemy import select

    from suite_api.models import ServiceMessage

    messages = db.execute(
        select(
            ServiceMessage.id,
            ServiceMessage.session_id,
            ServiceMessage.role,
            ServiceMessage.content,
            ServiceMessage.kind,
            ServiceMessage.citations,
        ).order_by(ServiceMessage.id)
    ).all()
    occurrences: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if msg.role != "customer" or is_probe_question(msg.content):
            continue
        # 下一条同会话 agent 消息（顾客消息后紧邻的 agent 回复）
        agent = next(
            (m for m in messages[idx + 1 :] if m.session_id != msg.session_id or m.role == "agent"),
            None,
        )
        if agent is None or agent.session_id != msg.session_id or not agent.kind:
            continue
        occurrences.append(
            {
                "question": msg.content,
                "kind": agent.kind,
                "cited": bool(agent.citations),
            }
        )
    return occurrences


def build_candidates(
    occurrences: Sequence[dict[str, Any]],
    names: Sequence[str],
) -> list[dict[str, Any]]:
    """occurrences -> 候选清单（纯装配；引擎现状由调用方补写 engine_top3）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for occ in occurrences:
        grouped.setdefault(normalize_question(occ["question"]), []).append(occ)
    candidates: list[dict[str, Any]] = []
    for stats in (merge_stats(group) for group in grouped.values()):
        stats["suggest"] = classify_candidate(
            stats["question"],
            agent_kind=stats["agent_kind"],
            agent_cited=stats["agent_cited"],
            names=names,
        )
        stats["name_hits"] = name_hits(stats["question"], names)
        candidates.append(stats)
    candidates.sort(key=lambda c: (-c["count"], c["question"]))
    return candidates


def published_asset_names(db: Any) -> list[str]:
    """可点名名集：已发布资产标题原文 + subject 剥装饰后的商品名 + 商品类目词。

    类目词（键盘/显示器/耳机…）是顾客口语里最常点的「商品名」——「显示器和耳机
    你们都有吗」这类多类目问句正是混淆候选的主形态，只按资产标题会漏掉它们。
    """
    import generate_golden as gg
    from sqlalchemy import select

    from suite_api.models import Asset, Product

    titles = db.scalars(
        select(Asset.title).where(Asset.current_published_version_id.isnot(None))
    ).all()
    names = [t for t in titles if t]
    names += [gg.subject(t) for t in names if gg.subject(t) != t]
    names += db.scalars(select(Product.category).distinct()).all()
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="问法回流半自动（抽问+去重+预分类提示）")
    parser.add_argument("--db", default=DEFAULT_DB, help="演示库 Postgres URL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="候选清单 JSON 输出路径")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Asset

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        occurrences = load_question_occurrences(db)
        names = published_asset_names(db)
        candidates = build_candidates(occurrences, names)
        meta = {
            a.id: a.title or "<无标题>"
            for a in db.scalars(select(Asset).where(Asset.current_published_version_id.isnot(None)))
        }
        for cand in candidates:
            hits = retrieve(db, cand["question"], top_k=ENGINE_TOP_K)
            cand["engine_top3"] = [
                {
                    "asset_id": h["asset_id"],
                    "version_no": h["version_no"],
                    "title": meta.get(h["asset_id"]),
                }
                for h in hits
            ]
            cand["engine_hit"] = bool(hits)

    payload = {
        "source": "service_messages (customer -> next agent kind/citations)",
        "db": args.db,
        "probe_patterns": list(PROBE_PATTERNS),
        "occurrences": len(occurrences),
        "unique_questions": len(candidates),
        "candidates": candidates,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_suggest: Counter[str] = Counter()
    for cand in candidates:
        for dist in cand["suggest"]:
            by_suggest[dist] += 1
    print(f"occurrence：{len(occurrences)}（探针/无回复已排除）  唯一问句：{len(candidates)}")
    print(f"预分类提示计数：{json.dumps(dict(by_suggest), ensure_ascii=False)}")
    print(f"候选清单 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
