# 工程第 8 刀 closeout：顾客通道（feat/customer-channel）

日期：2026-09-08。上游：ADR 0021（顾客不登录、会话级身份、同一引擎、顾客不能发布/回流）+ 0033（令牌+两级限流）。短对齐：`.scratch/customer-channel/spec.md`（含验收后裁决补记）。基线=main（PR #9 合并后）。

## 交付（一条厚路径）

顾客打开 `/customer`（无登录、不进操作者壳）→「开始咨询」签发会话令牌 → 同一引擎（检索→厂商模型→降级/拒答）SSE 流式回答 + 引用芯片（只读版，`A-XXXX · vN`）→ 无证据拒答+转人工（缺口照常落库）→ 操作者客服页历史列表见「顾客」徽章会话 → 回流登记为对话资产 → 登记后顾客再问 409 显性。狂刷：会话发问 10/60s、IP 发问 30/60s、IP 建会话 5/60s，超限 429+Retry-After。

- **数据**：迁移 0005 `service_sessions.customer_token`（nullable+unique；非空即顾客会话，不加 origin 列）。表结构变化映射 ADR 0021/0023（无新领域决策，未新开 ADR）。
- **后端**：`routes/customer.py`（签发+发问两端点，Bearer compare_digest+WWW-Authenticate，无 GET/列表/回流）；`services/chat_engine.py`（从 service.py 抽出 `run_ask`/`sse_event_stream`，操作者/顾客同调；顾客版 complete 不带 gap_id——载荷白名单）；`services/rate_limit.py`（SlidingWindowLimiter，deque+Lock，时钟/阈值注入挂 app.state）。
- **前端**：`/customer` 路由无守卫；`MessageBubble` 抽共享组件（操作者预览可跳引用/顾客只读）；客服页会话列表「顾客」徽章（列表 API 加 origin）。

## Owner 浏览器验收（全过）

真模型「净含量为500ml。」+ `A-0003 · v1`；拒答+转人工；complete 无 gap_id（页面上下文抓 SSE 事件核验）+ 错令牌 401+`WWW-Authenticate: Bearer`；操作者回流登记 `A-0004`（待人洗）→ 顾客再问 409 alert「当前状态: registered」；狂刷实测第 11 问 429（Retry-After=41）、第 5 次建会话 429（Retry-After=34）。

**验收揪出并修掉**（`f141bff`）：409/401/429 等未进引擎的失败残留空 agent 占位并标「已停止展示 · 完整回答已留档」——该场景后端没有落任何回答，文案不实。修为：收到过事件（引擎先落库再流式）才保留停止标注，空占位移除、原因由 alert 表达。操作者 ServicePage 同款表现记债务（行为零变化闸，本刀不动）。

## 两轴评审与处置

- Standards：**零硬违规**，7 条 judgement call。实修：CitationChipPlain 复用 `labels.formatAssetId`（展示约定单一来源）。
- Spec：Must 1-6 全落地、无 scope creep；核查通过项（令牌恒定时间比较、限流器锁内剪枝、迁移旧行全 NULL、引擎抽取后操作者 complete 逐字节一致、既有测试文件零改动）。实修：顾客 Bearer 通道 fetch `credentials:'omit'`（0033 从「服务端不消费」收紧到「也不外发」）；XFF 第一跳托底口径补集成测试（此前无断言）。提交 `22cb6a2`。

## 计数（摘自命令输出）

- 仓库根无 DB：`121 passed, 45 skipped in 6.35s`
- 带 `SUITE_TEST_DATABASE_URL` 全量：`166 passed in 19.68s`（上刀 153 + 顾客集成 7 + 限流单测 7 - 计数口径：新增 13）
- `ruff check apps packages`：All checks passed
- web：`npm run build` ✓（323ms）；`npm run lint` 0 errors

## 遗留（下一刀议题 + 债务，接既有排期）

**安全面（下一刀优先评估）**：
- XFF 第一跳可自报伪造（直连部署无前置代理时 IP 托底可被换头绕过）——建议可配置「直连模式只信 remote addr」
- 限流先于鉴权：无效令牌替真顾客会话耗配额；404 先于 401 存在性可探测
**债务**：ServicePage 空占位同款表现、askService/askCustomer 分发闭包收敛、UiMessage 字面量工厂（两页 send 各 11 字段重复）、ACTIVE 常量集中、rate_limit `check` 命名（检查+记账一体）、AbortController 即弃（顾客页无停止按钮）。

## 下一刀

七块能力：客服/中台/MCP 三块已有闭环，顾客入口（0021）本刀补齐接待侧；下一刀从运营（商品/资产运营台深化）或切片（0028 排队中）按计划者方向定；审计刀 2 约第十一刀。
