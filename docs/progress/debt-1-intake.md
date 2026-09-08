# Intake · 第 16 刀还债刀（feat/debt-1）

- 日期：2026-09-08（Slice Owner 接手第 17 刀）
- Prev slug：`debt-1`；实现提交+评审处置两笔+文档三笔，合入 `5bfc7ac`（PR #21）
- Merge 状态：**已合入 default**。第 17 刀从 `origin/main` 起 `feat/material-center`。
- 特殊性：同会话 Owner 一手验收（门禁/浏览器行为抽查/两轴评审/合并），下表为当时原始记录。

## Evidence（Owner 一手，junitxml 机械计数）

| 项 | Closeout 声明 | Intake 复核（同会话原始输出） |
|---|---|---|
| 无 DB | `tests=327 passed=236 failed/errored=0 skipped=91` | 同 |
| 带库全量（5433） | `tests=327 passed=327 failed/errored=0 skipped=0` | 同（基线 316 → 327 只增） |
| ruff | All checks passed | 同 |
| Owner 浏览器抽查 | 工具仍命中/规格问句回检索/政策问句拒答 | 亲历：「钛钢保温杯有货吗」get_stock 42 件；「保温杯还剩多少毫升」检索引用 A-0003+A-0009 真模型诚实回答；「库存政策是什么」检索拒答转人工 |

## Spec vs claim（抽查 3 项）

1. **跨循环客户端** — PASS。按 loop 懒建+混跑钉测（替身模拟 loop 亲和）。
2. **回流 LLM 前释放事务** — PASS。register_asset 与 retry 双路钉测（in_transaction=False）。
3. **词表收窄双前置** — PASS。单测哨兵+集成政策文档可命中+ADR 0037 修订段；既有断言更新=对称收紧。

## Safety

无秘密入库；纯工程修复零新功能面。

## 债务（轻）

分派谓词可提纯函数；词表命中无匹配时全表扫（规模可忽略）；一次性 loop 客户端 fd churn 面；密钥热轮换需重启（与修复前同口径）。

## Verdict

**通过** — 进第 17 刀素材中心（audit-3 排期首位）。刀计数：第 17 刀。
