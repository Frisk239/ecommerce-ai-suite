# 工程第 12 刀 closeout：回流增强（feat/reflow-qa）

日期：2026-09-08。上刀 intake：`docs/progress/concurrency-hardening-intake.md`（通过）。短对齐：`.scratch/reflow-qa/spec.md`；领域裁决：ADR 0035。基线=`origin/main`（21fcb52，PR #15 合并后）。

## 交付（会话回流后 LLM 抽 QA 草稿→人洗→发布）

1. **`llm.complete_chat`**：聚合既有流式 `stream_chat`（同端点/错误契约/网关 session 头），登记链路复用厂商客户端，不另开非流式调用。
2. **机洗按种类分派**：kind=dialogue → 字段集 `["qa_pairs"]`，LLM 从转写抽 `[{q,a}]`；`run_machine_wash` 按 kind 显式分派，`machine_wash_field_names` 对非 dialogue 滤掉撞名键（评审修的 bug：document 撞名会在 async 路由误触 `asyncio.run`）。
3. **LLM 失败分级（ADR 0035）**：空 key=弃权降级推进待人洗（第 3 刀行为保留，既有回流测试零改动）；已配置但失败/超时/坏输出=停已接入+`last_error`，走既有就地重试端点（重试也重跑 LLM）；合法 `[]`=弃权。
4. **PATCH fields 扩展**：dialogue 合法集=`["qa_pairs"]`；数组值逐项 q/a 禁空串（共享校验 `machine_wash.validate_qa_pairs`，ValueError→422）；空数组=确认「没有 QA」；confirmed 写 `source:"human"`。
5. **发布切块**：confirmed qa_pairs 每对一块「问：{q}\n答：{a}」续排；未确认草稿不进索引；转写按轮切块不变；dialogue 无必填闸门不变。
6. **治理台 web**：`GET /api/assets/{id}/versions/{no}/text` 正文端点（复用 `read_version_text`）；详情页「对话转写 · v1」只读区 + 「QA 对」编辑器（改/删/增、机洗草稿/已确认标签、近黑主按钮）；`fields.ts` 支持数组值字段。README 一句：配置 LLM 后回流登记同步等待生成（≤20s）。

## Owner 验收（浏览器点穿，全过）

compose 全栈（db 5433/api/web，`--build`）。登录 operator → 客服页预览问「保温杯的净含量是多少？」→ 真模型「净含量为500ml。」+ 引用 `A-0003 · v1` → 结束并回流登记（LLM 抽取约 10s 内返回）→ 对话资产 `A-0009` → 详情页三块齐：转写（只读）、QA 对（机洗草稿 1 对，问/答与转写一致）、字段闸门提示不变 → 人洗改答案为「净含量为 500ml。」并确认（标签转「已确认」）→ 发布 v1（出现「开修订」「当前已发布」）→ 新会话再问同问法 → 回答含 QA 块「问：保温杯的净含量是多少？ 答：净含量为 500ml。」+ 引用 `A-0009 · v1`（该条恰走模板回退徽章——厂商瞬时不可用的第 7 刀降级路径，引用仍服务端定准）。视觉抽查（截图 AI 视觉核验）：三区块排版协调、主按钮近黑、徽章分层清晰，token 锚定一致。

## 两轴评审与处置（`docs/progress/reflow-qa-review.md`）

- Standards 零硬违规、Spec 零缺失零越界。
- **实修真 bug**：LLM 分派按字段名不按 kind（spec_schema 撞名 `qa_pairs` → document 在 async 路由 `asyncio.run` 崩）——改按 kind 显式分派 + 字段集滤撞名 + 2 个撞名单测。
- 顺手收敛：共享 `validate_qa_pairs`、`confirm_fields` 复用 `machine_wash_field_names`、docstring 补 MCP/事件循环前提。
- 记债务：requestText 抽公共核心、`_version_or_404` 提取、测试替身共用 helper。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=237 passed=178 failed/errored=0 skipped=59`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=237 passed=237 failed/errored=0 skipped=0`（第 11 刀基线 206 → 237 只增）
- web：`npm run build` 过，lint 3 warning 全为基线既有

## 遗留

- 评审债务三条（见上）；PII 打码（词条「机洗含打码」代码中尚不存在，全仓现状即无）不进本刀。
- 聚组/聚类（faq-extract 五步只取了「抽 QA」一步）、多会话批量抽留后续刀。
- 回流登记请求在真 key 下同步等 LLM（≤20s）；顾客回流体验不受影响（回流是操作者动作）。

## 下一刀

刀计数：审计刀 2 在第 10 刀后；本刀第 12。**审计刀 3 触发线=第 15 刀后**（约再 3 刀）。下一刀候选（关刀后按债务/演示缺口重排，不提前锁）：素材/切片（有接待飞轮和 MCP 证据，调研表已列为「再进队」）、订单工具刀（调研候选 3）、评测集（golden conversations 进 CI）。CONTEXT 推进句已回写。
