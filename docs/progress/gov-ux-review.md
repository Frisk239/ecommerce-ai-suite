# 工程第 30 刀两轴评审：治理体验小刀（feat/gov-ux）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`ed39f95`+`77a2236`）。Spec：`.scratch/gov-ux/spec.md`（优化计划 P2 治理体验）。

**净，可合。** 归一化快慢路径均走 normalized_question、迁移 0015（回填仅 open/删重留最小 id/索引重建/resolved 不动）钉死；提示条用掩码视图无新原文出口；title 兜底不覆盖缺口预填；ProductOut stock 仅治理台路由（MCP/顾客面零泄漏）；被更断言仅措辞（精确→归一化）语义等价。

P2 记债：UI 两件（提示条/预填）无自动化钉测（web 无测试基建历史空缺，Owner 浏览器实证补位）；SQL btrim 空白集窄于 Python strip（\v\f/U+3000 极端输入可分叉，实际难触发）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed
- 无 DB：`tests=556 passed=410 failed/errored=0 skipped=146`
- 带 DB（5433）全量：`tests=556 passed=556 failed/errored=0 skipped=0`（迁移 0015 随 lifespan 跑通；只增）
- Owner 浏览器实证：「发票怎么开具？」与「发票怎么开具」两问→**open 缺口恰 1 条**（归一化幂等）；商品页「库存 42/库存 0」呈现；「去补文档」抽屉顶部提示条「发布后该缺口将自动解决」
