# 第 87 刀 intake：对第 86 刀（OOV 对照商品表）的吸收核验

日期：2026-09-16。核验人：本刀 Owner 会话。

## Merge 状态

`feat/oov-catalog-86` 已合并进 main（PR #122，merge commit 5c6b488）。工作区仅一个未跟踪文件 `scripts/eval/out/report-judge-86.md`（上个会话尝试补跑 faithfulness judge 的半途产物：48/80 评上后 `LLMError: LLMUnavailable` 中断）——正是本刀要处置的对象，非泄漏非垃圾。

## 证据抽查

| 声明（86 closeout） | 核验 | 结果 |
| --- | --- | --- |
| 集成 1200 passed / 0 skipped | 全量 `uv run pytest`（显式带 `SUITE_TEST_DATABASE_URL`，真库） | 见 closeout 摘录（本刀同批跑） |
| run_eval 逐位不变（90.0/86.2） | 演示库实跑：positive 90.0/90.0、paraphrase 88.0/96.0、confusion 73.3/93.3+混淆 73.3、refusal 100.0、overall 86.2/92.5 | **逐位吻合** ✓（与 83 刀认账后的基线一致） |
| live 双分支（不在库=戴森吸尘器；在库=辛丑條約） | 未重跑（演示库数据可漂，历史 live 已留档；引擎行为由 1200 集成含本刀 +3 钉子覆盖） | 采信 + 由本刀全量测试间接复核 |

## 环境

- 演示库 compose db 起在 5433，pg_isready 绿。
- LLM 网关（complete_chat 探针一次）**当前可用**——judge 补跑窗口成立。

## Spec vs claim 抽样

- 「不在库不落缺口、在库落缺口」：closeout 声称 +3 钉子（判据表/在库落缺口/不在库无缺口），与本刀全量测试收集数一致，采信。
- 「存量 8 条 OOV 悬空缺口外科置 resolved」：演示库治理动作，无从代码复核，留档采信（demo_reset 只报告不清缺口的口径未破坏）。

## 记债（上一刀结转，本刀裁决归属）

1. `ops.fetch_published_refs` 未过滤 `discarded_at`（84 刀记债）→ **本刀顺手清**（Must 之一）。
2. OOV 中间地带保守度（81/85 刀结转语义裁决）→ 仍留 Owner 裁决池，非本刀。
3. 部署面 → Owner 暂缓中，非本刀。

## 结论

**通过**。可开第 87 刀（faithfulness judge 补跑 + goal 收官订正）。
