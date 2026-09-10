# 第 50 刀规格：多来源可见 + 演示价回填（intake + 裁决）

日期：2026-09-11。分支 `feat/source-price-50`（基于 main `0516680`）。依据：审计刀 8 记债 + 审计刀 9 复核（**连续四份 closeout 点名的两条产品面缺口**：「115 件只有 3 件有价」「四个真实数据源在 UI 里全叫『上传』」）。迁移 **0026**。探查实录见本文件「现状」节（全部为只读 SQL/命令回显）。

## 痛点（实测）

1. **真实数据在产品面不可见**：演示库有四份真实数据集，**四条灌入路径都没在库里记来源**——来源只活在脚本常量与 CSV 文件名里：
   - Wikidata 91 件商品（`scripts/realdata/fetch_wikidata_products.py`，落 `products`，6 个类目，`spec_schema={}`）；
   - OpenFoodFacts 20 件商品 + 20 份规格资产（`load_openfoodfacts.py`，资产标题 `{name} 规格（OFF）`，product 类目「食品」、`stock=12`）；
   - 在线购物评论 200 条（`load_reviews.py`，资产标题 `{类目}评论 · {前18字}`，走 `POST /api/assets/import-csv` → `source_kind='upload'`）；
   - WANDS 30 条切片候选（`load_wands_clips.py`，落 `clip_candidates`，`source_video_label='WANDS · wayfair 家具检索基准'`，另建承载商品「WANDS 家具（演示）」）。
   资产列表/详情**已经有「来源」列**（`sourceKindLabel`），但 232 条全是「上传」；`products` 表**没有来源列**。
2. **商品价只覆盖 3/115**：`SELECT category, count(*), count(price_cents) FROM products GROUP BY category` → 笔记本电脑 34/0、图书 19/0、电视机 16/0、智能手机 10/0、平板电脑 8/0、洗衣机 4/0、**家具 1/0**、食品 21/1、器皿 2/2。ADR 0045 只给了「食品 3 元 / 器皿 129 元」两个字面量（`services/seed.py`），roadmap 41 承诺的「按类目基准回填演示价」**没有落在导入商品上**——「多少钱」只对 3 件成立，目录回落（只列已定价前 8 件）实际只列得出 3 件。

## 裁决

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | `SOURCE_KINDS` 增两值：**`review_import`**（评论数据集导入）与 **`open_dataset`**（开放数据集：Wikidata / OpenFoodFacts） | 只加**两个**有界值，不按数据集建枚举（不是中台概念）；产品面需要的是「这不是我们传的」，不是「来自哪个 GitHub 仓库」。`source_kind` 是无 DB CHECK 的应用层枚举（ADR 0025），加值零 DDL |
| 2 | `products` 加 **`source_kind` 列（nullable）**，迁移 0026 回填：Wikidata 91（`spec_schema='{}'` 的六类目）+ OFF 20（类目食品且净含量单字段模板）+ WANDS 承载 1（`name='WANDS 家具（演示）'`）+ 两条种子 → `seed` | 商品侧要显示来源就必须有列（没有可用列）；可空 + 只回填存量，不放开给表单填（列表/抽屉只读展示） |
| 3 | 迁移 0026 按**稳定形态判据**回填资产来源：`title ~ '评论 ·'` → `review_import`（200 条）、`title LIKE '%规格（OFF）'` → `open_dataset`（20 条）；**只动 `source_kind='upload'` 的行** | 判据来自脚本写死的命名形态（不是猜测）；不动其它来源（幂等，可重跑） |
| 4 | **演示价按类目基准回填**（`price_cents IS NULL` 才写，不覆盖手改价——与第 41 刀同口径）：新增单一来源常量 `CATEGORY_DEMO_PRICES`（`services/seed.py`），迁移用同一组数字（快照，注释指向 seed） | 「目录问现在只列得出 3 件」是演示硬伤；数字是**演示价**（非真实售价），UI 与 README 如实标注（沿用第 41 刀「mock 演示数据」口径） |
| 5 | 基准价取值：食品 300 / 器皿 12900（既有）；**笔记本电脑 499900 / 智能手机 299900 / 平板电脑 199900 / 电视机 349900 / 洗衣机 219900 / 图书 5900 / 家具 89900**（分） | 与既有两件种子同量级、同类目一致；**是演示价不是定价建议**，写进常量注释与 README |
| 6 | 前端：`SOURCE_KIND_LABELS` 加两词；商品卡片与编辑抽屉显示**只读来源**；资产列表/详情**零改动**（已渲染 `sourceKindLabel`，新值自动出词） | 资产侧早就留好了展示位——本刀只补词；商品侧补一个 chip 与一行元数据 |
| 7 | 不再给 WANDS 切片候选加来源列：候选页已显示 `source_video_label`（「WANDS · wayfair 家具检索基准」），四份数据在**至少一个产品面**可见即达标 | 候选不是中台对象（第 18 刀），加列是为对齐而加列 |
| 8 | 商品来源**不可通过表单编辑**（POST/PATCH 不收该字段） | 来源是既成事实（谁灌进来的），不是运营可改的属性；可改就成了可造假的溯源 |

