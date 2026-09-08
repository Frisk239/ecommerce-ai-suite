# 工程第 13 刀 closeout：订单工具（feat/order-tools）

日期：2026-09-08。上刀 intake：`docs/progress/reflow-qa-intake.md`（通过）。短对齐：`.scratch/order-tools/spec.md`；领域裁决：ADR 0036。基线=`origin/main`（9cb4676，PR #16 合并后）。

## 交付（只读 get_order_status + 转人工触发器落地）

1. **迁移 0007**：`orders` 表（order_no 唯一/status/items JSONB/events JSONB/placed_at；无外键，不升格为非中台对象）+ `service_messages.tool` JSONB（工具条随消息落库、回放还原）。
2. **`services/order_tools.py`**：`SO-\d+` 订单号正则（知识型凭证，分派在代码不在 prompt）；`get_order_status` 三分支（found/not_found/error，DB 异常吞掉并 best-effort rollback 保会话）；摘要（「已发货 · 2 个物流事件」）与确定性中文模板（状态+商品行+物流轨迹）。
3. **引擎级插入**（ADR 0018/0036）：run_ask 检索前分派——命中订单号跳过检索；成功→kind="answer" 模板组装、citations 恒空、**不调 LLM**；查无/故障→**kind="handoff"**（新值，不复用 refusal）+handoff=True+交接摘要，**不检索、不产生缺口**（0024 契约落地）；非订单问题 commit 序列与事件序零漂移（既有契约测试零改动绿）。
4. **SSE**：新 `tool` 事件 `{name,arg,result}`；thinking「查询订单中…」；complete 加 `tool` 字段（两通道同形状，不走 gap_id 式裁剪——单号本由提问者提供）。
5. **web**：endpoints 双通道 tool 分支；MessageBubble 工具条（灰底 mono `get_order_status(SO-1001) → 已发货 · 2 个物流事件`，UX-NOTES §6 冻结口径，与青底引用芯片视觉分离）；handoff 气泡复用「已转人工」徽章、不出拒答徽章；客服/顾客页建议问题加订单问法；README 一段。
6. **种子**：幂等三单（SO-1001 已发货带 2 物流事件/SO-1002 运输中/SO-1003 已签收）。

## Owner 验收（浏览器点穿，六项全过）

compose 重建（迁移 0007 自动跑）。操作者：问「我的订单 SO-1001 到哪了？」→ thinking「查询订单中…」→ 工具条+模板回答（状态/商品/轨迹）、无引用芯片；问「订单 SO-9999 到哪了？」→ 「已转人工」徽章（无拒答徽章）+「订单 SO-9999 未找到，已转人工，请人工核实单号。」+工具条 `→ 未找到`；页面无知识缺口芯片；刷新后工具条还原（tool 落库回读）；普通知识问题「保温杯的净含量是多少？」仍引用 `A-0009 · v1`（检索路径零漂移）。顾客页：SO-1001 工具条+回答+无缺口芯片；SO-9999 转人工徽章+摘要+工具条+无拒答徽章（过程注：playwright 对顾客页 textarea 受控组件注入有时序怪癖，点「新会话」后 fill/Enter 正常——非产品缺陷，操作者页同款组件全程正常）。

## 两轴评审与处置（`docs/progress/order-tools-review.md`）

- Standards 零硬违规、Spec 六条全兑现零越界（评审子代理本机真 PG 复跑）。
- 实修两处过期注释：answer.py kind 注释补 handoff；knowledge_gaps docstring 更新「工具失败不缺口」现状。
- 记债务：后端 ToolCallRecord 类型、三态判别收敛、kind 注释集中、命名后缀、前端双重断言。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=259 passed=193 failed/errored=0 skipped=66`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=259 passed=259 failed/errored=0 skipped=0`（第 12 刀基线 237 → 259 只增）
- web：`npm run build` 通过

## 遗留

- 评审债务五条（轻）；库存工具（词条同段承诺「库存/订单工具」，同模式复用）为下一刀强候选；LLM 组织工具语言、email+zip 凭证、两阶段写操作（check_eligibility/create_return）、坐席队列均 Out 留后续。
- 无单号的订单问题走检索→拒答转人工（无凭证即转人工，语义正确）；拒答文案未针对订单语境特化（记备注）。

## 下一刀

刀计数：本刀第 13。**审计刀 3 触发线=第 15 刀后**（还差 2 刀）。下一刀强候选：库存工具（ADR 0036 已留同模式复用入口；词条「库存可 mock」仍未兑现）。CONTEXT 推进句已回写。
