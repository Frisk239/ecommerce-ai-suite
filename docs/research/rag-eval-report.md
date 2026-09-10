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

---

## After（第 36 刀同义词接线，2026-09-09）

接线演进：替换式归一（query=apply_synonyms(query)）after 复跑为**净负**——paraphrase @1 仅 +4pp 而 positive @1 -5pp、confusion @1 -13.3pp（替换丢原词 bigram）——按 goal §6.2.2「无提升不留」纪律当场改**并集扩展**（terms = 原查询 ∪ 归一后，token expansion，ES synonym 工业惯例同款；score 分子只增不减，数学上保证既有命中不丢）。

### After 数字（并集扩展，同集同种子，Owner 复跑逐位一致）

| 分布 | 条数 | recall@1（before→after） | recall@3（before→after） | 其他 |
|---|---|---|---|---|
| 正例 | 40 | 70.0 → **70.0**（零漂移 ✓） | 75.0 → 75.0 | 误拒 2.5% 不变 |
| 同义改写 | 25 | 56.0 → **60.0**（+4pp） | 64.0 → **72.0**（+8pp） | — |
| 跨商品混淆 | 15 | 60.0 → **60.0**（零漂移 ✓） | 80.0 → 80.0 | 混淆@1 60.0 不变 |
| 应拒答 | 16 | 拒答率 **100%**（不变） | — | — |
| 总体 | 96 | 63.7 → **65.0**（+1.3pp） | 72.5 → **75.0**（+2.5pp） | — |

**结论：保留接线**——每个分布相对 before 非降（正例/混淆组实证零漂移），同义组显著提升。

### 局限声明（审计刀 6 P1#5）

paraphrase 组 25 条改写问由**同表 16 组轮转生成**——after 提升部分来自表内闭包自测（轮转出的词被同表归一回原词），不证明对表外同义词的泛化。表外探针：语料内「邮费」类同义词命中为 0/461 块（「退」4/461），当前语料规模下表外泛化**无有效探针**——结论限定为「表内同义词组有效」，表外泛化留语料扩大后再测（WANDS 候选登记前必须重跑 before/after）。

### 替换式 vs 并集式过程数据（留档）

替换式：overall @1 60.0/-3.7pp、positive @1 65.0/-5pp、confusion @1 46.7/-13.3pp——负收益证据驱动改为并集；并集式全分布非降。

---

## After（第 41 刀商品可运营+目录可答，2026-09-10）

本刀检索侧零改动（`retrieval.py`/`answer.py` 一行未动；目录回落挂在
`chat_engine.py` 的 retrieve 调用之后、`compose_answer` 空命中分支之前——
`run_eval.py` 直调 retrieve+compose，不经过回落分支，数学上不可能漂移）。
同库（演示库，与上节同库）同种子大集跑两遍，stdout diff 为空：

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     70.0%     75.0%       -    2.5%       -
paraphrase     25     60.0%     72.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     65.0%     75.0%  100.0%    2.5%   60.0%
```

**结论：现有分布零漂移**——四个分布每格与第 36 刀 after 逐位一致（正例
70.0/75.0、同义 60.0/72.0、混淆 60.0/80.0、拒答 100%、总体 65.0/75.0）。
目录能力由 CI 回归集覆盖（`apps/api/evals/golden.json` 13→16 条：列举/
报价/miss 拒答，走 `run_ask` 真实引擎路径），不进大集（大集只读已发布，
目录回落读商品行——两个正交面，各自有集）。

---

## After（UX-A2 目录列举模板改短，2026-09-10）

本刀只改 `catalog_tools.render_listing`（列举回落模板）：不再逐条铺未定价商品，改为
「总数 + 已定价前 8 件 + 其余未定价请直接问商品名」。检索侧零改动（`retrieval.py`/
`answer.py` 未动）；`run_eval.py` 直调 retrieve+compose_answer、**不经过**
`try_catalog_answer` 回落分支（与第 41 刀同一正交面），故大集数学上不可能漂移。
同库同种子跑 before（main 代码）与 after 两遍，stdout **逐字相同**（`diff` 为空）：

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     70.0%     75.0%       -    2.5%       -
paraphrase     25     60.0%     72.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     65.0%     75.0%  100.0%    2.5%   60.0%
```

