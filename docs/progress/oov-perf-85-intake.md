# 第 85 刀 intake + 对齐（OOV 判定性能：语料快照缓存）

Owner：部署面暂缓；做唯一还值得单开一刀的技术项（每 ask 三笔全库级操作）。

## 测量（先测量后优化）

| 项 | 改前 |
| --- | --- |
| 每 ask 查询 | 3 笔 / 990 行 |
| `retrieve` | 6.6–7.2ms（1 笔候选） |
| `oov_verdict` | **11.8ms**（2 笔：候选 483 行 + titles 54 行；含全量 bigram 抽取 + idf 重算）——**比 retrieve 本身还慢** |

## Must
1. 语料（corpus bigram 集 / 字段名词表 / titles / 标题 idf）按资产摘要缓存，失效覆盖发布/修订/治理改标题/废弃/增删；
2. 护栏改轻量 count（不再为判规模拉全部文本）；
3. OOV 判定行为零变化（`oov_criterion.py` 全部通过 + golden 判别不变）+ 缓存失效有钉子（新增/废弃/改标题三向）。

## Out
- retrieve 与 OOV 的查询彻底合并（需改 retrieve 返回契约，收益低于风险——快照缓存已把重活消掉）。
