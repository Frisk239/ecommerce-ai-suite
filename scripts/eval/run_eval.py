"""评测尺 runner（第 35 刀）：golden 大集逐条直调 retrieve+compose_answer -> 分层指标。

不 mock 引擎：与线上完全相同的检索打分（services/retrieval）与降级模板组装
（services/answer），直接吃 --db 会话；检索层指标零 LLM 依赖（空 key 可复现）。
可选 --judge 对「有命中且未拒答」的条目调 complete_chat 评 faithfulness
（回答是否只由命中块支持）——LLMNotConfigured 跳过该列；LLMError 每条有限
重试，重试尽该条记未评上并继续（第 87 刀：此前单条失败会终止整列，judge
从未跑全过），失败条目在报告注明。

--judge-llm（第 102 刀）：**生成路径**忠实度观察——87 刀的 judge 只评模板
路径（runner 直调 compose_answer），本模式评真 LLM 生成的回答：
- 存量：service_messages 里 kind='answer' 且带 citations 的 agent 消息，
  剔除降级模板形状（compose_answer 两类前缀=模板路径指纹；fallback 布尔
  是运行时键不落消息表，形状判定是唯一判据），配同会话前一条顾客问句，
  (问句, 回答) 全同的重复只留首条；
- 新问：FRESH_QUESTIONS 现场走线上 run_ask 真生成（新鲜度优先：观察应评
  当前引擎行为，不是历史快照），answer+生成的入评；
- 证据面：该消息 citations 指向 (asset_id, version_no) 的**全部**切块
  （retrieval_chunks 发布留档，比模型 prompt 实际可见的 top-2 证据宽——
  判的是「答案 vs 引用源」的忠实度）；判卷复用 87 刀 judge_with_retry 的
  重试纪律与 _supported_verdict 解析，同款严格协议 prompt（输出多要一句
  reason 供逐条留档）。
一次性观察：非确定性不进 CI、不进可复现评测尺（口径声明随报告输出）。

指标（docs/research/rag-accuracy-engineering.md §3 协议）：
- recall@1/@3：cite 组（positive/paraphrase/confusion/oov_syn/sem_neg）期望资产进 top-1/top-3；
- 拒答率：refusal 组实际拒答比例（kind=refusal）；
- 误拒率：positive 组被拒答的比例（宁缺勿滥的反面代价，单独成列）；
- 混淆@1：confusion 组 top-1 恰为期望资产的比例（跨商品共有词是否被词法分
  拉向「证据更短更实」的资产）；
- 表外同义（第 101 刀第五分布 oov_syn）：同义词表之外的自然改写，期望锚与
  表内基底同句相同——recall@1 即表外泛化实测，embedding 评估门的裁判列；
- 语义负例（第 107a 刀第六分布 sem_neg）：否定语义（「不支持退货吗」——不/没
  是停用字，词法层否定式与正向完全同命中；稠密检索的已知弱向：否定句向量≈
  肯定句向量，检索应命中同一块而模型不被否定形态带偏）+ 语义近邻（「保温杯的
  保修政策」——实体词与意图分属两资产，词法被实体亲和拉偏的形态）——向量
  上场前先把靶子钉进评测集；
- 排序三指标（第 107a 刀，对全部 cite 组；refusal 无期望锚不适用）：
  MRR（期望资产首次进榜位次的倒数 1/rank，不中=0）、nDCG@3（期望资产首次
  进榜位次的 log2(i+1) 折扣增益，p1=1.0/p2≈0.63/p3=0.5——IDCG 取 rank1 常数
  归一）、噪声率@3（top3 里非期望资产块占比 (3-期望块数)/3，期望不在=100%）；
- 忠实度（--judge）：answered 条目中被 judge 判 supported 的比例。

统计全为纯函数（judge_case/aggregate/_supported_verdict/format_table），
离线单测见 apps/api/tests/test_rag_eval_tools.py。

用法（仓库根目录）：
    uv run python scripts/eval/run_eval.py --db postgresql://suite:suite@localhost:5433/suite
    uv run python scripts/eval/run_eval.py --db ... --judge --report scripts/eval/out/report.md
    uv run python scripts/eval/run_eval.py --db ... --judge-llm --report scripts/eval/out/report-judge-102.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from suite_api.services.retrieval import retrieve

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"
TOP_K = 3
# 六分布（第 107a 刀起）：四原始分布 + oov_syn（表外同义探针，101 刀）+ sem_neg
# （语义负例，107a 刀——否定语义/语义近邻两类：稠密检索已知弱向的提前钉死，
# 向量上场前评测集先备好靶子）。
DISTRIBUTIONS = ("positive", "paraphrase", "confusion", "refusal", "oov_syn", "sem_neg")
JUDGE_ATTEMPTS = 3
JUDGE_RETRY_WAIT_SECONDS = 2.0

JUDGE_SYSTEM_PROMPT = (
    "你是严格的检索增强问答评审。给定问题、检索证据与回答，逐句评答案忠实度"
    "（faithfulness）：回答的每条陈述都必须在证据里有原文依据（数值、实体、"
    "措辞一致或直接可导出）。注意：回答是否切题、是否完整不在评审范围——只评"
    "「有没有编证据外的东西」；不同句子可以来自不同证据条（各自有依据即可），"
    "多条证据之间内容不同不构成违规。任一陈述在证据里找不到原文依据 => false。"
    '只输出 JSON：{"supported": true} 或 {"supported": false}。'
)

# 第 102 刀生成路径观察：严格协议判据与 87 刀逐字相同，只有输出格式多要一句
# reason（供逐条 verdict 留档「原因首句」）；解析仍走 _supported_verdict。
JUDGE_LLM_SYSTEM_PROMPT = (
    "你是严格的检索增强问答评审。给定问题、检索证据与回答，逐句评答案忠实度"
    "（faithfulness）：回答的每条陈述都必须在证据里有原文依据（数值、实体、"
    "措辞一致或直接可导出）。注意：回答是否切题、是否完整不在评审范围——只评"
    "「有没有编证据外的东西」；不同句子可以来自不同证据条（各自有依据即可），"
    "多条证据之间内容不同不构成违规。任一陈述在证据里找不到原文依据 => false。"
    '只输出 JSON：{"supported": true/false, "reason": "一句中文：不支持的那句'
    '陈述或整体判定依据"}。'
)

# 现场真问清单（第 102 刀）：数码店（规格/图片描述/政策/刻字）+ 存量金标问句
# 各取其半——全部是纯 RAG 问法（不带订单号/库存词/转人工，避免工具步事实进
# 答案造成证据外陈述的观察噪音；那类混合形态由存量样本自然携带）。
FRESH_QUESTIONS: tuple[str, ...] = (
    "Vivo Y300 是什么品牌",
    "Xperia Ear Duo 什么时候上市的",
    "显示器支架能调节吗",
    "蓝牙耳机电池保修多久",
    "塑料外壳的键盘能刻字吗",
    "退货运费多少钱",
    "发票怎么开具",
    "保温杯的净含量是多少",
    "钛钢保温壶的材质是什么",
    "羊绒围巾起球怎么处理",
)

# compose_answer 的两类固定前缀=模板路径（降级回落）的形状指纹：fallback 布尔
# 是 SSE 运行时键不落消息表（ADR 0033「消息表不加列」），存量生成样本只能按
# 形状判。真 LLM 生成不会稳定复现这两个前缀（系统提示要求直接给结论）。
TEMPLATE_ANSWER_PREFIXES: tuple[str, ...] = (
    "根据已发布的规格文档《",
    "根据已发布的客服对话记录",
)


# ---------------------------------------------------------------- 单条判定（纯函数）


def reciprocal_rank(hits: list[dict[str, Any]], want_asset_id: int) -> float:
    """MRR 单条（第 107a 刀）：期望资产（任意版本，与 recall@3 同口径）首次进榜
    位次的倒数 1/rank——top1=1.0/top2=0.5/top3≈0.333；不在 top-k 或零命中=0。"""
    for rank, hit in enumerate(hits[:TOP_K], start=1):
        if hit["asset_id"] == want_asset_id:
            return 1.0 / rank
    return 0.0


def discounted_gain(hits: list[dict[str, Any]], want_asset_id: int) -> float:
    """nDCG@3 单条（第 107a 刀）：期望资产首次进榜位次 i 的折扣增益 1/log2(i+1)
    ——p1=1.0/p2≈0.6309/p3=0.5；不在 top-k=0。

    二值相关（命中即 gain=1）且 IDCG 取 rank1 常数 1 归一——期望资产多块进榜
    时只记首块位次（多块的贡献由噪声率吸收），保证取值域 [0,1] 且与
    「p1=1/p2=0.63/p3=0.5」口径逐位一致。"""
    for rank, hit in enumerate(hits[:TOP_K], start=1):
        if hit["asset_id"] == want_asset_id:
            return 1.0 / math.log2(rank + 1)
    return 0.0


def noise_rate(hits: list[dict[str, Any]], want_asset_id: int) -> float:
    """噪声率@3 单条（第 107a 刀）：top3 里非期望资产块的占比 (3-期望块数)/3。

    「相关」= 属于期望资产（任意版本）的块（同资产多块都算相关）；期望资产
    不在 top3（含零命中）时=100%；命中列表短于 3 时空位按非相关计（分母恒 3，
    与 spec 公式逐字一致）。"""
    expected = sum(1 for hit in hits[:TOP_K] if hit["asset_id"] == want_asset_id)
    return (TOP_K - expected) / TOP_K


def judge_case(case: dict[str, Any], hits: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """单条判定：hits=retrieve(top_k) 结果、kind=compose_answer().kind -> 行记录。

    recall@1 要求资产与版本都中；recall@3 资产命中即可（引到同资产更早版本
    也说明检索找到了证据源，版本漂移是发布语义不是检索错误）。排序三指标
    （MRR/nDCG@3/噪声率@3，107a 刀）同为资产级口径，随 cite 期望一并落列。
    """
    row: dict[str, Any] = {
        "id": case["id"],
        "distribution": case["distribution"],
        "refused": kind == "refusal",
        "answered": kind == "answer",
        "hit_asset_ids": [hit["asset_id"] for hit in hits],
        "recall1": None,
        "recall3": None,
        "mrr": None,
        "ndcg3": None,
        "noise3": None,
        "confusion_top1": None,
        "faithful": None,
    }
    expect = case["expect"]
    if "cite" in expect:
        want = expect["cite"]
        row["recall1"] = bool(hits) and hits[0]["asset_id"] == want["asset_id"] and hits[0][
            "version_no"
        ] == want["version_no"]
        row["recall3"] = any(hit["asset_id"] == want["asset_id"] for hit in hits)
        row["mrr"] = reciprocal_rank(hits, want["asset_id"])
        row["ndcg3"] = discounted_gain(hits, want["asset_id"])
        row["noise3"] = noise_rate(hits, want["asset_id"])
        if case["distribution"] == "confusion":
            row["confusion_top1"] = bool(hits) and hits[0]["asset_id"] == want["asset_id"]
    return row


def _mean(values: Sequence[bool]) -> float | None:
    return round(sum(1 for v in values if v) / len(values), 4) if values else None


def _mean_float(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按分布聚合 + overall（纯函数）：None 表示该分布不适用该指标。"""
    agg: dict[str, dict[str, Any]] = {}
    for dist in (*DISTRIBUTIONS, "overall"):
        group = rows if dist == "overall" else [r for r in rows if r["distribution"] == dist]
        agg[dist] = {
            "n": len(group),
            "recall1": _mean([r["recall1"] for r in group if r["recall1"] is not None]),
            "recall3": _mean([r["recall3"] for r in group if r["recall3"] is not None]),
            "mrr": _mean_float([r["mrr"] for r in group if r["mrr"] is not None]),
            "ndcg3": _mean_float([r["ndcg3"] for r in group if r["ndcg3"] is not None]),
            "noise3": _mean_float([r["noise3"] for r in group if r["noise3"] is not None]),
            "refusal_rate": _mean([r["refused"] for r in group if r["distribution"] == "refusal"]),
            "false_refusal": _mean([r["refused"] for r in group if r["distribution"] == "positive"]),
            "confusion_top1": _mean(
                [r["confusion_top1"] for r in group if r["confusion_top1"] is not None]
            ),
            "answered": sum(1 for r in group if r["answered"]),
            "judged": sum(1 for r in group if r["faithful"] is not None),
            "faithful": _mean([r["faithful"] for r in group if r["faithful"] is not None]),
        }
    return agg


