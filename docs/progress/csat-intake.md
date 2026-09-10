# 第 48 刀规格：CSAT + 反馈闭环补全（intake + Owner 裁决）

日期：2026-09-11。分支 `feat/csat-48`（基于 main `9942898`）。依据：`docs/roadmap-product-hardening.md` 第 48 刀（第二梯队末项）。迁移 **0024**。

## 痛点

反馈面只有**半边**，而且是哑的：

1. **没有「这次服务怎么样」的出口**：顾客只有一条「没有帮助」（thumbs-down），满意度无法表达为分数。
2. **thumbs-up 是 40 刀明文 Out**：`FeedbackBody` 只接受 `helpful=false`，传 true 直接 422——「这条有用」按不了，「没有帮助」却被记下来，**只收负反馈**。
3. **打回素材不记理由**：`reject_task` 只把 `last_error` 写成「人工打回」，运营看不到为什么被打回，生成方无从改。
4. **仪表没有满意度口径**：43 刀有被踩 top 3，但「顾客满不满意」只能靠数。

## 现状（实测）

- `service_messages.feedback` JSONB（消息级，`{"helpful": false, "at": ...}`）；负反馈触发 `triage_asset_ids` + 资产 `last_verified_at=None`（复审队列）。
- `service_sessions.status` ∈ active/registered；**顾客侧没有「结束会话」动作**——`closed_at` 只在操作者回流登记时落值。
- `material_tasks.last_error` 已存在且 UI 已展示失败原因。
- `GET /api/stats/overview` 已有 7 日窗 + 分列口径，CSAT 只是加一段。

## 裁决

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | 新表 `session_ratings`（迁移 **0024**）：`session_id` FK **唯一**、`score` 1–5、`comment` nullable、`created_at` | CSAT 是**会话级**口径（Chatwoot 形态），与消息级 thumbs 正交；独立成表才能按分聚合（仪表）。不做「多评/改评」（v1 唯一约束） |
| 2 | **不做自动弹出、不等「会话结束」**：顾客页页脚常驻一行 1–5 星（+ 可选留言），**评过即收**（「谢谢反馈」），不打断对话 | ①本仓顾客通道**没有**「顾客关闭会话」这个事件（`closed_at` 只在操作者回流时落），等它出现等于永远不出现；②自动弹窗伤转化且 roadmad 只要「一次性」——「评过就没了」正是一次性。**这是对 roadmap 字面的一处订正，回退点写在这里** |
| 3 | 评分端点 `POST /api/customer/sessions/{id}/rating`：Bearer + IP 限流 + 409 非 active + 422 分值越界 + **幂等 409 已评** | 与发问/反馈同闸序（第 45a 刀的单一鉴权出处）；幂等与 thumbs-down 同口径 |
| 4 | **留言出口必掩**（ADR 0038）：库内原文，出口过 `redact_contact` | 留言是顾客手打的自由文本，比 thumbs 更容易带手机号/邮箱；操作者面（仪表）展示的是掩码后的 |
| 5 | thumbs-up 放开：`helpful=true` 记 `{"helpful": true, "at": ...}`；**只有 false 走分诊**（撤销验证），true 不碰 `last_verified_at` | 正反馈的语义是「这条有用」，不是「证据要复审」；反向若也撤销验证，等于顾客点赞就进复审队列 |
| 6 | 打回理由：`POST /material/tasks/{id}/reject` 收可选 `reason`（≤200 字，空白=无）→ `last_error = "人工打回：{reason}"`，无理由保持「人工打回」 | 复用既有 `last_error` 列与既有 UI 展示口径，不新造列；`reason` 与不传等价 |
| 7 | 仪表加 `csat` 段（**7 日窗，与既有口径一致**）：`ratings_last_7d` / `average_last_7d`（1 位小数，**无样本 = None**）/ `distribution`（1–5 各计数）/ `recent_comments`（最近 3 条，**掩码 + 截断 60 字**） | 与 43 刀同一窗、同一「不除零、不谎报」纪律；分布让人看见「均分 4.3 其实是 5+5+3」 |
| 8 | 前端总览加一条 **panel 行**（非卡片、无图表库——UX-E 约束）；客服页会话行加星级徽章 | 43 刀立的形态约束不许破；评分在场才有用 |
| 9 | **低分不自动写库**：≤2 分不撤销验证、不建缺口、不建工单 | CSAT 是会话级主观分（可能因为物流慢），把它当「证据有问题」误伤；要复审走 thumbs-down（那才是证据语义） |
| 10 | 顾客页 `CustomerPage` 评分条只在**有过至少一条 agent 回答**后出现 | 空会话打 1 分是噪声 |
| 11 | 无新页面、无新依赖 | 本刀是补闭环，不是开面 |

## 验收

1. **评分**：顾客问一句 → 页脚出现评分条 → 点 4 星（+留言）→ 提交成功、变「谢谢反馈」；重复提交 409；非 active 会话 409；分 0/6 → 422；无令牌 401。
2. **落库**：`session_ratings` 一行、`score=4`、comment 原文在库（未掩）。
3. **出口掩码**：留言含手机号 → 仪表 `recent_comments` 里是掩码后的值（库内仍原文）。
4. **thumbs-up**：`helpful=true` → 200、`feedback.helpful=true`、**`triaged_asset_ids` 为空**且资产 `last_verified_at` 不变；同一消息再评 → 409。
5. **打回理由**：带 reason → 任务 `failed` 且 `last_error="人工打回：画面糊"`；不带 → `"人工打回"`；reason 超 200 字 → 422。
6. **仪表**：空库时 `csat.average_last_7d is None`、`ratings_last_7d=0`；评 2 条后均分/分布/计数正确；`recent_comments` 最多 3 条且掩码。
7. **门禁**：后端全量绿、ruff 净；前端 build 绿、lint 7/0。
8. **端到端（playwright）**：顾客真问一句 → 打分 → 操作者客服页看到星级 → 总览看到 CSAT 行与均分。

## Out

- NPS / 点评估（每消息打分）/ 自动弹窗 / 多评与改评 / 评分通知（邮件短信）/ 按坐席分（没有坐席概念）/ CSAT 与缺口的自动联动 / 评分趋势图 / 顾客侧「查看历史评分」。
