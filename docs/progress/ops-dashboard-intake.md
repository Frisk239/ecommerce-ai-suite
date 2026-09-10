# 第 43 刀规格：操作者仪表 + 通知（intake + Owner 裁决）

日期：2026-09-10。分支 `feat/ops-dashboard`（基于 main `fbc2d49`）。依据：`docs/roadmap-product-hardening.md` 第 43 刀（最痛#3 操作者聋）。

## 痛点

「我的客服最近怎么样」现在回答不了：没有仪表、没有通知、没有趋势。操作者要自己去翻会话列表和缺口列表才知道发生了什么。

## Owner 裁决（实现按此）

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | **拒答与转人工分开报数**（`refusals` / `handoffs` 两个数），不合并成「没答上」 | 两者语义不同（无证据 vs 显式要人/工具失败），且第 42 刀刚把 handoff 抬成一等公民 |
| 2 | **引用覆盖率** = 有引用的 `answer` / 全部 `answer`（分母 `kind='answer'`），**同时下发分子分母**供 UI 标注口径 | 拒答按定义无引用，混进分母会把「无证据」讲两遍；模板/工具回答是 `kind='answer'` 且 `citations=[]`，故该指标口径必须写明是「回答带引用占比」，不是「RAG 准确率」 |
| 3 | 近 7 日 = **含今天在内的 7 个自然日**（`today-6 … today`），**零填充升序**，日期用 `YYYY-MM-DD` | 滚动 7×24h 会产生 8 个半截桶；零填充让前端不必处理空洞 |
| 4 | 分桶按 **UTC 天** | 库与应用全用 `DateTime(timezone=True)` + `datetime.now(UTC)`，DB 时区 `Etc/UTC`；无本地时区先例，UTC 是唯一确定性选择 |
| 5 | 角标为 **0 时隐藏**（不是显示 0） | 既有唯一先例（客服页行内「工单 N」）就是 0 隐藏 |
| 6 | 「被踩最多」→ **跳到详情页并定位/高亮「重新验证」**，**不自动执行写动作** | 深链触发写是坏模式（刷新/分享链接会写库）；「一键」= 一次点击直达动作所在处 |
| 7 | 角标刷新 = `useApiData(fetcher, pathname)`——**每次导航刷新一次，无定时器** | 满足「不做轮询」；shell 只在导航时重渲染，天然跟着走 |
| 8 | 反馈汇总取 **top 3** | 它是行摘要，不是磁贴网格 |

## 形态约束（硬）

- 第 43 刀必须在 **UX-E 去卡之后**落地：总览页**不得再出现等宽磁贴网格**。趋势与反馈都是 `panel` 里的**行/条**形态（与既有摘要条同一语言）。
- **不引图表库**（package.json 只有 react/router/phosphor/tailwind）；迷你趋势手写 inline SVG 或 CSS 柱。
- 视觉照 `prototype/UX-NOTES.md` §二点七：实色、墨线分层、hover 同色相叠加不上浮、品牌蓝只做语义。
- **无新表、无迁移**（全部由现有表聚合）。

## 端点（一次请求喂三处）

`GET /api/stats/overview`（新文件 `routes/stats.py`，操作者 cookie 鉴权，内联响应模型）：

- `daily[]`：7 天，每天 `{date, sessions, refusals, thumbs_down}`
- 7 日标量：`sessions / refusals / handoffs / thumbs_down / gaps_opened / gaps_resolved / answers / answers_with_citations / citation_rate`
- `badges`：`open_gaps`（`knowledge_gaps.status='open'`）/ `pending_qc`（`material_tasks.status='pending_qc'`）/ `open_tickets`（`handoff_tickets.status='pending'`）
- `feedback_assets[]`：被踩最多的资产 top 3（`{asset_id, title, count}`），由 `service_messages.feedback @> '{"helpful": false}'` 展开其 `citations` 的 `asset_id` 聚合得到——**复用 `routes/customer.py` 的 `triage_asset_ids` 防御式解析**，不要重造；JSONB 键可能缺失，禁止直接下标取值

## 前端

- `types.ts` / `endpoints.ts` 加类型与 `getStatsOverview`。
- `AppShell`：`useApiData(() => api.getStatsOverview(), pathname)`；NAV 项加可选 `badgeKey`；角标渲染在 label 之后；`.nav-badge` 新样式（11px tabular-nums，`bg-fill`/`text-ink-2`，无渐变/无位移）。
- `OverviewPage`：摘要条之后、能力闭环之前插**一条 7 日趋势条**（`panel`，内含每日柱 + 图例：会话/拒答/被踩）+ **一条反馈汇总行**（top 3 资产，每条带「去重新验证」链接 → `/platform/assets/{id}?verify=1`）。
- `AssetDetailPage`：支持 `?verify=1` —— 把「重新验证」块滚入视口并短暂高亮（**不自动点**）。

## 测试

- 新 `apps/api/tests/test_stats_integration.py`：形状 + 计数正确性（造数据后逐项对）+ 零填充 7 天 + 无反馈时 `citation_rate` 为 `None` 而非除零 + 鉴权 401。
- 前端 build/lint（lint 必须仍 7/0）。

## Out

- 不引图表库、不做实时推送/轮询、不做通知外发（邮件/短信）、不做自定义时间窗（固定 7 日）、不加索引（演示规模 195 行 service_messages；生产规模的 `created_at` 索引记为债）。
