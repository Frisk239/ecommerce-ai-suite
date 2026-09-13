# 第 84 刀 closeout：结转记债清偿（三件）

日期：2026-09-13。`feat/debt-84`。**无新表无迁移**。

## 交付

| 件 | 交付 |
| --- | --- |
| `_can_publish` 废弃闸 | `routes/assets.py`：已废弃资产（0042 discarded_at）不可再发布——否则新版在检索/导出面恒不可见（83 刀把废弃过滤落到证据面后暴露的状态机冲突）。钉子：`test_lifecycle_exits.py`（同形态 + 废弃 → False） |
| 会话生命周期指标 | `service_session_transitions_total{from,to}`（observability，标签名保留 from/to 惯例——`from` 是 Python 保留字只能展开传）；三处埋点：顾客/操作者建会话 `new->active`、顾客结束 `active->ended`（仅 rowcount>0 记，幂等重入不记）、操作者回流 `{active\|ended}->registered`（from 在条件更新**前**取——update 会同步内存值，收口后读会成 registered->registered）。钉子：`test_session_end_integration.py`（计数 + 幂等不重复） |
| OOV 点名文案 | `build_refusal_handoff_content(..., missing_entity=)`：OOV 首行「抱歉，已发布资料里没有与「X」相关的信息，已记录并转人工处理。」；非 OOV 路径首行仍是 `REFUSAL_CONTENT` 常量原样（既有全等断言不破）。钉子 ×2（纯函数 + 引擎落库消息） |

## 验收

- 集成 **1195 passed / 0 skipped**（1191→1195，+4）；ruff 全过。
- live（rebuild）：OOV 问句「戴森吸尘器的配料是什么」→ 拒答且**落库首行点名实体**（「…没有与「戴森吸尘器」相关的信息」+ 问句摘要 + 缺口 G-0073）。
- 指标 live 未验证（本机 `.env` 无 `METRICS_TOKEN`，`/metrics` 恒 401 fail-closed 是设计）——由单测钉子覆盖，如实披露。

## 记债（本刀未吞）

- 每 ask 三笔全库级查询合并/缓存（单独一刀：动检索主路径，需评测前后各跑）。
- `ops.fetch_published_refs` 的废弃过滤（低险：废弃闸挡已发布资产，仅手工 SQL 废弃会漏）。
- 中间地带（修饰语不可证伪）与 OOV 搭车建工单的语义裁决（历刀结转）。

## 后续

本会话七刀：PR #114–#119 + 本刀。Owner 待裁：部署面（暂缓）/ 其余记债顺序。
