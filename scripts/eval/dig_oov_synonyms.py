"""表外同义词挖掘（第 104 刀：数据驱动同义表的收词上游）。

101 刀评估门判决 oov_syn@1 40.0% < 70%——病根是顾客用的词不在 synonyms 表里
（vocabulary mismatch 的表外自由面）。本脚本从**真实问法**挖表外同义候选：
问句池（collect_questions 的 210/215 唯一问句，101 与 104 两份清单合并）+
golden oov_syn 15 条探针（探针词也是真实缺口证据）。

方法（分词假设检验，逐 (token -> 目标词) 一条假设）：
1. 分词：问句按停用字/非中文字符切成连续中文段，段内取 2-4 字滑窗子串为
   候选 token（``candidate_spans``）；对语料 bigram 集标缺口状态
   （``span_oov_state``：full=纯表外 / partial / in）。
2. 目标词表分三层（tier 化——滑窗语料词纯按分排序会被「到货后看」这类跨词
   窗口碎片霸榜，真目标反而进不了实跑名单）：
   - 表内词：同义词表全部成员（表内归一假设——任务口径的主通道）；
   - 字段名：语料「字段：值」行的字段名整体（条码/品牌/上市年份/上班时间…
     数据驱动，新组发现的主形态）；
   - 语料词：全部语料文本的中文 2-4 字滑窗片段里 df>=2 的高频词（兜底——
     雕字->刻字 这类正文词只能从这层找）。
   每层先过本地词法预筛（``rank_targets``——只算词法分，无亲和/保鲜乘数），
   各取前若干送实跑。
3. 假设检验：把「token 轮转到目标词」的试探边临时插进 synonyms 规则表
   （``hypothesis_rules``——retrieve 在调用点读 apply_synonyms 的模块全局，
   patch 即**实跑语义**，非本地模拟），retrieve 前后对照：探针按期望锚
   recall 翻转判收，问句池按零命中->命中（或 top1 分显著提升）判收。
4. 词频分拣（``triage_label``）：token 在问句池出现 >=2 问、或出现在 oov_syn
   探针里 -> priority（收词候选）；频次 1 且非探针 -> deferred（记候选不收，
   「不追低频孤例」纪律）。

输出：候选清单 JSON（token + 频次 + 来源问句 + 替换假设（归一到表内哪个词/
哪个语料词）+ 当前/假设命中对照）+ stdout 摘要。收词决策（收/弃）由人依据
清单逐词做——机器只产证据不定案（101 刀「机器不给自己出题打分」同款纪律）。

--ab 模式（收词对照，一词一跑 run_eval 太重——全量收完再跑）：golden 的
oov_syn 15 + paraphrase 58 直调 retrieve（run_eval 同口径），逐条 recall@1/@3
+ top3 资产；附问句池命中快照（真实问法零漂移守卫）。--against 传上一状态
工件即打印逐位翻转。用法（仓库根目录）：

    uv run python scripts/eval/dig_oov_synonyms.py --db postgresql://suite:suite@localhost:5433/suite
    uv run python scripts/eval/dig_oov_synonyms.py --ab --ab-out scripts/eval/out/104-ab.json
    uv run python scripts/eval/dig_oov_synonyms.py --ab --out ... --against scripts/eval/out/104-ab-prev.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from suite_api.services import synonyms
from suite_api.services.retrieval import _STOP_CHARS, FIELD_LINE_RE, query_terms, retrieve

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"
DEFAULT_POOLS: tuple[Path, ...] = (
    SCRIPT_DIR / "out" / "questions-candidates-101.json",
    SCRIPT_DIR / "out" / "questions-candidates-104.json",
)
DEFAULT_OUT = SCRIPT_DIR / "out" / "oov-syn-candidates-104.json"
DEFAULT_AB_OUT = SCRIPT_DIR / "out" / "104-ab.json"

TOP_K = 3
# 候选 token：连续中文段内的滑窗子串长度区间（2 字起——单字不成同义判定）
SPAN_MIN_CHARS = 2
SPAN_MAX_CHARS = 4
# 语料侧目标词的文档频下限（df=1 的孤词不配当归一目标）
TARGET_DF_MIN = 2
# 问句池路径：表内词+字段名合并送实跑数 / 语料滑窗词送实跑数
TIER_KEEP = (12, 6)
# 探针路径送实跑的滑窗目标词数上限（表内词/字段名全数送；按期望资产块分预筛后）
PROBE_KEEP = 14
# 问句池判收的 top1 分提升门槛（词法分绝对值——低于此视为并列抖动噪声）
POOL_TOP1_GAIN_KEEP = 0.15
# 清单里每个 token 最多留多少条来源问句（工件可读性）
MAX_SOURCES = 5

_CN_RUN_RE = re.compile(r"[\u4e00-\u9fa5]+")

# ---------------------------------------------------------------- 纯函数：分词/假设/分拣


def candidate_spans(question: str) -> tuple[str, ...]:
    """问句 -> 候选 token 序列（纯函数）。

    按停用字与一切非中文字符（英文型号/数字/标点）切段，段内取 2-4 字滑窗
    子串；去重保序（长词在前——人读清单时长词是主形态）。停用字口径复用
    retrieval._STOP_CHARS（bigram 打分同一套功能字表，单一真源）。
    """
    runs: list[str] = []
    current: list[str] = []
    for ch in question:
        if "\u4e00" <= ch <= "\u9fa5" and ch not in _STOP_CHARS:
            current.append(ch)
        else:
            if len(current) >= SPAN_MIN_CHARS:
                runs.append("".join(current))
            current = []
    if len(current) >= SPAN_MIN_CHARS:
        runs.append("".join(current))
    spans: list[str] = []
    for run in runs:
        for size in range(min(SPAN_MAX_CHARS, len(run)), SPAN_MIN_CHARS - 1, -1):
            for i in range(len(run) - size + 1):
                span = run[i : i + size]
                if span not in spans:
                    spans.append(span)
    return tuple(spans)


def span_oov_state(span: str, corpus_terms: frozenset[str]) -> str:
    """token 对语料 bigram 集的缺口状态（纯函数）：full=全部 bigram 缺失（纯
    表外词，如 邮资/雕字）、partial=部分缺失（保温壶——保温 在、温壶 缺）、
    in=全在（语料已有词）。"""
    terms = query_terms(span)
    if not terms:
        return "in"
    missing = sum(1 for term in terms if term not in corpus_terms)
    if missing == 0:
        return "in"
    return "partial" if missing < len(terms) else "full"


def hypothesis_question(question: str, span: str, target: str) -> str:
    """假设问句：把 token 首次出现替换为目标词——「token 轮转到目标词」在查询
    侧的形态（收词后 apply_synonyms 会产出它，retrieve 取原句∪改写并集）。"""
    return question.replace(span, target, 1)


def triage_label(freq: int, in_probe: bool) -> str:
    """词频分拣（纯函数）：频次 >=2（问句池出现 >=2 问）或出现在 oov_syn 探针
    里 -> priority；频次 1 且非探针 -> deferred（记候选不收——低频孤例）。"""
    return "priority" if freq >= 2 or in_probe else "deferred"


def build_candidate(
    token: str,
    freq: int,
    probe_ids: Sequence[str],
    sources: Sequence[str],
    oov_state: str,
    hypotheses: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """候选清单行（纯装配，形状由单测钉住）。hypotheses 每条含 target（归一
    假设：表内组词或语料词）、group（目标词所在同义组，null=语料词新组）、
    question、对照（base/hyp 的 top3 资产与分、探针期望锚的 recall 翻转）。"""
    return {
        "token": token,
        "freq": freq,
        "in_probe": sorted(set(probe_ids)),
        "sources": list(sources[:MAX_SOURCES]),
        "oov_state": oov_state,
        "triage": triage_label(freq, bool(probe_ids)),
        "hypotheses": list(hypotheses),
    }


# ---------------------------------------------------------------- 纯函数：本地词法预筛


def rank_targets(
    question: str,
    span: str,
    targets: Sequence[str],
    chunk_terms_seq: Sequence[frozenset[str]],
    inverted: dict[str, tuple[int, ...]],
    *,
    only_chunk_ids: frozenset[int] | None = None,
) -> list[tuple[str, float]]:
    """目标词本地预筛（纯函数）：对每个 target 造假设词集（原句 ∪ 替换句的
    bigram 并集——收词后 retrieve 的查询侧形态），只算词法分（score_chunk 同
    公式，无亲和/保鲜乘数），返回按「假设后最高块分」降序的 (target, 分)。

    并集只增不删，分数变化的块只会是含新增 bigram 的块——经倒排索引直达，
    免全量扫描。替换不带来任何词法命中（gain<=0）的目标词不进榜。

    only_chunk_ids 给定时只在给定块上计分——探针有期望锚，按**期望资产**的
    块分排序才能把真目标（运费之于 479）排进实跑名单；按全库绝对分排序会
    被与锚无关的高分短块（净含量：500ml）占坑，真目标反而被截断。
    """
    base_terms = query_terms(question)
    ranked: list[tuple[str, float]] = []
    for target in targets:
        if target == span or target in question:
            continue
        hyp_terms = base_terms | query_terms(hypothesis_question(question, span, target))
        diff = hyp_terms - base_terms
        if not diff:
            continue
        chunk_ids = {cid for term in diff for cid in inverted.get(term, ())}
        if only_chunk_ids is not None:
            chunk_ids &= only_chunk_ids
        if not chunk_ids:
            continue
        best = 0.0
        for cid in chunk_ids:
            units = chunk_terms_seq[cid]
            if not units:
                continue
            overlap = len(hyp_terms & units)
            score = overlap / math.sqrt(len(units))
            if score > best:
                best = score
        if best > 0.0:
            ranked.append((target, round(best, 4)))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def corpus_vocabulary(texts: Sequence[str]) -> dict[str, int]:
    """语料词表（纯函数）：全部文本的中文连续段里 2-4 字滑窗片段 -> 文档频
    （出现于多少条文本）。字段名（条码/品牌/上市年份…）天然在内——它们是
    「字段：值」行块的前缀，也是切块原文。"""
    df: Counter[str] = Counter()
    for text in texts:
        grams: set[str] = set()
        for run_m in _CN_RUN_RE.finditer(text):
            run = run_m.group()
            for size in range(SPAN_MIN_CHARS, min(SPAN_MAX_CHARS, len(run)) + 1):
                for i in range(len(run) - size + 1):
                    grams.add(run[i : i + size])
        for gram in grams:
            df[gram] += 1
    return dict(df)


# ---------------------------------------------------------------- 试探边（实跑语义）


@contextmanager
def hypothesis_rules(span: str, target: str) -> Iterator[None]:
    """把 span->target 试探边临时插进 synonyms 规则表（上下文管理器）。

    检索全链路在调用点读 apply_synonyms -> 模块全局 _SYNONYM_RULES，patch 即
    实跑语义（与「收词后真表」同构：新词源不在表内，新边必生效；排序长度
    降序、同长稳定追加在尾——与 synonyms 表的构造规则一致）。退出恢复原表。
    """
    original = synonyms._SYNONYM_RULES
    synonyms._SYNONYM_RULES = tuple(
        sorted((*original, (span, target)), key=lambda rule: len(rule[0]), reverse=True)
    )
    try:
        yield
    finally:
        synonyms._SYNONYM_RULES = original


def _hit_view(hits: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"asset_id": h["asset_id"], "version_no": h["version_no"], "score": round(h["score"], 3)}
        for h in hits
    ]


def _recall(hits: Sequence[dict[str, Any]], cite: dict[str, Any]) -> tuple[bool, bool]:
    r1 = (
        bool(hits)
        and hits[0]["asset_id"] == cite["asset_id"]
        and hits[0]["version_no"] == cite["version_no"]
    )
    r3 = any(h["asset_id"] == cite["asset_id"] for h in hits)
    return r1, r3


# ---------------------------------------------------------------- DB 装配


def load_corpus_rows(db: Any) -> list[tuple[int, int, str, str | None]]:
    """已发布资产的 (asset_id, version_no, chunk, title) 全量（retrieve 同款
    join 语义：指针版本 + published + 未废弃）。"""
    from sqlalchemy import select

    from suite_api.models import Asset, AssetVersion, RetrievalChunk

    return [
        (aid, vno, chunk, title)
        for aid, vno, chunk, title in db.execute(
            select(
                RetrievalChunk.asset_id,
                RetrievalChunk.version_no,
                RetrievalChunk.chunk,
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
                & (Asset.status == "published")
                & (Asset.discarded_at.is_(None)),
            )
            .order_by(RetrievalChunk.id)
        ).all()
    ]


def load_pool(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """问句池装配：多份 collect_questions 清单合并（101 基线 + 104 增量），同句
    取最大 count；按 count 降序。缺文件跳过（增量清单可选）。"""
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for cand in payload["candidates"]:
            key = cand["question"].strip().rstrip("？?")
            if key not in merged or cand["count"] > merged[key]["count"]:
                merged[key] = {
                    "question": cand["question"].strip(),
                    "count": cand["count"],
                    "engine_hit": cand.get("engine_hit"),
                }
    return sorted(merged.values(), key=lambda c: (-c["count"], c["question"]))


def table_words() -> tuple[dict[str, str], list[str]]:
    """同义词表侧目标词：{词 -> 组串}（表内归一假设的落点）与去重词表。"""
    group_of: dict[str, str] = {}
    for group in (*synonyms.SYNONYM_GROUPS, *synonyms._SUBSTRING_GROUPS):
        label = "/".join(group)
        for member in group:
            group_of[member] = label
    return group_of, sorted(group_of)


def _new_evidence_entry(span: str, corpus_terms: frozenset[str]) -> dict[str, Any]:
    return {
        "token": span,
        "probe_ids": [],
        "sources": [],
        "oov_state": span_oov_state(span, corpus_terms),
        "hypotheses": [],
    }


# ---------------------------------------------------------------- 挖掘主流程


def run_dig(args: argparse.Namespace) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    probes = [c for c in golden if c["distribution"] == "oov_syn"]
    pool = load_pool(args.pools)

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        rows = load_corpus_rows(db)
        texts = [chunk for _a, _v, chunk, _t in rows] + [t for _a, _v, _c, t in rows if t]
        corpus_terms: frozenset[str] = frozenset(
            term for text in texts for term in query_terms(text)
        )
        chunk_terms_seq = [query_terms(chunk) for _a, _v, chunk, _t in rows]
        inverted: dict[str, list[int]] = {}
        for cid, terms in enumerate(chunk_terms_seq):
            for term in terms:
                inverted.setdefault(term, []).append(cid)
        vocabulary = corpus_vocabulary(texts)
        corpus_targets = sorted(w for w, df in vocabulary.items() if df >= TARGET_DF_MIN)
        field_names = sorted(
            {
                chunk.split("：")[0].split(":")[0]
                for _a, _v, chunk, _t in rows
                if FIELD_LINE_RE.match(chunk)
            }
        )
        group_of, twords = table_words()
        tword_set, field_set = set(twords), set(field_names)

        probe_base = {c["id"]: retrieve(db, c["question"], top_k=TOP_K) for c in probes}
        pool_base = {c["question"]: retrieve(db, c["question"], top_k=TOP_K) for c in pool}

        # 问句池 token 频次（出现问句数——分拣口径）与待测问句（缺口剪枝后）
        token_freq: Counter[str] = Counter()
        token_questions: dict[str, list[str]] = {}
        for item in pool:
            for span in set(candidate_spans(item["question"])):
                token_freq[span] += 1
                state = span_oov_state(span, corpus_terms)
                # 剪枝：纯表外/部分缺口 token 值得测；全在语料的 token 只在该问句
                # 当前零命中时测（命错资产也可能是词形问题）。
                if state != "in" or item.get("engine_hit") is False:
                    token_questions.setdefault(span, []).append(item["question"])

        evidence: dict[str, dict[str, Any]] = {}

        # 期望资产 -> 块下标集（探针预筛用：按期望锚的块分排目标词）
        asset_chunk_ids: dict[int, frozenset[int]] = {}
        for cid, (aid, _v, _chunk, _title) in enumerate(rows):
            asset_chunk_ids.setdefault(aid, set()).add(cid)
        asset_chunk_ids = {aid: frozenset(cids) for aid, cids in asset_chunk_ids.items()}

        def tier_of(target: str) -> int:
            if target in tword_set:
                return 0
            return 1 if target in field_set else 2

        def test_question(
            *,
            question: str,
            span: str,
            label: str,
            base_hits: list[dict[str, Any]],
            cite: dict[str, Any] | None,
        ) -> list[dict[str, Any]]:
            """单问句单 token 的假设检验。探针（cite 非空）：目标词按**期望资产**
            的块分预筛取前 PROBE_KEEP 个送实跑（按全库绝对分排序会被与锚无关
            的高分短块占坑，真目标反而被截断）；问句池：表内词+字段名合并取前
            若干、语料滑窗词另取。返回全部判收（命中改善）的假设记录（探针按
            期望锚 recall 翻转、池按零命中->命中或 top1 分跃升）。"""
            all_targets = (*twords, *field_names, *corpus_targets)
            if cite is not None:
                ranked = rank_targets(
                    question,
                    span,
                    all_targets,
                    chunk_terms_seq,
                    inverted,
                    only_chunk_ids=asset_chunk_ids.get(cite["asset_id"], frozenset()),
                )
                # 表内词/字段名**全数**送实跑（表内归一假设是任务主通道，不该被
                # 高分滑窗挤掉），滑窗词补足到 PROBE_KEEP 个
                shortlist = [
                    *[t for t, _s in ranked if t in tword_set or t in field_set],
                    *[t for t, _s in ranked if t not in tword_set and t not in field_set][
                        :PROBE_KEEP
                    ],
                ]
            else:
                ranked = rank_targets(question, span, all_targets, chunk_terms_seq, inverted)
                shortlist = [
                    *[
                        t
                        for t, _s in ranked
                        if t in tword_set or (t in field_set and t not in tword_set)
                    ][: TIER_KEEP[0]],
                    *[t for t, _s in ranked if t not in tword_set and t not in field_set][
                        : TIER_KEEP[1]
                    ],
                ]
            improved: list[dict[str, Any]] = []
            for target in shortlist:
                with hypothesis_rules(span, target):
                    hyp_hits = retrieve(db, question, top_k=TOP_K)
                if cite is not None:
                    base_r1, base_r3 = _recall(base_hits, cite)
                    hyp_r1, hyp_r3 = _recall(hyp_hits, cite)
                    if not ((hyp_r1 and not base_r1) or (hyp_r3 and not base_r3)):
                        continue
                    outcome = {"base_r1": base_r1, "hyp_r1": hyp_r1}
                else:
                    base_hit = bool(base_hits)
                    hyp_hit = bool(hyp_hits)
                    base_top = base_hits[0]["score"] if base_hits else 0.0
                    hyp_top = hyp_hits[0]["score"] if hyp_hits else 0.0
                    if not ((hyp_hit and not base_hit) or hyp_top > base_top + POOL_TOP1_GAIN_KEEP):
                        continue
                    outcome = {
                        "base_hit": base_hit,
                        "hyp_hit": hyp_hit,
                        "base_top1": round(base_top, 3),
                        "hyp_top1": round(hyp_top, 3),
                    }
                improved.append(
                    {
                        "target": target,
                        "group": group_of.get(target),
                        "tier": tier_of(target),
                        "question": question,
                        "source": label,
                        "base_top3": _hit_view(base_hits),
                        "hyp_top3": _hit_view(hyp_hits),
                        **outcome,
                    }
                )
            return improved

        # 探针：全量 token（探针是靶子，不按缺口剪枝——「语料已有词但命错资产」
        # 的形态（存放）只有全量测得到）
        for case in probes:
            question = case["question"]
            for span in candidate_spans(question):
                records = test_question(
                    question=question,
                    span=span,
                    label=case["id"],
                    base_hits=probe_base[case["id"]],
                    cite=case["expect"]["cite"],
                )
                if not records:
                    continue
                entry = evidence.setdefault(span, _new_evidence_entry(span, corpus_terms))
                entry["probe_ids"].append(case["id"])
                entry["hypotheses"].extend(records)

        # 问句池：探针已立证的 token 不重复烧实跑；每 token 最多测 4 条来源问句
        for span, questions in sorted(token_questions.items()):
            if span in evidence:
                continue
            for question in questions[:4]:
                records = test_question(
                    question=question,
                    span=span,
                    label="pool",
                    base_hits=pool_base[question],
                    cite=None,
                )
                if not records:
                    continue
                entry = evidence.setdefault(span, _new_evidence_entry(span, corpus_terms))
                entry["sources"].append(question)
                entry["hypotheses"].extend(records)

        candidates = [
            build_candidate(
                span,
                token_freq.get(span, 0),
                entry["probe_ids"],
                entry["sources"],
                entry["oov_state"],
                entry["hypotheses"],
            )
            for span, entry in evidence.items()
        ]
        # 探针证据优先、频次降序、token 字典序稳定
        candidates.sort(key=lambda c: (-len(c["in_probe"]), -c["freq"], c["token"]))

    payload = {
        "source": "dig_oov_synonyms（问句池 101+104 合并 + golden oov_syn 探针）",
        "db": args.db,
        "pool_questions": len(pool),
        "probe_cases": len(probes),
        "corpus_rows": len(rows),
        "table_target_words": len(twords),
        "field_target_words": len(field_names),
        "corpus_target_vocab": len(corpus_targets),
        "candidates": candidates,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _best(cand: dict[str, Any]) -> dict[str, Any]:
        # 榜首假设：探针 r1 翻转 > 表内组归一 > 假设后 top1 分
        return sorted(
            cand["hypotheses"],
            key=lambda h: (
                not h.get("hyp_r1", False),
                h["group"] is None,
                -(h["hyp_top3"][0]["score"] if h["hyp_top3"] else 0.0),
            ),
        )[0]

    priority = [c for c in candidates if c["triage"] == "priority"]
    deferred = [c for c in candidates if c["triage"] == "deferred"]
    print(
        f"问句池 {len(pool)} 问 + 探针 {len(probes)} 条；语料行 {len(rows)}、"
        f"目标词 表内 {len(twords)} / 字段名 {len(field_names)} / 语料 {len(corpus_targets)}"
    )
    print(f"候选 token {len(candidates)}（priority {len(priority)} / deferred {len(deferred)}）")
    for cand in candidates[:60]:
        hyp = _best(cand)
        goal = f"组 {hyp['group']}" if hyp.get("group") else "语料词（新组）"
        flip = (
            f"r1 {int(hyp.get('base_r1', False))}->{int(hyp.get('hyp_r1', False))}"
            if "hyp_r1" in hyp
            else f"top1 {hyp.get('base_top1')}->{hyp.get('hyp_top1')}"
        )
        print(
            f"  [{cand['triage']}] {cand['token']:<6} 频次{cand['freq']:<3} "
            f"探针{','.join(cand['in_probe']) or '-':<16} {cand['oov_state']:<7} "
            f"-> {hyp['target']}（{goal}）{flip}"
            f" base={[(h['asset_id'], h['score']) for h in hyp.get('base_top3', [])[:1]]}"
            f" hyp={[(h['asset_id'], h['score']) for h in hyp.get('hyp_top3', [])[:1]]}"
        )
    print(f"候选清单 -> {args.out}")
    return 0


# ---------------------------------------------------------------- --ab 轻量对照


def run_ab(args: argparse.Namespace) -> int:
    """轻量对照：golden oov_syn + paraphrase 直调 retrieve（run_eval 同口径，
    judge_case 的 recall 判定复用），附问句池命中快照；--against 时打印逐位
    翻转（一词一收词的前后状态工件各一份，diff 即该词的净效应）。"""
    import run_eval as runner
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = [c for c in golden if c["distribution"] in ("oov_syn", "paraphrase")]
    pool = load_pool(args.pools)

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        ab_rows = []
        for case in cases:
            hits = retrieve(db, case["question"], top_k=TOP_K)
            row = runner.judge_case(case, hits, "answer")
            row["top3"] = _hit_view(hits)
            ab_rows.append(row)
        pool_rows = []
        for item in pool:
            hits = retrieve(db, item["question"], top_k=1)
            pool_rows.append(
                {
                    "question": item["question"],
                    "hit": bool(hits),
                    "top1": hits[0]["asset_id"] if hits else None,
                }
            )

    agg = runner.aggregate(ab_rows)
    summary = {
        dist: {
            "n": agg[dist]["n"],
            "recall1": agg[dist]["recall1"],
            "recall3": agg[dist]["recall3"],
        }
        for dist in ("oov_syn", "paraphrase")
    }
    args.ab_out.parent.mkdir(parents=True, exist_ok=True)
    args.ab_out.write_text(
        json.dumps({"summary": summary, "rows": ab_rows, "pool": pool_rows}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    for dist, m in summary.items():
        r1 = "-" if m["recall1"] is None else f"{m['recall1'] * 100:.1f}%"
        r3 = "-" if m["recall3"] is None else f"{m['recall3'] * 100:.1f}%"
        print(f"{dist:<12}{m['n']:>4}  recall@1 {r1:>7}  recall@3 {r3:>7}")
    pool_hit = sum(1 for p in pool_rows if p["hit"])
    print(f"问句池命中 {pool_hit}/{len(pool_rows)}")
    print(f"对照工件 -> {args.ab_out}")

    if args.against and args.against.exists():
        prev = json.loads(args.against.read_text(encoding="utf-8"))
        prev_rows = {r["id"]: r for r in prev["rows"]}
        for row in ab_rows:
            old = prev_rows.get(row["id"])
            if old and (
                old["recall1"] != row["recall1"]
                or old["recall3"] != row["recall3"]
                or [h["asset_id"] for h in old["top3"]] != [h["asset_id"] for h in row["top3"]]
            ):
                print(
                    f"  FLIP {row['id']}: r1 {int(old['recall1'])}->{int(row['recall1'])}"
                    f" r3 {int(old['recall3'])}->{int(row['recall3'])}"
                    f" top3 {[h['asset_id'] for h in old['top3']]}"
                    f"->{[h['asset_id'] for h in row['top3']]}"
                )
        prev_pool = {p["question"]: p for p in prev["pool"]}
        for p in pool_rows:
            old = prev_pool.get(p["question"])
            if old and (old["hit"], old["top1"]) != (p["hit"], p["top1"]):
                print(
                    f"  POOL FLIP: {p['question']}: 命中 {old['hit']}/{old['top1']}"
                    f" -> {p['hit']}/{p['top1']}"
                )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="表外同义词挖掘（第 104 刀收词上游）")
    parser.add_argument("--db", default=DEFAULT_DB, help="演示库 Postgres URL")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="golden JSON 路径")
    parser.add_argument(
        "--pools",
        type=Path,
        nargs="+",
        default=list(DEFAULT_POOLS),
        help="collect_questions 问句池清单（可多份，合并）",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="候选清单 JSON 输出")
    parser.add_argument(
        "--ab", action="store_true", help="轻量对照模式（oov_syn+paraphrase 直调检索层）"
    )
    parser.add_argument("--ab-out", type=Path, default=DEFAULT_AB_OUT, help="--ab 工件输出路径")
    parser.add_argument(
        "--against",
        type=Path,
        default=None,
        help="--ab 与该上一状态工件逐位 diff（一词一收词净效应）",
    )
    args = parser.parse_args(argv)
    if args.ab:
        return run_ab(args)
    return run_dig(args)


if __name__ == "__main__":
    raise SystemExit(main())
