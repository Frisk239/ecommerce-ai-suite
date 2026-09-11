# 第 74 刀 intake：空 schema 治理激活（roadmap 3.1 老债）

上一刀：第 73 刀多轮工具追问（PR #106）。roadmap 3.1：91 件 Wikidata 商品
`spec_schema={}`——0010 写回 / 0019 必填闸门在这批商品上是死的，治理路径
（登记挂商品→机洗→确认→发布写回）在真名字上走不通。

## 现状核因

- 导入脚本 `fetch_wikidata_products.py` 的 `_schema_for`（类目模板）与空 schema
  回填（reconcile）**早已实现且有测试**——演示库的 92 条空 schema（91 wikidata +
  1 wands）是**修复前灌入的存量**，重跑导入即可回填，但 SPARQL 网络不必再拉：
  模板来源 `schema_for_category` 是单一真源，就地回填等价。

## 本刀（演示数据刀 + 验证）

1. **就地回填**：92/92 空 schema 商品按类目模板回填（`schema_for_category` 单一
   真源，与导入脚本 reconcile 同一条规则）。
2. **治理全链在真名字上走通**（Vivo Y300）：登记（挂商品 productId）→ 机洗对
   品牌/存储容量**弃权**（设计内：0010 无验证抽取器的字段不冒充，FIELD_EXTRACTORS
   只有 净含量/保质期/材质）→ 人洗确认 → 发布 → **spec_values 写回商品行**
   （含 source 溯源 {version, asset_id}）。
3. **顾客面验证**：「Vivo Y300 是什么品牌？」→ 带引用作答。
