# 第 68 刀 intake：引擎路径金标进 CI（评测尺加厚）

上一刀：复审审计（PR #99，审计清零达成）。Owner 裁决（2026-09-11）：按推荐顺序
「先把产品做到足够完善」，第 68 刀 = 评测尺加厚（地基）。

同批 Owner 裁决记录：**评分可改 = 允许**（后续刀实现）；**分币种类目报价 = 维持拒答**；
**工作队列默认视角 = 维持现状**。

## 问题：检索面金标看不见引擎路径

`golden_large.json` + `run_eval.py` 直调 `retrieve+compose_answer`，**不经引擎**——
历次审计抓的缺陷几乎都在引擎层：目录闸只装一条入口（审计刀 10 P0）、路由词表
层间缝（65 刀）、拼接检索挤掉本问主题（审计刀 13 P0-1）、评论证据适用域（13 P0-2）。
这些 bug 全靠浏览器验收与子代理对抗抓到，尺子事后才知道。

## 设计

- **数据**：`apps/api/evals/golden_engine.json`（加 case 以改 JSON 为主，与检索金标
  同约定）；23 例覆盖 catalog-price/purity/miss/listing、stock/purity、order、
  evidence-gate、rag、multi-turn 十个面——每个面至少一枚历刀真实缺陷的对位 case
  （eg-cat-002←62 刀别名、eg-stk-003←65 刀路由层、eg-evd-001←66 刀评论闸、
  eg-mt-001←13 刀 merge、eg-cat-006←13 刀花多少钱）。
- **runner**：`apps/api/tests/test_engine_eval.py` 直调 `run_ask` 真引擎断言
  **确定性产物**（kind/tool{arg}/citations/模板文案）——**进 CI**（检索金标是本地
  口径，这层是门禁）。LLM 依赖的生成路径不进本层（无 key 可复现，ADR 0027 既定
  取舍），那面仍由检索金标 + 忠实度闸钉。
- **种子**：module 级 fixture（商品三类带价带库存、两份文档、一条评论资产；
  SO-1001 用 conftest 种子）——形态要求写在 docstring，加 case 先对齐种子。

## 明确不做

- 不给 LLM 生成文案做金标断言（不稳定且 ADR 0027 禁止无 key 依赖）。
- 不动 run_eval.py（检索尺保持本地口径与历史可比性）。
