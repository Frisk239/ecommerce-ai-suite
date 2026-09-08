# Intake · 第 14 刀库存工具（feat/stock-tool）

- 日期：2026-09-08（Slice Owner 接手第 15 刀）
- Prev slug：`stock-tool`；实现提交 `f9b0b19`，文档 `align(14)`/`review(14)`/`closeout(14)` 三笔，合入 `033fbe2`（PR #18）
- Merge 状态：**已合入 default**。第 15 刀从 `origin/main` 起 `feat/eval-set`。
- 特殊性：同会话 Owner 一手验收（门禁/浏览器六项/两轴评审/合并），下表为当时原始记录。

## Evidence（Owner 一手，junitxml 机械计数）

| 项 | Closeout 声明 | Intake 复核（同会话原始输出） |
|---|---|---|
| 无 DB | `tests=301 passed=225 failed/errored=0 skipped=76` | 同 |
| 带库全量（5433） | `tests=301 passed=301 failed/errored=0 skipped=0` | 同（基线 259 → 301 只增） |
| ruff | All checks passed | 同 |
| Owner 浏览器六项 | 有货/无货/未命中/规格零漂移/订单零漂移/分派序 | 亲历：`get_stock(钛钢保温杯) → 有货 · 42 件`；「暂时无货」；「没有找到对应商品，已转人工」无拒答徽章无缺口芯片；规格问题引用 `A-0009 · v1`；SO-1001 订单工具照常 |

## Spec vs claim（抽查 3 项）

1. **五分支归属** — PASS。>0/==0 是 answer（0 是事实）；NULL/未命中/error 是 handoff 不检索不缺口（三哨兵+缺口计数断言）。
2. **分派序订单优先** — PASS。双层钉测试（单测打桩 + 集成「SO-1001 里的保温杯」走订单）。
3. **stock 不升格** — PASS。ProductOut 不泄漏、无写端点、检索/治理/MCP 零触点（评审全扫）。

## Safety

无秘密入库；stock 仅引擎工具读。

## 债务（轻，进审计刀 3 候看清单）

工具共享缝（_run_order_ask/_run_stock_ask 重复 45 行，第三工具时收）、thinking 文案三元链、裸 dict 协议、「剩」字词表误伤面、第 12/13 刀遗留债务（requestText 公共核心、_version_or_404、测试替身 helper）。

## Verdict

**通过** — 可进 step 3 短对齐第 15 刀（评测集：golden conversations + policy edges 进 CI，ADR 0027 v1=可复现记录，不建表不做台）。刀计数：第 15 刀；**本刀后触发审计刀 3**（计数线到）。
