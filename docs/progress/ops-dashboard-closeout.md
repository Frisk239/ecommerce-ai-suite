# 第 43 刀 closeout：操作者仪表 + 通知（`feat/ops-dashboard`）

日期：2026-09-10。规格 `docs/progress/ops-dashboard-intake.md`（Owner 八条裁决 + 形态硬约束）；施工权威 `docs/roadmap-product-hardening.md` 第 43 刀（roadmap v3 第三刀，最痛#3 操作者聋）。**无新表、无迁移**。

## 交付

| 层 | 交付 |
| --- | --- |
| 端点 | 新文件 `routes/stats.py`：`GET /api/stats/overview`（操作者 cookie 鉴权）。一次请求喂三处：7 日逐日序列、7 日标量、三个角标、被踩资产 top 3 |
| 口径 | 拒答与转人工**分列**（`kind='refusal'` vs `kind='handoff'`，拒答消息自带的 `handoff=True` 不污染 handoffs）；缺口**新开按 `created_at`、解决按 `resolved_at`**（不是当前态过滤）；引用覆盖率分母 = `kind='answer'` 且**同时下发分子分母** |
| 窗口 | 含今天在内的 **7 个 UTC 自然日**、00:00 起、零填充升序、`YYYY-MM-DD` |
| 角标 | `open_gaps` / `pending_qc` / `open_tickets`（第 42 刀工单表提供了第三个数据源）；**0 时隐藏**；随导航刷新一次，无定时器 |
| 总览 | 摘要条之后插**一条 7 日趋势条**（21 根柱 = 7 天 × 会话/拒答/被踩，共用纵轴，含图例与 7 日合计）+ **一条反馈汇总行**（被踩最多 top 3，每条「去重新验证」） |
| 深链 | `/platform/assets/{id}?verify=1` → 滚入视口 + 1.6s 高亮，**不自动执行写动作**（深链触发写是坏模式：刷新/分享链接会写库） |

## 验收（实测）

- 端点数值与独立盘点的库内计数**逐项吻合**：sessions 69 / refusals 19 / handoffs 4 / thumbs 2 / answers 75 / with_citations 57 / rate 0.76 / gaps 新开 10 解决 2 / 角标 8-1-1；`daily` 恰好 7 条升序含今天；未登录 401。
- 浏览器：21 根趋势柱 + 图例 + 「引用覆盖率」标注（写明分母是回答、非 RAG 准确率）；角标 8/1/1 在首屏即在位、**导航后不闪**（保留上一帧值）；**`.stat-card` 计数为 0**（UX-E 去卡的形态约束未被违反）；反馈行标题「被踩最多的资产 · 全时段」。
- 同一路由的 `/stats/overview` **只发一次逻辑请求**（去掉 OverviewPage 的重复 fetch 后，dev 下 StrictMode 双调用显示为 2，生产构建为 1）。
- 深链：URL `?verify=1` 正确、按钮在位、**未自动执行**、高亮按 1.6s 后消失。
- 门禁：集成 **780 → 786 passed / 0 failed / 0 skipped**；ruff 全过；前端 build 绿、lint **7/0** 与 main 基线逐数一致；`package.json` 无变化（**未引图表库**）。

## 两轴评审与处置

- **Spec 轴**（对八条裁决逐条判定）：全部满足、无越界（无通知外发/无轮询/固定 7 日窗/无图表库/无新迁移）。指出两处覆盖薄弱：`citation_rate is None` 断言是条件式、`pending_qc`/`open_tickets` 角标无造数断言。
- **Standards 轴**：无 P0/P1；6 条 P2 全部修掉——①总览与 AppShell 重复请求 → 改为 AppShell 用 **context 下发一次取数**（数字单一来源）②角标导航闪烁 → 保留上一帧值 ③反馈行口径标注（全时段）④共享函数 `triage_asset_ids` 加固（`asset_id: null`/字符串/非 dict/`bool` 全部跳过——**这条同时救了既有反馈路径的 500 风险**）⑤滚动随 `prefers-reduced-motion` 降级 ⑥仪表取数失败由静默改为 `ErrorBanner` + 重试。
- 我另外补强了 Spec 轴点名的两处覆盖：引用覆盖率改成**无条件等价断言**（`rate is None == 分子为 0`，不依赖用例顺序）；新增 `test_badges_track_open_ticket_and_keep_shape`（走第 42 刀真实路径造 pending 工单，钉住 `open_tickets` +1 与 `handoffs` +1）。

记债（P2，未改）：`service_messages.created_at` / `service_sessions.created_at` 无索引（演示规模 195 行无影响，intake 的 Out 已记）；`pending_qc` 角标只做形状断言——造一条 `pending_qc` 要走素材生成链路、依赖模型底座，不适合当统计口径的验证手段（closeout 如实写明，未假装覆盖）。

## 诚实披露

- 趋势条是**手写 CSS 柱**（无图表库），纵轴三序列共用刻度；小值有 3px 最小高度，7 日合计以文字给出——不是精细图表，够用即止。
- 引用覆盖率**不是准确率**：分母含目录/工具模板回答（它们 `citations=[]`），UI 已明写口径。
- 反馈汇总为**全时段**（与同屏 7 日趋势不同窗），标题已标注。
- 角标只随**导航**刷新，不轮询：长时间停留单页不会自动更新（有意，符合「不做轮询」）。

## 后续

- 下一刀：**第 44 刀退货状态迁移**（`confirm_return` 落 `orders.status` 合法值集 + 事件；钉测「确认前已发货 → 确认后退货中 → 进度问句答退货中」）→ 第 45 刀（令牌 TTL + embed.js）→ **审计刀 8**。
- 仍待 Owner 裁决：工作队列首屏 183 条原始灌入货的呈现方式。
