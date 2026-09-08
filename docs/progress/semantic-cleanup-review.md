# 工程第 26 刀两轴评审：语义收口小刀（feat/semantic-cleanup）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（`4adc8cf`+处置 `0591939`）。Spec：`.scratch/semantic-cleanup/spec.md` + audit-5 排期。两轴合并轻评审。

**评审抓 P1×1（已实修）**：ops output 落库面（厂商 title/body+兜底标题）未掩——实修统一过 redact+2 钉测（output 面收尾闭环）。

净项核实：三掩齐（material prompt 同款/ops_runs 落库三函数/MCP 三出口 `_mask_title`，`assets.title` 行原文不动钉）；三修齐（批取+查询探针/with_for_update 行锁含裁决注记/mcp SAVEPOINT 兜底）；exports 块零新查询不混 writebacks；gaps 出口双钉；+14 钉测 491→505 只增；Out 零越界。

P2 记债：gaps 列表 product.name 口径不齐；slices 本刀仅补空行（结构已在 83bf183 清）。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed
- 无 DB：`tests=505 passed=376 failed/errored=0 skipped=129`
- 带 DB（5433）全量：`tests=505 passed=505 failed/errored=0 skipped=0`（基线 491 → 505 只增）
- Owner 浏览器验证：真实 MCP export 一次→A-0009 血缘头部「引用 5 · 写回 1 · 考核 1 · **导出 1**」+留痕时间线「**导出 · MCP** · v1 · 操作者 #2」中文化