def format_table(agg: dict[str, dict[str, Any]], *, judge_on: bool) -> str:
    """stdout/报告共用的分层表（纯函数）。None 指标显示 -。"""
    header = (
        f"{'分布':<12}{'条数':>5}{'recall@1':>10}{'recall@3':>10}"
        f"{'MRR':>8}{'nDCG@3':>9}{'噪声@3':>9}"
        f"{'拒答率':>8}{'误拒率':>8}{'混淆@1':>8}"
    )
    if judge_on:
        header += f"{'忠实度':>8}{'评/答':>7}"
    lines = [header]
    for dist in (*DISTRIBUTIONS, "overall"):
        m = agg[dist]

        def pct(value: float | None) -> str:
            return "-" if value is None else f"{value * 100:.1f}%"

        def num(value: float | None) -> str:
            return "-" if value is None else f"{value:.4f}"

        line = (
            f"{dist:<12}{m['n']:>5}{pct(m['recall1']):>10}{pct(m['recall3']):>10}"
            f"{num(m['mrr']):>8}{num(m['ndcg3']):>9}{pct(m['noise3']):>9}"
            f"{pct(m['refusal_rate']):>8}{pct(m['false_refusal']):>8}{pct(m['confusion_top1']):>8}"
        )
        if judge_on:
            line += f"{pct(m['faithful']):>8}{m['judged']:>4}/{m['answered']:<3}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- judge（--judge 可选路）


