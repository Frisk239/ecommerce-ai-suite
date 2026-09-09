"""评测尺大集生成器（第 35 刀，goal §6.2.2）：演示库已发布资产 -> 四分布 golden 集。

四分布（docs/research/rag-accuracy-engineering.md §3 协议）：
- positive 正例 40%：从已发布资产的「字段：值」块 / QA 块 / 标题抽实体句改问，
  expect={cite:{asset_id,version_no}}——期望引用**来源资产本身**。
- paraphrase 同义改写 25%：正例问句过 apply_synonyms（双向归一）+ 句式变换
  （…是多少->…有多少 等），expect 同源——不换实体只换说法。
- confusion 跨商品混淆 15%：取两资产共有 bigram 组问句，expect=词法分更高
  （score 相同取 asset_id 小）的那个资产——正确性由 runner 实测判定。
- refusal 应拒答 20%：硬编码无证据话题清单，生成时用与检索同口径的
  query_terms/score_chunk 预检——库里有对应资产的自动剔除（如积分文档在库则
  会员积分话题作废），expect={refuse:true}。

固定种子 random.Random(42)：同库同种子两次生成逐字节一致。核心是纯函数
build_cases(assets, refusal_topics, rng)——assets 用 dict 行喂，离线可测
（apps/api/tests/test_rag_eval_tools.py）。

用法（仓库根目录）：
    uv run python scripts/eval/generate_golden.py                 # -> out/golden_large.json
    uv run python scripts/eval/generate_golden.py --out 其他路径.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from suite_api.services.retrieval import FIELD_LINE_RE, query_terms, score_chunk
from suite_api.services.synonyms import apply_synonyms

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / "out" / "golden_large.json"
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
SEED = 42

# 目标总量与分布配比（spec Must 1：40/25/15/20；演示库 52 资产 -> 总量落在 80-150）
TARGET_TOTAL = 100
RATIOS: dict[str, float] = {
    "positive": 0.40,
    "paraphrase": 0.25,
    "confusion": 0.15,
    "refusal": 0.20,
}

# 每资产正例候选上限（把 40 条正例摊到尽量多的资产上，避免单资产霸榜）
PER_ASSET_POS_CAP = 2

# 元数据字段（来源/许可）不进问句——它们是采集脚注，不是商品证据
_META_FIELDS = {"来源", "许可"}
# 字段值超过这个长度不成问句（OFF 营养成分长行拼成问句不可读）
_MAX_FIELD_VALUE_CHARS = 30
# 对话轮前缀（retrieval.chunk_dialogue 的行形）——不是「字段：值」语义
_SPEAKER_PREFIXES = ("顾客：", "客服：")

# 应拒答候选话题：探测后库里无对应资产的才入集（顺序即取用顺序，固定不 shuffle）。
# 注：会员积分/积分商城刻意留在清单里演示「库里有积分文档则自动剔除」（资产 17
# 的积分规则块命中 积分 bigram）。
REFUSAL_TOPIC_QUESTIONS: tuple[str, ...] = (
    "发票可以开电子的吗",
    "宠物用品区在几楼",
    "机票改签找谁",
    "会员积分怎么兑换礼品",
    "积分商城在哪里",
    "以旧换新怎么办理",
    "支持分期付款吗",
    "可以货到付款吗",
    "海外能直接下单吗",
    "礼品卡怎么充值",
    "话费充值有优惠吗",
    "二手回收怎么估价",
    "医疗器械能退吗",
    "汽车用品有推荐的吗",
    "母婴用品有活动吗",
    "你们营业到几点",
    "门店地址在哪里",
    "支持到店自提吗",
    "降价了能补差价吗",
    "延保服务怎么买",
    "直播间优惠券怎么领",
    "数码相机防水吗",
    "无人机保修多久",
    "运动鞋有增高款吗",
)


# ---------------------------------------------------------------- 资产行（纯形状）


def asset_row(
    asset_id: int,
    version_no: int,
    title: str | None,
    kind: str,
    source_kind: str,
    chunks: Sequence[str],
) -> dict[str, Any]:
    """资产行归一（DB 读取与测试 fixture 共用的形状）。"""
    return {
        "asset_id": int(asset_id),
        "version_no": int(version_no),
        "title": title,
        "kind": kind,
        "source_kind": source_kind,
        "chunks": list(chunks),
    }


def display_title(title: str | None) -> str:
    """问句里的资产名：缺标题给中性占位（不编造属性）。"""
    cleaned = (title or "").strip()
    return cleaned or "该商品"


def subject(title: str | None) -> str:
    """字段模板的问句主体：剥采集脚注与分隔符尾巴，取商品名。

    「M&M white 规格（OFF）」->「M&M white」；「钛钢保温杯 · 规格」->
    「钛钢保温杯」；评论标题（「水果评论 · …」）保留全名（主体就是评论本身）。
    """
    t = display_title(title)
    for suffix in (" 规格（OFF）", " 规格(OFF)"):
        if t.endswith(suffix):
            trimmed = t[: -len(suffix)].strip()
            if trimmed:
                return trimmed
    if " · " in t:
        head = t.split(" · ", 1)[0].strip()
        if head:
            return head
    return t


# ---------------------------------------------------------------- 正例候选


def qa_pair_question(chunk: str) -> str | None:
    """「问：…\\n答：…」块 -> 直接复用问句部分（QA 成块天然是问题的证据单元）。"""
    if not chunk.startswith("问："):
        return None
    head = chunk.split("\n", 1)[0]
    q = head.removeprefix("问：").strip()
    while q and q[-1] in "？?。.":
        q = q[:-1].rstrip()
    return q or None


def field_pair(chunk: str) -> tuple[str, str] | None:
    """「字段：值」块 -> (字段, 值)；对话轮/元数据脚注/超长值不成问句。"""
    if chunk.startswith(_SPEAKER_PREFIXES):
        return None
    if not FIELD_LINE_RE.match(chunk):
        return None
    for sep in ("：", ":"):
        if sep in chunk:
            field, value = chunk.split(sep, 1)
            field = field.strip()
            value = value.strip()
            break
    else:
        return None
    if not field or not value:
        return None
    if field in _META_FIELDS or len(value) > _MAX_FIELD_VALUE_CHARS:
        return None
    return field, value


def positive_candidates(asset: dict[str, Any]) -> list[str]:
    """单资产的正例问句候选（去重保序；每资产至少有「{标题}怎么样」兜底）。"""
    name = subject(asset["title"])
    cands: list[str] = []
    for chunk in asset["chunks"]:
        q = qa_pair_question(chunk)
        if q:
            cands.append(q)
            continue
        pair = field_pair(chunk)
        if pair:
            field, value = pair
            if any(ch.isdigit() for ch in value):
                cands.append(f"{name}的{field}是{value}吗")
                cands.append(f"{name}的{field}是多少")
            else:
                cands.append(f"{name}的{field}是什么")
    cands.append(f"{display_title(asset['title'])}怎么样")
    deduped: list[str] = []
    for q in cands:
        if q not in deduped:
            deduped.append(q)
    return deduped


def rephrase(question: str) -> str:
    """同义改写器：同义词表双向归一 + 句式变换（不依赖 LLM，空 key 可复现）。"""
    q = apply_synonyms(question)
    if q.endswith("是多少"):
        return q[: -len("是多少")] + "有多少"
    if q.endswith("是什么"):
        return q[: -len("是什么")] + "是多少"
    if q.endswith("怎么样"):
        return "请问" + q[: -len("怎么样")] + "值得入手吗"
    if q.endswith("吗"):
        return q[: -len("吗")] + "，对吧"
    return q + "，麻烦告知"


# ---------------------------------------------------------------- 混淆组


def _asset_bigrams(asset: dict[str, Any]) -> frozenset[str]:
    terms: set[str] = set()
    for chunk in asset["chunks"]:
        terms |= query_terms(chunk)
    return frozenset(terms)


def confusion_word_score(word: str, asset: dict[str, Any]) -> float:
    """混淆问句「{word}怎么样」在该资产的词法分：含该词的块里得分最高者。

    与检索打分同口径（score_chunk）——「{word}怎么样」的有效词法单元就是 word
    本身（怎么样全为停用字组合）。两资产同分时调用方取 asset_id 小者。
    """
    terms = query_terms(f"{word}怎么样")
    return max((score_chunk(terms, chunk) for chunk in asset["chunks"]), default=0.0)


def build_confusions(assets: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """跨商品混淆组：两资产共有 CJK bigram 组问句，expect=词法分更高的那个资产。

    词选口径：纯汉字 bigram（英文转写里的碎片 bigram 如「It」「df」不成问句），
    按**出现资产数**降序再按词序稳定排序——支持面最广的词（净含量/物流/保温
    这类真实跨商品词）优先成题，选取顺序与 rng 无关（同库完全可复现）；
    一词一题；问句资产对取支持该词的前两个资产（asset_id 升序，与检索并列
    排序一致）。期望资产由词法分确定（同分取 asset_id 小）。
    """
    sigs = [_asset_bigrams(a) for a in assets]
    support: dict[str, list[int]] = {}
    for idx, sig in enumerate(sigs):
        for word in sig:
            if all("\u4e00" <= ch <= "\u9fff" for ch in word):
                support.setdefault(word, []).append(idx)
    ranked = sorted(
        (word for word, idxs in support.items() if len(idxs) >= 2),
        key=lambda w: (-len(support[w]), w),
    )
    out: list[dict[str, Any]] = []
    for word in ranked:
        if len(out) >= n:
            break
        left, right = assets[support[word][0]], assets[support[word][1]]
        score_left = confusion_word_score(word, left)
        score_right = confusion_word_score(word, right)
        if score_left > score_right:
            expect_asset = left
        elif score_right > score_left:
            expect_asset = right
        else:
            expect_asset = left if left["asset_id"] <= right["asset_id"] else right
        out.append(
            {
                "word": word,
                "question": f"{word}怎么样",
                "expect_asset": expect_asset,
            }
        )
    return out


# ---------------------------------------------------------------- 拒答组


def topic_has_evidence(topic: str, assets: list[dict[str, Any]]) -> bool:
    """话题探测：与检索同口径的词法打分——任一已发布块得分 >0 即视为库里有证据。

    纯停用词问句（无有效词法单元）视为有证据丢弃——它对拒答判定没有评测价值。
    """
    terms = query_terms(topic)
    if not terms:
        return True
    return any(
        score_chunk(terms, chunk) > 0 for asset in assets for chunk in asset["chunks"]
    )


def surviving_refusal_topics(
    assets: list[dict[str, Any]], topics: Sequence[str] = REFUSAL_TOPIC_QUESTIONS
) -> list[str]:
    """探测后仍无证据的话题（保持清单顺序，取前 N 即拒答组）。"""
    return [t for t in topics if not topic_has_evidence(t, assets)]


# ---------------------------------------------------------------- 四分布装配


def _cite_expect(asset: dict[str, Any]) -> dict[str, Any]:
    return {"cite": {"asset_id": asset["asset_id"], "version_no": asset["version_no"]}}


def _case(case_id: str, distribution: str, question: str, expect: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": case_id,
        "distribution": distribution,
        "question": question,
        "expect": expect,
    }


def build_cases(
    assets: list[dict[str, Any]],
    refusal_topics: Sequence[str] = REFUSAL_TOPIC_QUESTIONS,
    rng: random.Random | None = None,
    *,
    total: int = TARGET_TOTAL,
) -> list[dict[str, Any]]:
    """四分布装配（纯函数）：同库同种子逐字节复现。

    配比按 spec Must 1（40/25/15/20）取整；候选不足时该分布实际条数缩水、
    总量随之变小——诚实反映库规模，不硬凑。
    """
    rng = rng or random.Random(SEED)
    n_pos = round(total * RATIOS["positive"])
    n_syn = round(total * RATIOS["paraphrase"])
    n_conf = round(total * RATIOS["confusion"])
    n_ref = round(total * RATIOS["refusal"])

    # 正例池：每资产至多 PER_ASSET_POS_CAP 条（rng 决定挑哪几条），洗牌后截取
    pool: list[tuple[dict[str, Any], str]] = []
    for asset in assets:
        cands = positive_candidates(asset)
        rng.shuffle(cands)
        for q in cands[:PER_ASSET_POS_CAP]:
            pool.append((asset, q))
    rng.shuffle(pool)

    cases: list[dict[str, Any]] = []
    positives = pool[:n_pos]
    for i, (asset, q) in enumerate(positives, 1):
        cases.append(_case(f"pos-{i:03d}", "positive", q, _cite_expect(asset)))
    # 同义改写：从剩余池取基底，expect 同源
    for i, (asset, q) in enumerate(pool[n_pos : n_pos + n_syn], 1):
        cases.append(_case(f"syn-{i:03d}", "paraphrase", rephrase(q), _cite_expect(asset)))
    # 混淆组
    for i, conf in enumerate(build_confusions(assets, n_conf), 1):
        cases.append(
            _case(f"conf-{i:03d}", "confusion", conf["question"], _cite_expect(conf["expect_asset"]))
        )
    # 拒答组：清单顺序截取（探测剔除已在该清单外完成）
    for i, topic in enumerate(surviving_refusal_topics(assets, refusal_topics)[:n_ref], 1):
        cases.append(_case(f"ref-{i:03d}", "refusal", topic, {"refuse": True}))
    return cases


# ---------------------------------------------------------------- DB 读取（脚本入口专用）


def load_published_assets(db: Any) -> list[dict[str, Any]]:
    """演示库已发布资产（指针非空）+ 其当前版本全部切块，按 asset_id 稳定排序。"""
    from sqlalchemy import select

    from suite_api.models import Asset, AssetVersion, RetrievalChunk

    assets = db.execute(
        select(Asset.id, Asset.kind, Asset.source_kind, Asset.title, AssetVersion.version_no)
        .join(AssetVersion, AssetVersion.id == Asset.current_published_version_id)
        .where(Asset.current_published_version_id.isnot(None))
        .order_by(Asset.id)
    ).all()
    rows: list[dict[str, Any]] = []
    for asset_id, kind, source_kind, title, version_no in assets:
        chunks = db.scalars(
            select(RetrievalChunk.chunk)
            .where(
                (RetrievalChunk.asset_id == asset_id)
                & (RetrievalChunk.version_no == version_no)
            )
            .order_by(RetrievalChunk.seq)
        ).all()
        rows.append(asset_row(asset_id, version_no, title, kind, source_kind, chunks))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测尺大集生成器（四分布，固定种子）")
    parser.add_argument("--db", default=DEFAULT_DB, help="演示库 Postgres URL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 golden JSON 路径")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        assets = load_published_assets(db)
    rng = random.Random(SEED)
    cases = build_cases(assets, rng=rng)

    by_dist: dict[str, int] = {}
    for case in cases:
        by_dist[case["distribution"]] = by_dist.get(case["distribution"], 0) + 1
    used = {c["question"] for c in cases if c["distribution"] == "refusal"}
    filtered = [t for t in REFUSAL_TOPIC_QUESTIONS if topic_has_evidence(t, assets)]
    overflow = [
        t
        for t in REFUSAL_TOPIC_QUESTIONS
        if t not in filtered and t not in used  # 有存活但超出 20% 配比被截断
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"资产行：{len(assets)}（已发布、含切块）")
    print(f"分布：{json.dumps(by_dist, ensure_ascii=False)}，共 {len(cases)} 条")
    print(f"拒答候选被探测剔除 {len(filtered)} 条（库里有对应资产）：{filtered}")
    print(f"拒答候选超配比截断 {len(overflow)} 条：{overflow}")
    print(f"种子：{SEED} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
