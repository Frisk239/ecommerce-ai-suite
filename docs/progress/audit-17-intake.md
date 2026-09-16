# 审计刀 17 intake：对第 87 刀（faithfulness judge 跑全）的吸收核验

日期：2026-09-16。审计范围预告：第 81–87 刀（PR #117–#123）。

## Merge 状态

`feat/faithful-judge-87` 已合并进 main（PR #123，merge commit f5d3111，2026-09-16T03:12Z）。CI 双绿（lint + test 均 success，check-runs API 确认）。

## 证据抽查

| 声明（87 closeout） | 核验 | 结果 |
| --- | --- | --- |
| 集成 1204 passed / 0 skipped | 本刀会话亲跑（显式带 SUITE_TEST_DATABASE_URL；收集计数 1204 = main 基线 1200 + 4） | ✓ |
| judge 跑全 80/80 零失败 | 本刀会话亲跑两遍（run_eval --judge + 独立逐条脚本），聚合逐位一致 8/80 | ✓ |
| 检索层逐位 = 83 刀基线 | 亲跑不带 judge 基线，90.0/86.2 逐位吻合 | ✓ |
| 钉子承重（fetch_published_refs） | 回退过滤跑即红、恢复即绿（stash 验证） | ✓ |

## 记债（87 刀结转，本审计裁决归属）

- LLM 生成路径 faithfulness 评测（多次采样形态）——观察项，非本审计范围。
- judge prompt 对原文粘贴形态松紧——观察项。
- rag-eval-report「定位订正」与 goal.md 自洽性——交 A 轴对账。

## 结论

**通过**。开审计刀 17（覆盖 81–87；三路并行子代理 + Owner 亲跑门禁与抽查）。
