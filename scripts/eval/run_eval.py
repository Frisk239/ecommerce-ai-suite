"""评测尺 runner（第 35 刀）：golden 大集逐条直调 retrieve+compose_answer -> 分层指标。

不 mock 引擎：与线上完全相同的检索打分（services/retrieval）与降级模板组装
（services/answer），直接吃 --db 会话；检索层指标零 LLM 依赖（空 key 可复现）。
可选 --judge 对「有命中且未拒答」的条目调 complete_chat 评 faithfulness
（回答是否只由命中块支持）——LLMNotConfigured/LLMError 跳过该列并在报告注明。

指标（docs/research/rag-accuracy-engineering.md §3 协议）：
- recall@1/@3：cite 组（positive/paraphrase/confusion）期望资产进 top-1/top-3；
- 拒答率：refusal 组实际拒答比例（kind=refusal）；
- 误拒率：positive 组被拒答的比例（宁缺勿滥的反面代价，单独成列）；
- 混淆@1：confusion 组 top-1 恰为期望资产的比例（跨商品共有词是否被词法分
  拉向「证据更短更实」的资产）；
- 忠实度（--judge）：answered 条目中被 judge 判 supported 的比例。

统计全为纯函数（judge_case/aggregate/_supported_verdict/format_table），
离线单测见 apps/api/tests/test_rag_eval_tools.py。

用法（仓库根目录）：
    uv run python scripts/eval/run_eval.py --db postgresql://suite:suite@localhost:5433/suite
    uv run python scripts/eval/run_eval.py --db ... --judge --report scripts/eval/out/report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from suite_api.services.retrieval import retrieve

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"
TOP_K = 3
DISTRIBUTIONS = ("positive", "paraphrase", "confusion", "refusal")

JUDGE_SYSTEM_PROMPT = (
    "你是严格的检索增强问答评审。给定问题、检索证据与回答，逐句评答案忠实度"
    "（faithfulness）：回答的每条陈述都必须在证据里有原文依据（数值、实体、"
    "措辞一致或直接可导出）。注意：回答是否切题、是否完整不在评审范围——只评"
    "「有没有编证据外的东西」；不同句子可以来自不同证据条（各自有依据即可），"
    "多条证据之间内容不同不构成违规。任一陈述在证据里找不到原文依据 => false。"
    '只输出 JSON：{"supported": true} 或 {"supported": false}。'
)


# ---------------------------------------------------------------- 单条判定（纯函数）


def judge_case(case: dict[str, Any], hits: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """单条判定：hits=retrieve(top_k) 结果、kind=compose_answer().kind -> 行记录。

    recall@1 要求资产与版本都中；recall@3 资产命中即可（引到同资产更早版本
    也说明检索找到了证据源，版本漂移是发布语义不是检索错误）。
    """
    row: dict[str, Any] = {
        "id": case["id"],
        "distribution": case["distribution"],
        "refused": kind == "refusal",
        "answered": kind == "answer",
        "hit_asset_ids": [hit["asset_id"] for hit in hits],
        "recall1": None,
        "recall3": None,
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
        if case["distribution"] == "confusion":
            row["confusion_top1"] = bool(hits) and hits[0]["asset_id"] == want["asset_id"]
    return row


def _mean(values: Sequence[bool]) -> float | None:
    return round(sum(1 for v in values if v) / len(values), 4) if values else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按分布聚合 + overall（纯函数）：None 表示该分布不适用该指标。"""
    agg: dict[str, dict[str, Any]] = {}
    for dist in (*DISTRIBUTIONS, "overall"):
        group = rows if dist == "overall" else [r for r in rows if r["distribution"] == dist]
        agg[dist] = {
            "n": len(group),
            "recall1": _mean([r["recall1"] for r in group if r["recall1"] is not None]),
            "recall3": _mean([r["recall3"] for r in group if r["recall3"] is not None]),
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
        f"{'拒答率':>8}{'误拒率':>8}{'混淆@1':>8}"
    )
    if judge_on:
        header += f"{'忠实度':>8}{'评/答':>7}"
    lines = [header]
    for dist in (*DISTRIBUTIONS, "overall"):
        m = agg[dist]

        def pct(value: float | None) -> str:
            return "-" if value is None else f"{value * 100:.1f}%"

        line = (
            f"{dist:<12}{m['n']:>5}{pct(m['recall1']):>10}{pct(m['recall3']):>10}"
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


def judge_one(question: str, chunks: Sequence[str], answer: str) -> bool:
    """单条 faithfulness 评审（走线上同款 complete_chat；LLMError 上抛给主流程）。"""
    from suite_api.services.llm import LLMError, complete_chat

    async def _run() -> str:
        return await complete_chat(
            JUDGE_SYSTEM_PROMPT, build_judge_prompt(question, chunks, answer)
        )

    raw = asyncio.run(_run())
    verdict = _supported_verdict(raw)
    if verdict is None:
        raise LLMError("judge 输出不可解析") from None
    return verdict


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


# ---------------------------------------------------------------- 主流程


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测尺 runner（分层指标，可复现）")
    parser.add_argument("--db", default=DEFAULT_DB, help="演示库 Postgres URL")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="golden JSON 路径")
    parser.add_argument("--judge", action="store_true", help="启用 LLM faithfulness 评审列")
    parser.add_argument("--report", type=Path, default=None, help="另写 md 骨架到该路径")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import Asset
    from suite_api.services.answer import compose_answer

    cases = json.loads(args.golden.read_text(encoding="utf-8"))

    engine = create_engine(to_sqlalchemy_url(args.db))
    with sessionmaker(bind=engine)() as db:
        meta = {
            asset.id: {"kind": asset.kind, "title": asset.title}
            for asset in db.scalars(
                select(Asset).where(Asset.current_published_version_id.isnot(None))
            )
        }
        rows: list[dict[str, Any]] = []
        judge_on = False
        judge_error: str | None = None
        for case in cases:
            hits = retrieve(db, case["question"], top_k=TOP_K)
            composed = compose_answer(hits, meta)
            row = judge_case(case, hits, composed.kind)
            if args.judge and judge_error is None and row["answered"]:
                from suite_api.services.llm import LLMError
                from suite_api.settings import get_settings

                if not get_settings().llm_api_key:
                    judge_error = "空 LLM key，judge 列跳过"
                else:
                    try:
                        row["faithful"] = judge_one(
                            case["question"],
                            [hit["chunk"] for hit in hits],
                            composed.content,
                        )
                    except LLMError as exc:
                        judge_error = f"LLMError：{type(exc).__name__}，judge 列停止"
            rows.append(row)
            judge_on = judge_on or row["faithful"] is not None

    agg = aggregate(rows)
    table = format_table(agg, judge_on=judge_on)
    print(f"golden：{args.golden}（{len(cases)} 条）  检索 top-{TOP_K}")
    print(table)
    note = "未启用（未传 --judge）"
    if args.judge:
        note = judge_error or f"已启用：answered 条目 {agg['overall']['judged']}/{agg['overall']['answered']} 条评上"
    if args.judge and judge_error:
        print(f"judge：{judge_error}")
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
