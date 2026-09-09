# RAG 评测尺报告（第 35 刀，goal §6.2.2）

验收口径：本文所有数字**机械粘贴自 `scripts/eval/run_eval.py` 的 stdout**（同库同种子实跑两遍，输出 diff 为空）。报告在先、改动在后：此后任何检索侧改动（第 36 刀同义词接线、rerank）必须在本报告补 before/after 对照才许留（CONTEXT「评测报告」词条）。

## 环境

- 日期：2026-09-09；分支 `feat/rag-eval-ruler`。
- 演示库：`postgresql://suite:suite@localhost:5433/suite`，已发布资产（指针非空、含切块）52 个，按 `source_kind`：upload 45 / session_backflow 4 / material_generated 2 / clip_pick 1。
- 语料家族（分层解读用）：中文电商评论资产 ×20（水果/衣服/酒店/平板/洗发水/书籍等）、OFF 开放食品规格 ×20（德/法/英混排）、种子规格与政策 ×5（保温杯规格/退货政策/尺码对照/无标题文档/补口径）、回流对话 ×4（中文 QA ×1 + ABCD 英文 ×3）、生成素材 ×2 + 切片 ×1。
- golden 集：`scripts/eval/out/golden_large.json`，96 条，固定种子 42（`scripts/eval/generate_golden.py`）。分布：positive 40（40%）/ paraphrase 25（26%）/ confusion 15（16%）/ refusal 16（17%）。
- 应拒答话题探测：候选 24 条，8 条因库里有对应资产被同口径剔除（会员积分、积分商城、分期付款、海外下单、母婴用品、门店地址、到店自提、延保服务——探测用与检索同一套 `query_terms`/`score_chunk`，积分规则块在库故「会员积分」类话题不构成无证据）。
- LLM judge（`--judge`）：本机已配 key，但演示网关当日不稳定（APITimeoutError → APIConnectionError），判卷走通一次（43/79 条评上）后中断，按 spec「LLMError 跳过并注明」处置——**judge 列不纳入正式数字**；检索层指标零 LLM 依赖，两遍数字与 judge 干扰前后逐位一致。

## 实跑数字（RUN1 stdout 原样）

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     70.0%     75.0%       -    2.5%       -
paraphrase     25     56.0%     64.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     63.7%     72.5%  100.0%    2.5%   60.0%
```

## 可复现性

生成与评测各跑两遍（同库、种子 42）：

- `generate_golden.py` RUN1 vs RUN2 stdout diff 为空；
- `run_eval.py` RUN1 vs RUN2 stdout diff 为空（含逐条 golden 内容 diff 为空）。

数字逐位一致，即「同库同种子完全可复现」的直接证据。

## 分层解读

- **正例组（40，来自 OFF 规格 / 中文评论 / 种子资产正文）**：recall@1 70%、@3 75%，误拒 2.5%（1 条）。12 个 top-1 miss 的主体是「同模板字段跨资产同文」——`品牌：…`、`销售国家：…`、`净含量：…` 在多个 OFF 资产间逐字相同，词法分并列时按 (asset_id, chunk) 稳定出榜，把命中拉向同文邻居资产。这是真实语料的重复证据问题，是 chunk 元数据前缀（商品名注入）与 rerank 的候选靶子（调研 §4/§6）。唯一误拒（pos-038「补口径 · 你们几点上班怎么样」）根因：问句自标题生成，而标题不进切块索引——标题词不构成可检索证据，记为已知边界。
- **同义改写组（25，同义词轮转 + 句式变换，不依赖 LLM）**：recall@1 56%、@3 64%——较正例组 **@1 -14pp / @3 -11pp**。miss 集中在被轮转的词上：参数（←规格）、容量（←净含量）、退换（←退货）、评价（←评论）……即 vocabulary mismatch 的直接实测（调研 §1/§7）。**本组数字即第 36 刀检索侧同义词接线的 before 基线**；接线后须在本报告追加 after 对照。
- **跨商品混淆组（15，两资产共有汉字 bigram 组问句）**：recall@1 60%、@3 80%，混淆@1 60%。走错的 6 条全部是字段名同文跨 OFF 资产的并列（条码/来源/配料/品牌/净含），与正例组 miss 同根因；净含/含量 类共有词多被拉向「块更短更实」的资产（QA 对话块），词法分的长度归一行为符合 `score_chunk` 设计预期。
- **应拒答组（16）**：拒答率 100%（16/16），零编造。样本只覆盖 16 个无证据话题，且生成期探测与运行时同口径，该 100% 验证的是「拒答判定无假阴性泄漏」，不证明对所有开放话题拒答。
- **总体（96）**：recall@1 63.7%、recall@3 72.5%。

## 已知边界与下一步（均非本刀范围）

1. 同义词接线（36 刀）：以同义组 56% 为 before，目标逼近正例组 70%。
2. OFF 重复字段证据：元数据前缀或 rerank 触发数据见上。
3. faithfulness 列：judge prompt 已收敛为「逐句证据依据」判据，待网关稳定补跑 `--judge` 单列追加。
4. golden 大集不进 CI（spec Out）；CI 回归集仍为静态 13 条 `golden.json`，未动。

## 复现命令（仓库根目录）

```
uv run python scripts/eval/generate_golden.py --db postgresql://suite:suite@localhost:5433/suite
uv run python scripts/eval/run_eval.py --db postgresql://suite:suite@localhost:5433/suite --golden scripts/eval/out/golden_large.json
```
