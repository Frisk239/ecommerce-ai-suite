# 工程第 32 刀两轴评审：多来源数据 II 收口（feat/real-data-2）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`afcd3e2`）。Spec：`.scratch/real-data-2/spec.md` + `docs/roadmap.md` §4 第 32 行。

**净，可合。**

## Standards

- `git diff origin/main -- apps/api/src apps/web packages` 空：零产品代码。
- 脚本纯标准库；`source_kind=session_backflow` 走既有 `register_asset`；WANDS 灌 `clip_candidates`（0014 候选不是资产）。
- 离线 fixture 不吃外网。无密钥入库。

## Spec

- Must 齐：ABCD 转写 `顾客：`/`客服：`；WANDS Exact join；README 许可 MIT+链接；+11 离线测。
- Out 守住：无数据 III、无 schema 填、无新 ADR。
- 债务（不阻断）：承载商品「WANDS 家具」`spec_schema={}` → 第 33 刀。
