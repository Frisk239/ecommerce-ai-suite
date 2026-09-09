# 工程第 28 刀两轴评审：生命周期出口刀（feat/lifecycle-exits）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（`10586a0`+`8470009`）。Spec：`.scratch/lifecycle-exits/spec.md` + ADR 0042（随 align 提交）+ docs/optimization-plan.md P1。

**净，可合。** 三闸门与 0042 逐条一致；字节交换新键先写旧键后删（delete 幂等、孤儿语义有注）；机洗重跑在 commit 后（纪律钉明）；audit 两新动作独立不混 writebacks；CONTEXT 单句 Edit；迁移 0014 可回；测试零删纯增。

P2 记债：旧键 delete 在 commit 前（commit 失败悬指窗口极小，注释可）；评审环境计数 523 与实现方 533 口径出入（Owner 复跑为准确值，见下）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed
- 无 DB：`tests=533 passed=394 failed/errored=0 skipped=139`
- 带 DB（5433）全量：`tests=533 passed=533 failed/errored=0 skipped=0`（基线 510 → 533 只增；迁移 0014 随 lifespan 跑通）
- Owner 浏览器实证三出口：修订 v4 换新正文（v3 text 含「15天无理由」——上传-确认-机洗重算-发布链）→「放弃修订 v4」确认（audit `discard_revision` 行+版本行删除+解锁）；GBK 失败资产 A-0018「废弃」（200+列表消失+已发布资产 A-0013 废弃 409 兜底）