**结论：大集零漂移**——四分布每格与第 41 刀逐位一致（工件 `scripts/eval/out/eval-uxa2-{before,after}.txt`）。
**本刀真实回归保护来自纯函数单测与 DB 集成断言**（`test_product_operable.py`：只列已定价 /
8 条截断 / 全无价分支 / 全已定价不得谎称未定价；集成用例断言 content 含「已定价」且不含
「价格未定」）。`apps/api/evals/golden.json` 的 catalog 三例只断言**工具路由与摘要子串**
（`tool` 名 + `tool_summary_contains`），不比对 `answer.content`——模板回退成旧版也照样
通过，故**不构成对模板改动的回归网**（此前表述夸大，已订正）。演示库实测模板前后文本：

before（22 行，其中 18 行「价格未定」）：

```
本店在售商品共 115 件：
1. 瓶装水（食品）· 3元
2. 钛钢保温杯（器皿）· 129元
3. Trifolding phone（智能手机）· 价格未定
4. Vivo Y300（智能手机）· 价格未定
…
（仅列出前 20 件，共 115 件）
```

after（5 行，只列已定价）：

```
本店在售商品共 115 件，其中已定价 3 件：
1. 瓶装水（食品）· 3元
2. 钛钢保温杯（器皿）· 129元
3. 不锈钢保温壶（器皿）· 99元
其余未定价，直接问商品名。
```

工具式轨迹 `result` 保持「在售 115 件」不变（golden 断言 `tool_summary_contains: "在售"`）；
一件都没定价时改说「目前都没有公布价格」并请顾客直接问商品名（不再逐条写「价格未定」）。

---

## After（第 46 刀 video 正文源改由 transcript 字段承载，2026-09-11）

本刀改了检索侧一处：`index_chunks_for_version` 对 `kind='video'` **不再读对象字节**，正文
只从 `transcript` 字段取（confirmed 优先、回落 extracted；字段缺失/为空即正文为空）。原因是
切片登记字节从「时间码文本」变成**真 mp4 二进制**（ADR 0047），读回会进切块路径变乱码。
同库同种子复跑（本刀工作树）：

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     70.0%     75.0%       -    2.5%       -
paraphrase     25     60.0%     72.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     65.0%     75.0%  100.0%    2.5%   60.0%
```

**结论：大集零漂移**——与第 41/UX-A2 两节逐位一致。这次不是「碰巧没漂」而是**结构性**
的：大集 47 条引用资产经库内盘点**全是 document / dialogue / material**（无 video），
`video` 分支不在大集路径上。**本刀真实回归保护**是 `test_real_clips_integration.py::
test_published_video_chunks_come_from_transcript_field`（发布真 mp4 资产 → 块表内容 = 转写
原文、发布不因二进制报错）与既有 `test_clips_integration` 的「video 字段集 / PATCH 422」
断言——它们直接钉住「video 正文源是字段而不是字节」这条契约。

---

## After（第 50 刀 报价不看命中 + 演示价回填，2026-09-11）

本刀动了引擎的一处路由：`catalog_tools.try_price_answer`——**报价意图 + 命中商品名 +
该商品有价** 时直接答实时行价（不再要求 `retrieve == []`，ADR 0045 §二修订）；列举
仍走空命中闸，政策问/规格问/要真人仍被 `catalog_intent` 的四道闸挡住。另回填了
演示价（115 件全有价，此前 3 件）。

大集复跑（`run_eval.py` 直调 retrieve+compose，不经引擎路由分支）：

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     70.0%     75.0%       -    2.5%       -
paraphrase     25     60.0%     72.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     65.0%     75.0%  100.0%    2.5%   60.0%
```

