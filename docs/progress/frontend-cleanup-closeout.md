# 工程第 25 刀 closeout：前端收口刀（feat/frontend-cleanup）

日期：2026-09-08。上刀 intake：`docs/progress/debt-2-intake.md`（通过）。短对齐：`.scratch/frontend-cleanup/spec.md`（audit-4 债池前端部分；零行为变化无新 ADR）。基线=`origin/main`（bfcab23，PR #30 合并后）。

## 交付（audit-4 前端债一揽子，行为零变化）

1. **toUiMessage 工厂**（MessageBubble.tsx:41）：UiMessage 13 字段构造单点，两页 4 处内联删净。
2. **useAskStream 共享 hook**：ServicePage/CustomerPage 的 ask 双闭包收敛（差异参数化：gap_id 随载荷类型/建议列表留页内；stop/reset/prune/markStopped/onSettled 逐行对齐旧实现）。
3. **AssetAnchorChip**：CoachPage 三处手写题源锚 Link 组件化（不复用 CitationChip——青底引用芯片与资产详情链形状语义不同，注释注明）。
4. **ActionError 统一**：9 处（7 文件）内联 `role="alert"` 收敛（plain/compact/prominent 三档 DOM 冻结）；ErrorBanner 保留 API 错误+重试场景。

## Owner 验收

pytest 491 基线不变（api 零 diff）；tsc/build 过；浏览器抽查行为等价：顾客通道「SO-1001」问答（工具条+已发货）、考核页 `A-0009 · v1` 锚芯片。

## 两轴评审（`docs/progress/frontend-cleanup-review.md`）

净可合；P2×2（reset 主动 abort 微差/锁输入毫秒延长）。

## 计数（摘自命令输出，junitxml 机械计数）

- pytest 无 DB：`tests=491 passed=368 failed/errored=0 skipped=123`（不变）
- web：`tsc --noEmit` 0 错、`npm run build` 通过

## 遗留

web 测试基建（vitest）Out（单独评估）；audit-4 P2 池不变（token TTL/孤儿字节/放弃修订/检索缓存等，进审计刀 5 候看）。

## 下一刀

刀计数：第 25 刀（审计刀 4 后第 5 刀）——**计数线到，下一刀=审计刀 5**（审第 21–25 刀：打码出口收口/运营 Agent/总览连接页/后端债池/前端收口；三路子代理不改产品代码）。CONTEXT 推进句已回写。