def _supported_verdict(raw: str) -> bool | None:
    """LLM 输出 -> supported 布尔；解析不出返回 None（调用方计为未评上）。"""
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group()).get("supported")
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
        except ValueError:
            pass
    lowered = raw.lower()
    if "true" in lowered:
        return True
    if "false" in lowered:
        return False
    return None


def build_judge_prompt(question: str, chunks: Sequence[str], answer: str) -> str:
    evidence = "\n".join(f"- {chunk}" for chunk in chunks)
    return f"问题：{question}\n检索证据：\n{evidence}\n回答：{answer}"


def _judge_raw(question: str, chunks: Sequence[str], answer: str, system_prompt: str) -> str:
    """一次 judge 调用的原始输出（87 刀与 102 刀共用；LLMError 上抛给重试层）。"""
    from suite_api.services.llm import complete_chat

    async def _run() -> str:
        return await complete_chat(
            system_prompt, build_judge_prompt(question, chunks, answer)
        )

    return asyncio.run(_run())


def judge_one(question: str, chunks: Sequence[str], answer: str) -> bool:
    """单条 faithfulness 评审（走线上同款 complete_chat；LLMError 上抛给重试层）。"""
    from suite_api.services.llm import LLMError

    verdict = _supported_verdict(_judge_raw(question, chunks, answer, JUDGE_SYSTEM_PROMPT))
    if verdict is None:
        raise LLMError("judge 输出不可解析") from None
    return verdict


