# 工程第 21 刀两轴评审：打码出口收口（feat/redact-exports）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `ae5f9f4`，处置 `4d155a8` 在后）。Spec：`.scratch/redact-exports/spec.md` + ADR 0038 修订段（随 align(21) 提交）。两轴合并评审。

## Standards

**评审抓到 P0×1+P1×1（均已实修）**：① MCP `get_asset` 第六出口漏（content/字段表原样出给外部 bearer 客户端）——实修 `_mask_fields_map`+content redact（幂等兜历史脏行）；② title 出口全线（对话 title=顾客首问截断可含号）——实修 `_first_question` 源头先掩后截（回流登记 title/会话摘要/MCP 三工具/降级回答标题一次干净）。净项：redact 全复用零复制；豁免（版本正文端点）注释钉死；CONTEXT 词条 Edit 精确替换（防第 17 刀事故重演）；SSE 流式推论链成立（prompt 无裸号则模型无从复述，顾客面无历史 GET）。

P2 记债：knowledge_gaps.question 顾客原问裸落治理台表（仅操作者消费，ADR 未列，下轮括注）；ADR 0038 修订段评审时未提交（Owner 随 align(21) 落盘，先例模式）。

## Spec

Must1–5 全落地（五出口+小修四项）；Must6 十例钉测含豁免钉死；处置后再 +4 例（get_asset/title 集成钉+边界单测）；446→458 只增；**零既有断言改动**；Out 零越界（redact 正则/版本字节/检索打分/表结构全未动）。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=458 passed=344 failed/errored=0 skipped=114`
- 带 DB（5433）全量：`tests=458 passed=458 failed/errored=0 skipped=0`（基线 446 → 458 只增）
- Owner 亲跑 redact 模块 10/10（处置前）；无新 UI 面（文案微改），集成测试覆盖验收线
