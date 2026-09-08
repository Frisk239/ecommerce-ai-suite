# 工程第 12 刀两轴评审：回流增强（feat/reflow-qa）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `202923e`+`968aecc`，处置 `accc643`）。Spec：`.scratch/reflow-qa/spec.md` + ADR 0035。

## Standards

**零硬违规。** 分层/错误契约合规（services 抛 MachineWashError/LLMError，路由转 HTTPException）；`read_version_text` 与 MCP 共用取数；测试布局贴既有模式；CONTEXT 词条（三态/弃权/confirmed 才进索引/禁空串冒充）实现与文案一致；「机洗草稿」的「草稿」指 QA 值不是资产状态，不踩「草稿」_Avoid_（词条原句「等人改或补」兼容）。UI 用词（操作者/顾客/转写）无误。

**真 bug（已实修，accc643）**：LLM QA 分派原按 `QA_FIELD in names`——商品 spec_schema 撞名 `qa_pairs` 时 document 登记也走 `extract_qa_draft`，而文档登记是 async 路由，`asyncio.run` 在事件循环上直接 RuntimeError。修法：`run_machine_wash` 新增 `kind` 参数按种类显式分派（`kind=="dialogue" and QA_FIELD in names`）；`machine_wash_field_names` 对非 dialogue 滤掉 `qa_pairs`（第二道防御，人洗 allowed 同口径）。钉 2 个撞名单测。

实修（judgement 收敛）：`extract_qa_draft` docstring 补 MCP/事件循环前提；`qa_pairs` 形状校验收敛 `machine_wash.validate_qa_pairs`（解析/路由两侧复用，ValueError→422 包装）；`confirm_fields` 的 allowed 复用 `machine_wash_field_names`（消 Repeated Switches）。

记债务（不阻断）：`client.ts` requestText 与 request 错误处理可抽公共核心；`get_version_text`/`confirm_fields` 版本查询可提取 `_version_or_404`；`_patch_complete_chat` 与 vendor `_patch_stream` 测试替身可共用 helper。

## Spec

Must 1–6 全在 diff，无实质缺失；Out 零越界（无聚组/打码/必填闸门改动/独立资产/修订改动/MCP/顾客面/连接池）。三档 LLM 分级（空 key 弃权降级 / 坏输出停已接入可重试 / 合法空数组弃权）均有钉测试；三条既有行为（未确认草稿不进索引、转写按轮切块不变、dialogue 无必填闸门不变）无漂移且有测试。

轻度越位（可辩护，采信）：PATCH 文档字段非字符串值原为 500，现显式 422；未知字段文案改「不在该资产的合法字段集合内」（dialogue 无「所挂商品」可言）；空数组=确认「没有 QA」是 ADR「空数组可发布」的合理外延；q/a strip 后落库无害。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=237 passed=178 failed/errored=0 skipped=59`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=237 passed=237 failed/errored=0 skipped=0`
