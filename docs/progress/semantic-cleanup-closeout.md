# 工程第 26 刀 closeout：语义收口小刀（feat/semantic-cleanup）

日期：2026-09-08。上刀 intake：`docs/progress/audit-5-intake.md`（通过）。短对齐：`.scratch/semantic-cleanup/spec.md`（audit-5 排期首位；零新功能面无新 ADR）。基线=`origin/main`（83bf183，PR #32 合并后）。

## 交付（audit-5 P1 簇清零）

1. **打码漏网四处（0038 修订补全，含评审实修）**：material 生成 prompt（ops 同款）；ops_runs 落库三函数+**output 面**（厂商 title/body+兜底标题——评审 P1 实修）；MCP 三出口 title `_mask_title`（落库原文不动钉）。
2. **ops 三修**：列表批取消 N+1（查询探针钉）；retry/deliver `with_for_update` 行锁（docstring 裁决注记）；mcp 首插 SAVEPOINT+IntegrityError 兜底（并发恰一行钉）。
3. **血缘导出环拼装（0026 最后一环）**：usages.exports 块（audit_rows 派生零新查询）；前端「导出」块+头部计数；留痕时间线「导出 · MCP」中文化。
4. **gaps.question 出口掩**（落库原文不动双钉）；slices 结构补净。

## Owner 验收

门禁全绿（505/505）；浏览器真实链路：MCP export A-0009 一次→血缘「引用 5 · 写回 1 · 考核 1 · 导出 1」+「导出 · MCP」留痕行。

## 两轴评审（`docs/progress/semantic-cleanup-review.md`）

P1×1（ops output 面）实修+2 钉测；其余净；P2×2 记债。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed
- 无 DB：`tests=505 passed=376 failed/errored=0 skipped=129`
- 带 DB（5433）：`tests=505 passed=505 failed/errored=0 skipped=0`（基线 491 → 505 只增）

## 遗留

P2×2（gaps product.name 口径/slices 空行）；audit-5 P2 池不变（部署面进第 28 刀起）。

## 下一刀

刀计数：第 26 刀。**第 27 刀=演示收官刀**（README 3 分钟口述稿——goal 最后一条完成标准；总览「其余能力」行；拒答交接摘要（问句+缺口芯片）；ruff format 基线收口）——**达成即完成标准 7/7**。审计刀 6 于第 30 刀后。CONTEXT 推进句已回写。
