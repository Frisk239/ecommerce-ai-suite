# 工程第 30 刀 closeout：治理体验小刀（feat/gov-ux）

日期：2026-09-09。上刀 intake：`docs/progress/multi-turn-intake.md`（通过）。短对齐：`.scratch/gov-ux/spec.md`（优化计划 P2 治理体验四件；无新 ADR）。基线=`origin/main`（5e69061，PR #36 合并后）。

## 交付（优化计划 P2 治理体验四件）

1. **缺口问句归一化**：`normalize_question`（strip/全半角统一/去尾标点）；查重与插入走 `normalized_question`（迁移 0015：加列+open 回填+归一化删重留最小 id+索引重建到新列；resolved 不动）——「兑换？」与「兑换」不再产生两条待办。
2. **「去补文档」提示条**：抽屉顶部「本资产用于补口径：{问句}——发布后该缺口将自动解决」（掩码视图）+登记成功详情页横幅。
3. **标题文件名兜底**：title 空时预填文件名去扩展（缺口预填不覆盖）。
4. **商品页库存列**：卡片头部「库存 N」（NULL=—，0 红显只读）；ProductOut 仅治理台路由（MCP/顾客面零泄漏）。

## Owner 验收（浏览器实证）

「发票怎么开具？」+「发票怎么开具」两问→open 缺口**恰 1 条**（走查发现的 G-0001/G-0003 重复问题消除）；商品页库存 42/0 呈现；去补文档提示条在。

## 两轴评审（`docs/progress/gov-ux-review.md`）

净可合；P2×2（UI 无自动化钉测——Owner 浏览器实证补位；SQL btrim 空白集窄于 strip 极端分叉）。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed
- 无 DB：`tests=556 passed=410 failed/errored=0 skipped=146`
- 带 DB（5433）：`tests=556 passed=556 failed/errored=0 skipped=0`（只增；迁移 0015 跑通）
- web：`npm run build` 通过

## 优化计划执行对账

`docs/optimization-plan.md` 排期：第 28 刀生命周期出口 ✓（PR #35）、第 29 刀多轮记忆 ✓（PR #36）、第 30 刀治理体验 ✓（本刀）——**P1 族与 P2 可修项全部落地**；剩观察项（图片素材/投放日历/切片真转写——演示需要时裁决）与第 31+ 部署阶段。

## 下一刀

刀计数：第 30 刀——**计数线到，下一刀=审计刀 6**（审第 26–30 刀：打码收口/运营/总览连接/债池×2/前端收口/生命周期出口/多轮记忆/治理体验——自审计刀 5 后五刀：26-30；三路子代理，不改产品代码）。CONTEXT 推进句已回写。