_T = TypeVar("_T")


def _with_retry(call: Callable[[], _T], *, attempts: int) -> _T | None:
    """judge 单条 × 有限重试（87 刀纪律，两条 judge 路共用）：重试尽返回 None
    （fail-soft，不终止整列）。线上 llm 契约刻意 20s/0 重试（0018/0033），
    脚本层的韧性放这里，不动 services/llm。"""
    from suite_api.services.llm import LLMError

    for attempt in range(attempts):
        try:
            return call()
        except LLMError:
            if attempt < attempts - 1:
                time.sleep(JUDGE_RETRY_WAIT_SECONDS)
    return None


def judge_with_retry(
    question: str, chunks: Sequence[str], answer: str, *, attempts: int = JUDGE_ATTEMPTS
) -> bool | None:
    """87 刀模板路径 judge：返回 None 计入「未评上」，分母只算评上的，失败条目
    由调用方收集进报告注记。"""
    return _with_retry(
        lambda: judge_one(question, chunks, answer), attempts=attempts
    )


# ---------------------------------------------------------------- 生成路径观察（--judge-llm，第 102 刀）


def _reason_from_raw(raw: str) -> str:
    """judge 原始输出 -> reason 句（JSON 里的 reason 字段；回落首个非空行）。"""
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group()).get("reason")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except ValueError:
            pass
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    return line


