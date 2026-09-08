# Review · 工程第 6 刀：修订流 + 回滚

- 日期：2026-09-08
- 范围：`origin/main...HEAD`（`e617152` → `a470a1e` + 评审后小修）
- 规格：`.scratch/revision-rollback/spec.md`

## Standards

硬违规 0。无第四态、无 `in_flight_version_id`，未改 `prototype/`。

判断项（不阻断）：

- 去补仅当 `gap.product` 非空才开修订；拒答缺口 `product_id` 仍空（第 4 刀债务），0031 主路径需带商品的缺口或操作者选品。
- `_can_publish`（路由）与 `asset_view` 可发布判断略分叉（视图少指针检查）。
- 开修订曾同时收 Query 与 JSON；评审后只留 JSON body。

## Spec

Must 1–7 均落地。Out 干净。

判断项：

- 0031 只在控制台去补默认修订；API `register` 仍可对已有规格的商品再登记（spec 写的是去补路径）。
- 去补 `assets.find` 取该商品最新已发布 document，不一定是「规格」那一份。
- 二次开修订 409 前已 `put_bytes`，并发下可能留对象存储孤儿（与既有登记模式同类）。

## 评审后修复

- 已接入版本确认字段仍 409。
- 开修订 `knowledge_gap_id` 只走 JSON body。
- 审计/缺口模块注释补 rollback 与修订关缺口。
