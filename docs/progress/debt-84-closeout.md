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

## 评审实修（单代理双轴）

- **P0 registry 漏注册（最讽刺的一处）**：新指标加进了 `record_*` 但没进 `build_metrics_registry` 的注册元组——**自增了却永不输出**，而我的钉子直读 `._value` 绕过了断链（closeout 原写「metrics live 未验证」恰好盖住此洞）。修：注册；钉子改为走 `registry.collect()` / 端点输出（`m.name` 是 Prometheus 基名，`_total` 只在文本输出加——钉子首版写错后缀，实测暴露）。
- **P1 废弃影响面又只收一处**（83 刀同类教训重演）：`retry_machine_wash`（discarded→pending_review 僵尸：此后 publish 拒、再 discard 拒、无 un-discard）、`_can_replace_version_bytes`、`asset_view` 的内联 `can_publish` 重复闸——三处一并加废弃判断；`SimpleNamespace` 替身同步补字段（不为替身留生产分支）。
- **P1 `missing_entity` 未过 redact**：`_normalize` 保留数字，手机号问句可成 OOV 实体串——原文进拒答首行/缺口/工单，而同消息摘要行有 redact（0038 同出口双口径）。修：实体串过 `redact`。
- P2：README 指标节「六个→七个」+ `from,to` 登记（63 刀纪律）、observability 模块 docstring 标签清单、`register_from` 的 TOCTOU 记债。

## 记债（本刀未吞）

- 每 ask 三笔全库级查询合并/缓存（单独一刀：动检索主路径，需评测前后各跑）。
- `ops.fetch_published_refs` 的废弃过滤（低险：废弃闸挡已发布资产，仅手工 SQL 废弃会漏）。
- 中间地带（修饰语不可证伪）与 OOV 搭车建工单的语义裁决（历刀结转）。

## 后续

本会话七刀：PR #114–#119 + 本刀。Owner 待裁：部署面（暂缓）/ 其余记债顺序。
