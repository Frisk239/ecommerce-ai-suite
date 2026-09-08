# 工程第 13 刀两轴评审：订单工具（feat/order-tools）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `4c8fdc0`，处置注释提交在后）。Spec：`.scratch/order-tools/spec.md` + ADR 0036。

## Standards

**零硬违规。** 核查通过：orders 守住「不升格」（无路由/MCP/检索/治理触点，models.py docstring 钉死工具数据源语义）；handoff/refusal 分叉干净（record_refusal_gap 只挂 refusal 分支，集成测试钉死）；SSE tool 事件两通道同形状不裁剪（注释给理由）；非订单零接触（分派=纯前置正则，双测试钉）；迁移 0007/seed 幂等贴 0006 风格；「转人工」词条口径全兑现（消息种类、不拿检索顶、不产生缺口、无坐席队列）。

两处轻违规（过期注释，已实修）：
- `answer.py:32` ComposedAnswer.kind 注释漏 `handoff` 值 → 已补（含 ADR 0036 出处）。
- `knowledge_gaps.py:4` docstring 仍写「本刀无工具」→ 已更新为「第 13 刀起路径真实存在，kind="handoff" 不走本服务」。

记债务（judgement，不阻断）：`{name,arg,result}` 后端无 TypedDict（web 已有 ToolCallRecord）；三态判别（found/not_found/error）在摘要/文案/分派三处各写一遍可收敛；kind 联合注释散布 6 处（本次即漏一处）宜集中；`_NOT_FOUND_CONTENT_TPL`/`_ERROR_CONTENT` 命名后缀不一致；前端 `as unknown as ToolCallRecord` 双重断言只验 name。

## Spec

**六条全兑现，Out 零渗漏，建议通过。** 逐条：零漂移（`test_run_ask_commits_before_llm_stream` 不在 diff 且绿；分派唯一副作用=正则；HTTP 层另有复核测试）；查无/故障不检索不缺口（三哨兵单测 + 集成 gap 计数不变断言）；citations 恒空/不调 LLM/种子幂等均有显式断言（评审子代理本机真 PG 复跑三文件全绿）；kind="handoff" 不复用 refusal；handoff 分支不发「正在生成」thinking；工具条随消息落库与 gap_id 运行时口径区分正确；Out 全扫零命中（库存/坐席/email+zip/限流/写操作均未渗入）。轻微附加（已自认）：complete 多 `"tool": null` 键，既有消费方按键取值不受影响。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=259 passed=193 failed/errored=0 skipped=66`
- 带 DB 全量（处置前实现态 Owner 已复跑 `tests=259 passed=259 failed/errored=0 skipped=0`；处置仅两处注释，无行为改动）
