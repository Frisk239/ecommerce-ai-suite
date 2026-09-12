"""第 82 刀测量脚本：实体存在性闸（OOV）判据的验证矩阵。

用途：①对 golden 96 条验证判据**零误杀**（命中只允许落在 refusal 组——它们本来
就该拒答）；②对 OOV 病例表验证判据命中与对照不命中。判据本体在
`suite_api.services.retrieval.oov_verdict`（生产实现即被测对象，避免实验与生产分叉
——第 79 刀教训：复算必须与生产逐位一致）。

判据（三轮迭代定稿，见 docs/progress/oov-gate-82-closeout.md 测量节）：
问句剔观点标记与库内字段名词后取实体段 -> 存在 ≥4 字**连续零出现串**（全部 bigram
不在库内任何已发布文本）且实体段对全部资产标题的最高亲和（79 刀信号）为 0。

用法（仓库根，连演示库；注意：测试库语料 <100 条时 OOV_MIN_CORPUS_ROWS 护栏会让
判据一律不生效——本脚本要连演示库才有意义）：
    uv run python scripts/eval/oov_criterion.py --db postgresql://suite:suite@localhost:5433/suite
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from suite_api.db import to_sqlalchemy_url
from suite_api.services.retrieval import oov_verdict

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"

# (问句, 类别, 期望是否判 OOV)
CASES = [
    ("雀巢咖啡的配料是什么", "OOV-真例", True),
    ("乐事薯片的净含量是多少", "OOV-真例", True),
    ("星巴克杯子的价格是多少", "OOV-真例", True),
    ("泰国香米多少钱", "OOV-真例", True),
    ("戴森吸尘器的净含量是多少", "OOV-真例", True),
    ("华为手机的材质是什么", "OOV-漏判（串长 3 字，设计内保守）", False),
    ("M&M black的条码是多少", "近名不同实体（走既有拒答）", False),
    ("净含量是多少", "字段问（无实体）", False),
    ("品牌怎么样", "字段问（无实体）", False),
    ("配料是什么", "字段问（无实体）", False),
    ("保温杯的净含量是多少", "真匹配", False),
    ("Erdbeeren的净含量是多少", "真匹配", False),
    ("M&M white的条码是多少", "真匹配", False),
    ("请问退换政策说明值得入手吗", "同义改写", False),
    ("请问尺码对照说明值得入手吗", "同义改写", False),
    ("Erdbeeren 巧克力的条码是多少", "中间地带（亲和 0.75）", False),
    ("你们卖什么？", "目录问句", False),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OOV 判据验证矩阵")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    args = parser.parse_args(argv)

    engine = create_engine(to_sqlalchemy_url(args.db))
    failures = 0
    with sessionmaker(bind=engine)() as db:
        print("== 病例表（期望 vs 实际）==")
        for question, kind, expect in CASES:
            verdict = oov_verdict(db, question)
            ok = (verdict is not None) == expect
            failures += 0 if ok else 1
            mark = "✓" if ok else "✗"
            print(f" {mark} {question:<28}{kind:<28} -> {verdict}")

        print("\n== golden 96 条（命中只允许是 refusal 组）==")
        cases = json.loads(args.golden.read_text(encoding="utf-8"))
        flagged = []
        for case in cases:
            verdict = oov_verdict(db, case["question"])
            if verdict is not None:
                flagged.append((case["id"], case["distribution"], verdict))
        bad = [f for f in flagged if f[1] != "refusal"]
        failures += len(bad)
        print(f" 命中 {len(flagged)} 条（refusal 组 {len(flagged) - len(bad)} / 误杀 {len(bad)}）")
        for item in flagged:
            mark = "✓" if item[1] == "refusal" else "✗ 误杀"
            print(f" {mark} {item[0]} [{item[1]}] -> {item[2]}")
    print(f"\n{'全部通过' if failures == 0 else f'{failures} 处不符'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
