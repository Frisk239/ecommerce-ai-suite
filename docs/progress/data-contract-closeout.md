# 工程第 33 刀 closeout：数据契约与生产级目录（feat/data-contract）

日期：2026-09-09。上刀 intake：`docs/progress/real-data-2-intake.md`。依据：`docs/roadmap.md` 第 33 行 + `docs/research/production-grade-catalog.md`。**dump→清洗→既有登记/发布，不手写规格种子。**

## 交付

1. **Open Food Facts 公开 TSV dump**（`scripts/realdata/load_openfoodfacts.py`）：流式解压 `en.openfoodfacts.org.products.csv.gz`，清洗要求条码+品名+可被机洗抽出的净含量；规格正文只用 dump 字段（不编造保质期）；schema 仅 `{净含量: required}`。`--load` 灌 products；`--register --publish` 走 register → 确认净含量 → 发布写回。许可 ODbL，署名在正文。
2. Wikidata SPARQL 补 P176/P2067；有属性才生成规格正文。
3. 口径：README 写明 pgvector 镜像未用；总览考核「按维打分」不是 AI 扮客。
4. `goal.md` §6.2：完成标准改为生产级目录（空 schema 名字不算）。

## Owner 验收

- 离线 fixture：5 行 dump → 2 条干净；机洗抽出 550ml，保质期弃权。
- 集成：登记 dump 原文 → 未确认 422 → 确认净含量发布 → `spec_values` 写回 → `retrieve` 命中。
- 实网：`--n 20` 流式 dump 5s 得 20 行；演示库 `inserted=20 register=20 publish=20`。
- Chrome 商品页：Chocolate n3 净含量 **80g** 写回 **A-0227 · v1**（同页 granola 450g / 柠檬果酱 230g）。

## 计数

- ruff：All checks passed
- 带 DB（5433）：`tests=595 passed=595 failed=0 skipped=0`（基线 583 → 595）

## 遗留

Wikidata 91 个真名字仍无规格值（SPARQL 属性覆盖稀疏）。Icecat/JDDC 仍观察。客服对「Chocolate n3 净含量」有时引用到邻近 OFF 资产但模板说未覆盖——检索命中与生成组装是第 35–36 刀评测/同义词的事，本刀目录写回已成立。

## 下一刀

第 34 刀 CI 门禁。
