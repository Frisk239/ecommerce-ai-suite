# 工程第 24 刀两轴评审：后端债池刀（feat/debt-2）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（`3b8c1e3`）。Spec：`.scratch/debt-2/spec.md`。两轴合并轻评审，评审子代理真 PG 复核。

**净，可合。** GIN 迁移（jsonb_path_ops 与 `@>` containment 匹配、down 对称、只经 alembic、up/down/up 钉测）；单资产推导与列表端点同源+prompt 逐字节等价钉测+404 文案不变；批取一次 IN+SQL 计数钉测；四小条全落（retry 直入 running/qc 同串/storage 死参数删净/reject commit 收服务层）；前端零改动、响应形状不变、收集只增。

P2 记债：qc 截断改变超长 title 边界（spec 明示非回归）；SQL 计数断言用子串匹配略脆；spec「485 基线」与实收 475 口径差（开刀时收集数漂移，实现后 491 只增方向正确）。

## 计数（Owner 复跑，junitxml 机械摘取）

- ruff：All checks passed
- 无 DB：`tests=491 passed=368 failed/errored=0 skipped=123`
- 带 DB（5433）全量：`tests=491 passed=491 failed/errored=0 skipped=0`（基线 485 → 491 只增；迁移 0013 随 lifespan 跑通）
- Owner 抽跑 GIN 迁移+coaching 模块 32/32
