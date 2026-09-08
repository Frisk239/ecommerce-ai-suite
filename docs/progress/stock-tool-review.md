# 工程第 14 刀两轴评审：库存工具（feat/stock-tool）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `f9b0b19`）。Spec：`.scratch/stock-tool/spec.md` + ADR 0037。

## Standards

**零硬违规，可合。** stock 守住「商品 mock 字段」边界（ProductOut 不泄漏、无写端点、检索/治理/MCP 零触点）；词表与 ADR 0037 逐字一致；订单号优先分派序正确；LCS 滚动数组实现正确且 tie-break 确定性有测试；种子幂等「仅 NULL 回填不覆盖手改值」有断言；_Avoid_「用规格文档回答有没有货」闭环有钉测试。

记债务/候看（judgement，不阻断）：
- `_run_stock_ask` 与 `_run_order_ask` 形状重复约 45 行——两实例尚可，**第三工具出现时收 `_run_tool_ask` 共享缝**（审计刀 3 候看项）。
- `sse_event_stream` 按工具名字符串分叉 thinking 文案（三元链）；`{found/error/stock}` 裸 dict 协议第二实例；`summarize_tool_result` 通用名被 order 侧占用。
- 词表单字「剩」可能截走「还剩多少毫升」类准规格问句→handoff（ADR 已裁决误伤代价低；审计刀回看真实问句分布）。
- 负库存渲染「暂时无货」（无写端点不可达，不究）。

## Spec

**高度一致，零越界。** 分派序（订单优先）双层钉测试；五分支归属全对（==0 是 answer 事实、NULL/未命中/error 是 handoff 不检索不缺口）；非库存非订单零接触（commit 序列测试零改动）；Out 逐项核过无渗入；评审子代理真 PG 全量复跑绿。

字面级小瑕疵（采信）：`get_stock` 返回 `product_name` 而非 `product` 对象（语义等价）；规格零漂移测试动态发布资产断言而非直接钉 A-0003/A-0009（既有回归套件兜底）。

**处置**：无代码修改；ADR 0037 与 order-tools-intake 文档由 Owner 随 align(14) 提交（先例模式）。

## 计数（Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=301 passed=225 failed/errored=0 skipped=76`
- 带 DB（5433）：`tests=301 passed=301 failed/errored=0 skipped=0`（基线 259 → 301 只增）