**回退点**：迁移 0026 的 downgrade 把两处回填的 `source_kind` 还原为 `upload`/NULL、价格回填的部分清回 NULL（只清本次写的那批：按「类目基准 + 非种子」判据）；代码侧删两个枚举值与前端两词即可。

## 验收

1. **资产来源**：`GET /api/assets` 里评论资产 `source_kind='review_import'`（200 条）、OFF 规格资产 `='open_dataset'`（20 条）；其它资产一条没动（`upload` 计数从 232 降到 12）。
2. **商品来源**：Wikidata 91 件 + OFF 20 件 + WANDS 承载商品 = `open_dataset`；两条种子 = `seed`；`ProductOut` 带该字段。
3. **演示价**：`SELECT count(*) FROM products WHERE price_cents IS NOT NULL` = **115**（原 3）；两条种子的价**未被改动**（300 / 12900）；手改价不被覆盖（迁移只写 NULL）。
4. **目录回落**：问「你们卖什么」→ 模板列出**已定价前 8 件**（不再是 3 件）+ 其余未定价提示消失（全店已定价）；问「笔记本电脑多少钱」→ 报价回落答出实时价。
5. **界面**：资产列表/详情来源列显示「评论导入」「开放数据集」；商品卡片与抽屉显示来源；无 `undefined`/裸枚举值露出。
6. **迁移可逆**：临时库 `upgrade head → downgrade 0025 → upgrade head` 往返通过；downgrade 后 `price_cents IS NOT NULL` 回到 3、两类来源回 `upload`/NULL。
7. **门禁**：后端全量绿（含既有 `test_seed_demo_prices_present` 与评测 golden）、ruff 净；前端 build 绿、lint 7/0；`docs/research/rag-eval-report.md` 复跑（大集不经目录回落，预期零漂移——如实记录）。
8. **端到端（真浏览器）**：商品页看到来源 chip 与价格；资产列表看到「评论导入」；顾客页问「你们卖什么」列 8 件带价商品。

## Out

- 抓取/刷新数据集（脚本仍是一次性导入，来源是回填的既成事实）、商品来源的可编辑、按来源筛选/统计、价格的真实性（一律演示价）、WANDS 候选加列、把四份数据的许可信息搬进产品面（留在 `scripts/realdata/README.md`）。


## 实现期增补（订正 + 一处新增）

- **订正验收 4**：原写「问『笔记本电脑多少钱』→ 报价回落答出实时价」**不成立**——①按**类目**问价不匹配商品名（`match_product` 匹配商品名，不是类目）；②更要紧的是，报价回落老口径要求 `retrieve == []`，而演示库 56 条已发布资产（471 个检索块）让**带商品名的问句常会命中**，于是所有问价都走 RAG 答「证据未覆盖价格」——**把 115 件价补齐了顾客仍问不出价**（实测：钛钢保温杯/ Vivo Y300 / Clicks Communicator 等全部如此）。修法：本刀追加一条**裁决 9**——`try_price_answer`（报价意图 + 命中商品名 + 有价 → 直接答行价，不看命中），并写进 ADR 0045 的修订段。列举仍走空命中闸；政策问/规格问/要真人不受影响（实测「退货运费多少钱」照旧走 RAG）。
- **种子商品来源**：迁移 0026 回填的是**存量**库；新建库的种子商品由 `seed.py` 直接写 `source_kind='seed'`（否则新库 NULL、老库 'seed'，同一份数据两种形态——正是本刀要消灭的不一致）。发现路径：`test_product_out_carries_source_kind` 在全新测试库上失败。
- **验收 4 的可用形态**：按**商品名**问价（「钛钢保温杯多少钱？」→「售价 129元」，工具式、citations 恒空）；「你们卖什么？」→ 8 件带价 + 「均已定价」。