def reason_first_sentence(reason: str) -> str:
    """原因首句（。！？与换行切；无终止符整段即首句）。"""
    match = re.match(r"[^。！？\n]*[。！？]?", reason.strip())
    return match.group() if match else reason.strip()


def judge_llm_one(question: str, chunks: Sequence[str], answer: str) -> tuple[bool, str]:
    """生成路径单条评审：87 刀同款严格协议（JUDGE_LLM_SYSTEM_PROMPT 只多要一句
    reason），解析复用 _supported_verdict；LLMError 上抛给重试层。"""
    from suite_api.services.llm import LLMError

    raw = _judge_raw(question, chunks, answer, JUDGE_LLM_SYSTEM_PROMPT)
    verdict = _supported_verdict(raw)
    if verdict is None:
        raise LLMError("judge 输出不可解析") from None
    return verdict, _reason_from_raw(raw)


def judge_llm_with_retry(
    question: str, chunks: Sequence[str], answer: str, *, attempts: int = JUDGE_ATTEMPTS
) -> tuple[bool, str] | None:
    """生成路径 judge × 87 刀同款重试纪律：返回 (verdict, reason) 或 None。"""
    return _with_retry(
        lambda: judge_llm_one(question, chunks, answer), attempts=attempts
    )


def is_template_answer(content: str) -> bool:
    """降级模板（compose_answer）形状判定：两类固定前缀。"""
    return content.startswith(TEMPLATE_ANSWER_PREFIXES)