**结论：大集零漂移**（与上一节逐位一致）——如第 41 刀所记，大集不过引擎的目录/报价
回落分支，**数学上不可能漂移**。**本刀的真实回归网是引擎级评测集**
`apps/api/evals/golden.json`（`test_eval_set.py` 真跑引擎）：三例目录用例
（`catalog-listing` 列举 / `catalog-quote-new-product` 报价 / `catalog-miss-refuse`
未命中仍拒答）全部通过。**注意口径**：这三例都不带检索命中（夹具里没有与问句
重叠的已发布块），走的是**老的空命中回落**——「有命中时也走行价」这条新路径由
`test_product_operable.py::test_price_question_uses_row_price_even_when_retrieval_hits`
钉（该用例先发布一份含商品名的资产并断言 `retrieve` 非空，再断言回答仍是行价）。

---

## After（第 58 刀 覆盖声明闸 + 演示库数据变更，2026-09-11）

**先说结论：本刀的代码没有动检索路径**（`run_eval.py` 直调 retrieve+compose_answer，
不经引擎；第 58 刀改的是引擎在生成之后的「模型自述证据未覆盖 -> 按拒答收口」判定）。
**但复跑基线掉了**：positive recall@1 **70.0% → 67.5%**、overall **65.0% → 63.7%**。

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     67.5%     75.0%       -    2.5%       -
paraphrase     25     60.0%     72.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     63.7%     75.0%  100.0%    2.5%   60.0%
```

**原因（已定位，属演示库数据变更，不是代码）**：审计刀 11 做了一次**治理性数据订正**——
asset 13（「钛钢保温杯 · 规格」）此前 v3 的内容与 asset 6 的退货政策互相矛盾
（15 天 vs 7 天），走「开修订 -> 换正文 -> 发布 v4」对齐后，**v3 的正文与块被 v4 取代**。
受影响的正是 `pos-017`（「该商品的净含量是500ml吗」，期望 `13/v3`）：现在 top-1 是
asset 3，且期望版本 v3 也已被 v4 取代（**期望本身过期**）。逐条比对正例 top-1（只读
直调 retrieve）：12 条未命中 @1（= 28/40 = 70.0%，与旧基线一致），runner 记 67.5%
（27/40）差的那一条是同分位次差异。

**结论**：①**大集基线随演示库数据变化**——任何走发布路径的内容改动都会移块、动分数，
本次是治理动作的副作用，如实记录；②**golden 里引用了已被取代的版本号**（`13/v3`），
这类「期望过期」以后应随发布一并核对；③本刀代码对评测无影响（不经过引擎）。

### 订正（第 60 刀，2026-09-11）：那 1.3pp 全部是**过期期望**，不是检索变化

第 60 刀盘点 golden 里「期望版本 ≠ 当前已发布版本」的条目，得 **2 条**（都是 asset 13：
`pos-008` 退货政策、`pos-017` 净含量），均因审计刀 11 的治理订正（v3 -> v4）而过期。
把两条的 `version_no` 指到**当前已发布版**（期望的资产不变，只是版本前移）后复跑：

```
golden：scripts\eval\out\golden_large.json（96 条）  检索 top-3
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1
positive       40     70.0%     75.0%       -    2.5%       -
paraphrase     25     60.0%     72.0%       -       -       -
confusion      15     60.0%     80.0%       -       -   60.0%
refusal        16         -         -  100.0%       -       -
overall        96     65.0%     75.0%  100.0%    2.5%   60.0%
```

**基线逐位恢复**（与本文档此前各节完全一致）——所以上一节那个 67.5% 不是检索回归、也不是
「演示库数据变化导致的分数漂移」，而是**过期期望**：runner 比对 `(asset_id, version_no)`，
版本对不上即记未命中。

**再订正（审计刀 12 实测）**：造成那 1.3pp 的是**其中一条**——`pos-008`（退货政策，top-1 = 13/v4，
改期望后翻盘）；`pos-017`（净含量）的未命中**与版本无关**：实测 top-1 是 asset 3、asset 13 排
rank 3（三者同分）。净额 +1 例（27/40 -> 28/40）与「只有 pos-008 翻盘」吻合。教训不变：
**发布类治理动作之后要顺手核对 golden 的版本期望**；另外「同分并列时的位次」本身是脆弱面，
golden 期望对同分场景没有稳定性保证。
