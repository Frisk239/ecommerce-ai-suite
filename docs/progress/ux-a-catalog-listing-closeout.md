# UX-A2 closeout：目录列举模板改短（后端，平行工作流，不占刀号）

日期：2026-09-10。分支 `feat/ux-a-catalog-listing`（基于 main `f34efc3`）。依据：`docs/ui-ux-plan.md` §四 UX-A.5 + §九（含 A5 评测证据方式的裁决）。

## 规格（本刀）

列举回落模板不再铺未定价商品，改为「总数 + 已定价前 8 件 + 其余未定价请直接问商品名」：

- `catalog_tools.MAX_LISTED` 20 → **8**，语义从「前 N 件」改为「**已定价**前 N 件」。
- `render_listing`：只列 `price_cents is not None` 的商品；一件都没定价时说「目前都没有公布价格」并请顾客直接问商品名；不再出现逐行「价格未定」。
- 不动的：`format_price`（quote 路径与单价展示仍用它）、`render_quote`、`catalog_intent` 四道闸、工具式轨迹 `result`（保持 `在售 N 件`，golden 断言依赖）。

## 为什么

第 41 刀实测：客服问「你们卖什么」，115 件商品打了 **22 行**，其中 **18 行「价格未定」**——在售规模与真正能行动的信息（有价的 3 件）被淹掉，顾客看不出能买什么。未定价商品对顾客没有可行动信息，改为请他直接问商品名（问价走报价回落，读实时行价）。

## 交付

| 文件 | 改动 |
| --- | --- |
| `apps/api/src/suite_api/services/catalog_tools.py` | `MAX_LISTED=8` + `render_listing` 重写（只列已定价、无价时改口径、未定价行不铺） |
| `apps/api/tests/test_product_operable.py` | 两条旧断言（钉死 20 条截断与逐行「价格未定」）重写为：只列已定价 + 8 条截断 + 未定价不进清单 + 无价时请直接问商品名 |

## 验收与证据

- **单测**：`SUITE_TEST_DATABASE_URL=…/suite_test uv run pytest apps/api/tests/test_product_operable.py apps/api/tests/test_eval_set.py` → **64 passed，0 failed，0 skipped**。本刀真实回归网 = 纯函数单测（只列已定价 / 8 条截断 / 全无价 / 全已定价不得谎称未定价 / 恰好 8 件无截断说明 / 空店人话）+ DB 集成断言（content 含「已定价」且不含「价格未定」）。**注意**：`apps/api/evals/golden.json` 的 catalog 三例只断言工具路由与 `tool_summary_contains`、不比对 `answer.content`，**不构成模板改动的回归保护**（此前 closeout 表述夸大，已订正）。
- **评测 before/after**（roadmap 排期纪律）：同库同种子大集跑 before（main 代码）/after 两遍，stdout **逐字相同**（工件 `scripts/eval/out/eval-uxa2-{before,after}.txt`）——大集不经过回落分支（与第 41 刀同一正交面），零漂移属构造必然，**不是模板正确性的证明**；真实改动面以模板前后文本 + 上述单测留证，已写入 `docs/research/rag-eval-report.md`。
- **模板实测**（演示库 115 件 / 3 件已定价）：

```
before（22 行，18 行「价格未定」）           after（5 行，只列已定价）
本店在售商品共 115 件：                     本店在售商品共 115 件，其中已定价 3 件：
1. 瓶装水（食品）· 3元                      1. 瓶装水（食品）· 3元
2. 钛钢保温杯（器皿）· 129元                2. 钛钢保温杯（器皿）· 129元
3. Trifolding phone（智能手机）· 价格未定   3. 不锈钢保温壶（器皿）· 99元
…                                         其余未定价，直接问商品名。
（仅列出前 20 件，共 115 件）
```

## 两轴评审与处置

两轴独立子代理，均判「修后可合」（无 P0）。已修：

- **P1（自审揪出的事实错误）**：收尾行「其余未定价，直接问商品名。」原为无条件追加——全店已定价时撒谎（店里没有未定价商品），截断时还把未列出的**已定价**件说成未定价。已按 `len(priced) < total` 条件化；全已定价且截断时改说「其余商品直接问名字」。补三条单测（全已定价 / 恰好 8 件无截断说明 / 空店人话），并把原断言由「无条件出现」改为「有未定价才出现」。
- **P1（验收归因夸大）**：`golden.json` 三例只断言工具路由与 `tool_summary_contains`、不比对 `answer.content`，不构成模板回归网。报告与 closeout 均已收窄表述，并补 DB 集成断言（content 含「已定价」且不含「价格未定」）作为真实保护。
- **P2**：`render_listing` 补空店人话 + 前置条件写进 docstring；模块 docstring 的 miss 归宿限定为「报价路径」；ADR 0045 追加 UX-A2 修订注记；删掉仓库根两枚 `--junitxml` 副产物。

记债（P2，未改）：`MAX_LISTED` 名字宽泛（语义已是「已定价前 N 件」，可改名 `MAX_PRICED_LISTED`）；`docs/ui-ux-plan.md` 的两处旧行号引用未逐条订正（该文件 §九 已登记本刀会修模板）。

## 已知取舍与披露

- 列举只报「已定价 3 件」的清单，**未定价商品从答案里消失**——顾客若只看到 3 件会以为店里只有 3 件；已用首行的「共 115 件，其中已定价 3 件」明确规模，并用「直接问商品名」给出下一条路径。
- 边界：已定价超过 8 件时截断并注明「仅列出前 8 件已定价商品」（防大店刷屏）。
- 后端无 LLM 参与（工具式模板，citations 恒空），本刀不引入新语义。

## 后续

- UX-E（视觉回 v3 去卡）须在第 43 刀仪表前完成。
- 本刀不改 roadmap 42–48 刀序。