def build_generated_samples(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """消息行（时序）-> 生成路径样本（纯函数，第 102 刀）。

    messages 形状 {id, session_id, role, content, kind, citations}：
    - 取 kind='answer' 且 citations 非空的 agent 消息（RAG 路径；citations 空的
      answer 是工具/目录模板面，不在生成路径）；
    - 剔除模板回落形状（is_template_answer——fallback 不落消息表，前缀是唯一
      判据）；
    - 配同会话该回答**前一条**顾客问句（多轮语境下即触发它的那句）；
    - (问句, 回答) 全同的重复只留首条（重复探针不重复计票）。
    """
    last_question: dict[Any, str] = {}
    samples: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for msg in messages:
        if msg["role"] == "customer":
            last_question[msg["session_id"]] = msg["content"]
            continue
        if msg["role"] != "agent" or msg["kind"] != "answer":
            continue
        citations = msg["citations"] or []
        if not citations or is_template_answer(msg["content"]):
            continue
        question = last_question.get(msg["session_id"])
        if not question:
            continue
        key = (question, msg["content"])
        if key in seen:
            continue
        seen.add(key)
        samples.append(
            {
                "source": "存量",
                "message_id": msg["id"],
                "session_id": msg["session_id"],
                "question": question,
                "answer": msg["content"],
                "citations": citations,
            }
        )
    return samples


def judge_llm_summary(
    total: int, verdicts: Sequence[tuple[bool, str] | None]
) -> dict[str, Any]:
    """汇总（纯函数）：judged=评上数、supported_rate 分母只算评上的（87 刀口径）。"""
    judged = [v for v in verdicts if v is not None]
    supported = sum(1 for verdict, _reason in judged if verdict)
    return {
        "samples": total,
        "judged": len(judged),
        "supported": supported,
        "failed": total - len(judged),
        "supported_rate": round(supported / len(judged), 4) if judged else None,
    }


def _format_fresh_stats(fresh_stats: dict[str, int]) -> str:
    parts = [f"问 {fresh_stats.get('asked', 0)} 条"]
    if fresh_stats.get("generated"):
        parts.append(f"生成作答 {fresh_stats['generated']}")
    if fresh_stats.get("fallback"):
        parts.append(f"模板回落 {fresh_stats['fallback']}")
    if fresh_stats.get("refusal"):
        parts.append(f"拒答 {fresh_stats['refusal']}")
    if fresh_stats.get("handoff"):
        parts.append(f"转人工 {fresh_stats['handoff']}")
    return "、".join(parts)


def judge_llm_report(
    samples: Sequence[dict[str, Any]],
    verdicts: Sequence[tuple[bool, str] | None],
    *,
    fresh_stats: dict[str, int],
    db_url: str,
) -> str:
    """逐条 verdict + 汇总 + 口径声明（纯函数；stdout 与 --report 工件共用）。"""
    lines: list[str] = []
    summary = judge_llm_summary(len(samples), verdicts)
    stock_n = sum(1 for s in samples if s["source"] == "存量")
    lines.append(f"演示库：{db_url}")
    lines.append(
        f"样本：存量生成消息 {stock_n} 条（去重后）+ 现场真问 "
        f"{len(samples) - stock_n} 条（{_format_fresh_stats(fresh_stats)}）"
    )
    lines.append("")
    for idx, (sample, verdict) in enumerate(zip(samples, verdicts, strict=True), start=1):
        cites = ",".join(f"{c['asset_id']}/v{c['version_no']}" for c in sample["citations"])
        head = (
            f"{idx}. [{sample['source']}|msg {sample['message_id']}] "
            f"问：{sample['question']}\n   答：{sample['answer']}\n   引用：{cites}"
        )
        if verdict is None:
            lines.append(f"{head}\n   判定：未评上（重试尽，不计入分母）")
        else:
            supported, reason = verdict
            lines.append(
                f"{head}\n   判定：{'supported' if supported else 'False'}"
                f"——{reason_first_sentence(reason)}"
            )
    lines.append("")
    rate = summary["supported_rate"]
    rate_text = "-" if rate is None else f"{rate * 100:.1f}%"
    lines.append(
        f"汇总：评上 {summary['judged']}/{summary['samples']}"
        f"（未评上 {summary['failed']}），supported {summary['supported']} 条，"
        f"supported 率 {rate_text}"
    )
    lines.append("")
    lines.append("口径声明（87 刀同款）：")
    lines.append("- 生成与判卷均走当日同一网关（LLM_BASE_URL）；跨网关未测，引用数字应带跑全日期。")
    lines.append("- LLM 输出非确定性：本报告是一次性观察，不进 CI、不进可复现评测尺。")
    lines.append(
        "- 证据面=citations 指向 (asset_id, version_no) 的全部切块（比模型 prompt "
        "实际可见的 top-2 证据宽——判「答案 vs 引用源」的忠实度）；"
        "工具步/多轮语境带入的证据外陈述会被严格协议判 false，逐条原因留档。"
    )
    lines.append(
        "- 存量样本按模板前缀指纹剔除降级回落（fallback 布尔不落消息表）；"
        "87 刀模板路径 10.0% 是「框架语越界」基线，本列不可与之直接当幻觉率比。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 报告骨架（--report 可选路）


def report_markdown(
    table: str, *, db_url: str, golden: Path, total: int, judge_note: str
) -> str:
    return (
        "# RAG 评测尺报告（脚本骨架，数字由 run_eval.py 生成）\n"
        "\n"
        f"- 日期：{date.today().isoformat()}\n"
        f"- 演示库：`{db_url}`\n"
        f"- golden：`{golden}`（{total} 条，种子 42 生成）\n"
        f"- LLM judge：{judge_note}\n"
        "\n"
        "```\n"
        f"{table}\n"
        "```\n"
    )


# ---------------------------------------------------------------- 生成路径观察主流程（--judge-llm）


def load_stock_samples(db: Any) -> list[dict[str, Any]]:
    """存量：service_messages 全量（时序）-> build_generated_samples。"""
    from sqlalchemy import select

    from suite_api.models import ServiceMessage

    messages = [
        {
            "id": m.id,
            "session_id": m.session_id,
            "role": m.role,
            "content": m.content,
            "kind": m.kind,
            "citations": m.citations,
        }
        for m in db.scalars(select(ServiceMessage).order_by(ServiceMessage.id))
    ]
    return build_generated_samples(messages)


async def ask_fresh_samples(db: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """现场真问：FRESH_QUESTIONS 逐条走线上 run_ask（真检索+真生成+真闸）。

    单会话顺序问（真实使用形态；问句均无代词，多轮记忆不进检索词）。answer 且
    带引用且非模板回落形状的入评（与存量同判据）；refusal/handoff/工具面只进
    计数——87 刀口径只评 answered。
    """
    from collections import Counter

    from suite_api.models import ServiceSession
    from suite_api.services.chat_engine import run_ask

    session = ServiceSession(status="active")
    db.add(session)
    db.commit()
    db.refresh(session)

    stats: Counter[str] = Counter(asked=len(FRESH_QUESTIONS))
    samples: list[dict[str, Any]] = []
    for question in FRESH_QUESTIONS:
        outcome = await run_ask(db, session, question, expose_gap_id=False)
        message = outcome.agent_message
        stats[message.kind or "none"] += 1
        if outcome.generated:
            stats["generated"] += 1
        elif message.kind == "answer":
            stats["fallback"] += 1
        if (
            message.kind == "answer"
            and message.citations
            and not is_template_answer(message.content)
        ):
            samples.append(
                {
                    "source": "新问",
                    "message_id": message.id,
                    "session_id": session.id,
                    "question": question,
                    "answer": message.content,
                    "citations": message.citations,
                }
            )
    return samples, dict(stats)


def evidence_chunks(db: Any, citations: Sequence[dict[str, Any]]) -> list[str]:
    """样本证据面：citations 指向 (asset_id, version_no) 的全部切块（按 seq）。"""
    from sqlalchemy import select

    from suite_api.models import RetrievalChunk

    chunks: list[str] = []
    for cite in citations:
        chunks.extend(
            db.scalars(
                select(RetrievalChunk.chunk)
                .where(
                    RetrievalChunk.asset_id == cite["asset_id"],
                    RetrievalChunk.version_no == cite["version_no"],
                )
                .order_by(RetrievalChunk.seq)
            ).all()
        )
    return chunks


def run_judge_llm(args: argparse.Namespace) -> int:
    """--judge-llm 主流程：存量样本 + 现场真问 -> 逐条 judge -> 报告。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url
    from suite_api.settings import get_settings

    if not get_settings().llm_api_key:
        print("--judge-llm 需要真 LLM key（.env LLM_API_KEY）：现场生成与判卷都走网关")
        return 2

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        stock = load_stock_samples(db)
        fresh, fresh_stats = asyncio.run(ask_fresh_samples(db))
        samples = [*stock, *fresh]
        for sample in samples:
            sample["evidence"] = evidence_chunks(db, sample["citations"])
        # 引用版本切块已不存在的样本（发布沿革极端形态）不评——空证据会全判 false
        dropped = [s for s in samples if not s["evidence"]]
        samples = [s for s in samples if s["evidence"]]
        if dropped:
            print(f"跳过 {len(dropped)} 条引用切块已不存在的样本（msg {[s['message_id'] for s in dropped]}）")

    verdicts = [
        judge_llm_with_retry(sample["question"], sample["evidence"], sample["answer"])
        for sample in samples
    ]
    report = judge_llm_report(samples, verdicts, fresh_stats=fresh_stats, db_url=args.db)
    print(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            f"# 第 102 刀：生成路径忠实度观察（--judge-llm，{date.today().isoformat()}）\n\n"
            + report
            + "\n",
            encoding="utf-8",
        )
        print(f"\n报告工件 -> {args.report}")
    return 0


# ---------------------------------------------------------------- 主流程


def _stale_expectations(db: Any, cases: list[dict[str, Any]]) -> list[str]:
    """cite 期望里「版本号 ≠ 该资产当前已发布版本」的条目（第 60 刀教训的守卫）。

    返回人话列表（空 = 全部新鲜）；调用方据此非零退出，避免把「期望过期」误读成
    「检索退化」。
    """
    from suite_api.models import Asset, AssetVersion

    stale: list[str] = []
    for case in cases:
        cite = case.get("expect", {}).get("cite")
        if not cite:
            continue
        asset = db.get(Asset, cite["asset_id"])
        if asset is None or asset.current_published_version_id is None:
            stale.append(f"{case['id']}: 资产 {cite['asset_id']} 没有当前已发布版本")
            continue
        current = db.get(AssetVersion, asset.current_published_version_id)
        if current is not None and current.version_no != cite["version_no"]:
            stale.append(
                f"{case['id']}: 期望 {cite['asset_id']}/v{cite['version_no']}，"
                f"当前已发布 v{current.version_no}"
            )
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测尺 runner（分层指标，可复现）")
    parser.add_argument("--db", default=DEFAULT_DB, help="演示库 Postgres URL")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="golden JSON 路径")
    parser.add_argument("--judge", action="store_true", help="启用 LLM faithfulness 评审列（模板路径，87 刀）")
    parser.add_argument(
        "--judge-llm",
        action="store_true",
        help="生成路径忠实度观察（第 102 刀）：存量生成消息+现场真问，一次性报告不进 CI",
    )
    parser.add_argument("--report", type=Path, default=None, help="另写 md 骨架到该路径")
    args = parser.parse_args(argv)
    if args.judge and args.judge_llm:
        parser.error("--judge（模板路径）与 --judge-llm（生成路径观察）互斥")

    if args.judge_llm:
        return run_judge_llm(args)

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Asset
    from suite_api.services.answer import compose_answer

    cases = json.loads(args.golden.read_text(encoding="utf-8"))

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        # 「过期期望」守卫（审计刀 12）：cite 期望的版本号必须等于该资产**当前已发布
        # 版本**——发布类治理动作（换正文/开修订）会让期望悄悄过期，runner 只按
        # (asset_id, version_no) 判定就会把「期望过期」记成「检索未命中」，把基线
        # 漂移伪装成检索回归。不一致即非零退出并点名用例。
        stale = _stale_expectations(db, cases)
        if stale:
            print("golden 存在过期版本期望（先更新 golden，再谈基线）:")
            for item in stale:
                print(f"  {item}")
            return 2
        meta = {
            asset.id: {"kind": asset.kind, "title": asset.title}
            for asset in db.scalars(
                select(Asset).where(Asset.current_published_version_id.isnot(None))
            )
        }
        rows: list[dict[str, Any]] = []
        judge_on = False
        judge_error: str | None = None
        judge_failed_ids: list[str] = []
        for case in cases:
            hits = retrieve(db, case["question"], top_k=TOP_K)
            composed = compose_answer(hits, meta)
            row = judge_case(case, hits, composed.kind)
            if args.judge and judge_error is None and row["answered"]:
                from suite_api.settings import get_settings

                if not get_settings().llm_api_key:
                    judge_error = "空 LLM key，judge 列跳过"
                else:
                    faithful = judge_with_retry(
                        case["question"],
                        [hit["chunk"] for hit in hits],
                        composed.content,
                    )
                    if faithful is None:
                        judge_failed_ids.append(case["id"])
                    else:
                        row["faithful"] = faithful
            rows.append(row)
            judge_on = judge_on or row["faithful"] is not None

    agg = aggregate(rows)
    table = format_table(agg, judge_on=judge_on)
    print(f"golden：{args.golden}（{len(cases)} 条）  检索 top-{TOP_K}")
    print(table)
    note = "未启用（未传 --judge）"
    if args.judge:
        if judge_error:
            note = judge_error
        else:
            note = (
                f"已启用：answered 条目 {agg['overall']['judged']}/"
                f"{agg['overall']['answered']} 条评上"
            )
            if judge_failed_ids:
                note += (
                    f"，{len(judge_failed_ids)} 条重试后仍失败（{','.join(judge_failed_ids)}）"
                    "——忠实度分母只含评上条目"
                )
    if args.judge and judge_error:
        print(f"judge：{judge_error}")
    elif args.judge and judge_failed_ids:
        print(f"judge：{len(judge_failed_ids)} 条重试后仍失败：{','.join(judge_failed_ids)}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            report_markdown(
                table,
                db_url=args.db,
                golden=args.golden,
                total=len(cases),
                judge_note=note,
            ),
            encoding="utf-8",
        )
        print(f"报告骨架 -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
