# 工程第 20 刀 closeout：血缘视图（feat/lineage）

日期：2026-09-08。上刀 intake：`docs/progress/coaching-intake.md`（通过）。短对齐：`.scratch/lineage/spec.md`（0026 完全覆盖，不新开 ADR）。基线=`origin/main`（8120d9e，PR #24 合并后）。

## 交付（0026 落地：拼出来的派生视图，零新表零写路径）

1. **`GET /api/assets/{id}/lineage`**：`services/lineage.py` 固定 4 查询拼装——origin（来源种类）/ versions_audit（发布/回滚/确认时间线）/ usages 三块：**引用**（`service_messages.citations` JSONB containment `@>` 下推，总计数+样例 10 条带问句/版本/会话/时间）、**写回**（audit_log publish+rollback——0034 回滚也写回，每条带 action 与按 version_no 从 confirmed_fields 派生的字段名列表）、**考核**（coach_records question_key containment，题面+版本+时间）。
2. **web**：AssetDetailPage 侧栏「血缘」折叠面板（头部汇总「引用 N · 写回 N · 考核 N」；三块列表；写回行带发布/回滚徽章与字段名；空态「还没有被使用的记录」）。
3. **测试**：单测 12（拼装纯函数/回滚事件/字段派生/截断）+集成 6（发布 v2→回滚 v1 全链路 writebacks 三行；引用/考核带版本号；空资产；401/404；containment 下推断言）。

## Owner 验收（浏览器点穿）

A-0009（第 12 刀 QA 资产，本会话多刀验收的使用史）详情页血缘面板：「**引用 5 · 写回 1 · 考核 1**」——五条真实问句样例（净含量/卖点/内胆/还剩多少，会话 #23–#31 各带 v1）、写回行（operator+时间+诚实注释）、考核记录 #1（题面+v1）——全链路历史数据在一张视图串通，正是 0026「答错追溯到哪条资产被谁用过」的验收线。

## 两轴评审与处置（`docs/progress/lineage-review.md`）

两轴各 1 个 P1（同源）：回滚写回事件丢失（0034 口径）+fields 可派生未派生——**均已实修**（WRITEBACK_ACTIONS+action 徽章+confirmed_fields 键派生+全链路集成钉测）。P2 记债：origin.created_at 恒 null（无登记时间列，不发明时间）、两块无上限、样例 key 同刻可撞。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=446 passed=335 failed/errored=0 skipped=111`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=446 passed=446 failed/errored=0 skipped=0`（基线 428 → 446 只增）
- web：`npm run build` 通过

## 遗留

- MCP export 无留痕表——血缘「导出」环缺（0026 列导出为一环；微调导出 0028 已禁；若后续做 MCP 调用日志再拼）。
- 评审 P2 三条；audit-4 候看清单持续累积（17/18/19 刀+本刀）。

## 下一刀

刀计数：第 20 刀——**计数线到，下一刀=审计刀 4**（自审计刀 3 后五刀：16 还债/17 素材/18 切片/19 考核/20 血缘；三路子代理：设计符合性/技术债/功能缺口；候看输入=各刀 review/closeout 债务+CONTEXT 事故复盘+运营 Agent 缺口）。CONTEXT 推进句已回写。
