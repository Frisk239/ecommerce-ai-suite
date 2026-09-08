# 工程第 20 刀两轴评审：血缘视图（feat/lineage）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时两笔 feat，处置 `c183631` 在后）。Spec：`.scratch/lineage/spec.md` + ADR 0026。两轴合并评审（纯拼装小刀）。

## Standards

零新表零写路径（0026 核实）；citations/question_key JSONB containment 真下推（编译断言+行为验证双钉）；固定 5 查询无 N+1；UI 用词/token 合规。

**P1×1（已实修）**：writebacks 只取 publish——回滚也写回（0034），回滚写回事件丢失。实修：`WRITEBACK_ACTIONS={publish,rollback}`+每条带 `action`（UI「发布写回/回滚写回」徽章）。

## Spec

Must1–5 全命中；443→处置后 446 只增；Out 零越界。

**P1×1（已实修）**：spec 字面要求 writebacks 带 `fields` 而实现省略——实修：按事件 version_no 从 `confirmed_fields` 派生键列表（如实非现编，空列表兜底）；补「发布 v2→回滚 v1」全链路集成（writebacks 三行 rollback/publish/publish=v1/v2/v1，回滚行 fields=目标 v1 确认值）。

P2 记债：`origin.created_at` 恒 null（assets 无登记时间列，「不发明时间」成立，spec 用户路径「登记时间」勘误）；writebacks/coaching 无上限（规模可）；样例 key 同刻可撞。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=446 passed=335 failed/errored=0 skipped=111`
- 带 DB（5433）全量：`tests=446 passed=446 failed/errored=0 skipped=0`（基线 428 → 446 只增）
- web：`npm run build` 通过
- Owner 浏览器点穿（A-0009）：血缘面板「引用 5 · 写回 1 · 考核 1」——五条真实问句样例（会话 #23–#31 各带 v1+时间）、写回行、考核记录 #1；全链路历史数据串通
