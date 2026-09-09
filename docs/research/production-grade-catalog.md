# 生产级目录数据：现状审计与可灌源

日期：2026-09-09。起因：Owner 问「有没有接入相对生产级的真实/mock 数据；太简单无法证明含金量」。对照仓库实况 + 公开源（Open Food Facts / Icecat / Wikidata 属性 / Amazon 2023 meta / JDDC）。

结论先讲：**通道灌过真字节，目录不是生产级。** 第 31–32 刀证明「六种来源能吃第三方数据」；面试问「规格从哪来、写回什么、为什么不是两 SKU 玩具」时，当前数据会穿。缺的不是再灌 200 个空名字，是**带属性的商品主数据 + 和商品挂上的知识**。

---

## 1. 仓库里实际有什么

| 层 | 规模 | 是不是生产级 | 一句话 |
|---|---|---|---|
| compose 种子 | 2 商品、3 订单、4 条切片转写 | 否 | 路径钉测用，不能当目录故事 |
| Wikidata `--load` | 91 行真商品名 | **否** | `out/products.csv` 实测 `spec_schema={}`、`spec_values={}`。有「Vivo X300 Pro」这种真名字，**没有品牌/容量/GTIN/净含量** |
| 中文评论 CSV | 200 导入 / 20 发布 | 半 | 真中文电商评论；**不挂商品**、无规格字段；标题是「平板评论 · …」 |
| ABCD | 英文客服会话回流 | 半 | MIT 真对话，通道对；语言/域是英美客服，不是中文电商售后 |
| WANDS 切片 | Exact 对合成转写 | **否** | `顾客问 marble —— mcreynolds kitchen island marble（标注：Exact）`——检索基准，不是直播转写 |
| `data/seed/`（33 刀半成品） | 3 个短 txt | 否 | 保温杯/退货/手机各一段，仍是样例 |

所以：有「真来源」的证据，没有「真目录」。含金量卡在 **PIM（商品主数据）**，不在通道个数。

---

## 2. 生产级在这个仓库里指什么

不做淘宝后台、不做多租户。生产级 = 面试官能对着中台问下面这些，代码和数据对得上：

1. 商品有 **类目字段 + 真值**（品牌、净含量/容量、条码），不是空 schema。
2. 规格文档是 **从真实主数据生成或登记的**，发布后写回 `spec_values`，检索能命中。
3. 评论/对话 **能挂到商品或类目**，不是一锅orphan 文档。
4. 订单/库存仍可 mock，但 SKU 应能对上目录里的名字。
5. 许可可指、可复现脚本，不靠脏 volume。

达不到的也不装：真 OMS、真直播 ASR、京东全量 JDDC（要注册授权）。

---

## 3. 可灌的生产级源（按性价比）

### A. Open Food Facts（首选，食品目录）

- 免 key JSON API：`/api/v2/product/{barcode}`、`/api/v2/search`。
- 字段直接对 0019：`product_name`、`brands`、`quantity`（净含量）、`categories`、`ingredients_text`、营养/过敏原。
- 许可：**ODbL**（注明来源、衍生库同样开放即可；适合演示仓库）。
- 规模：全球 300 万+ 食品；可按 `countries_tags=en:china` 抽中文货架。
- 通道：种子灌 `products`（schema=食品：净含量/保质期）+ 把标签正文登记为规格文档 → 人洗确认 → 发布写回。这是 **0010 在真商品上走通** 的最短路径。
- 同族：Open Products Facts（非食品条码库），API 形状相同。

### B. 加深 Wikidata（3C 名字变成有属性的条目）

当前 SPARQL 只取 `?itemLabel`。公开属性就够用：

| 属性 | 含义 | 可写回字段 |
|---|---|---|
| P176 | manufacturer | 品牌 |
| P2067 | mass | 净含量/重量 |
| P2048 | height | 尺寸 |
| P3962 / P528 | GTIN / 型号 | 条码/型号 |

CC0，已有脚本，改查询即可。覆盖率不如 OFF 密，但能救「91 个空手机名」。

### C. Open Icecat（3C 规格最像电商 PIM）

- 品牌授权 datasheet：标题、结构化规格、GTIN、多语言（含中文）。
- 免费层要 **注册账号** + Open Content License + 署名；公平使用限制月下载量。
- 适合「消费电子规格表」故事；个人项目要账号，排在 OFF/Wikidata 属性之后。

### D. Amazon Reviews 2023 item metadata（英文目录+卖点）

- `title` + `features[]` + `description` + 类目；评论可挂 `parent_asin`。
- 研究用途（McAuley / 引用）。体量大，必须抽样。
- 比现在的评论强在 **评论能对上商品元数据**。中文域仍弱。

### E. JDDC / JDDC 2.0（中文真客服）

- 京东真实多轮：24.6 万场、附商品知识库（3 万 entity、759 关系）。
- **注册+授权，禁止商用。** 拿得到就是答辩最硬的对话；拿不到就维持 ABCD，不要假装。

---

## 4. 不要做的

- 再灌 Wikidata/WANDS **空 schema** 行。
- 爬淘宝/抖音（ToS）。
- Icecat 全量、Amazon 全量当演示库（体积/许可）。
- 把评论当规格文档发布却不挂商品——现在 20 条就是这个形状，检索能命中句子，讲不出写回。

---

## 5. 别人怎么做（不要手写种子）

手写 `data/seed/*.txt`、把 API 字段编成「保质期：见包装」**不是生产级**。业界和参考仓是 **dump/feed → 清洗 → 灌知识库**：

| 谁 | 做法 |
|---|---|
| **Open Food Facts 自己** | 夜更 **CSV/JSONL/Parquet dump**（CSV gzip ~0.9GB / 未压 ~9GB）：[static.openfoodfacts.org/data](https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz)。字段表 [data-fields.txt](https://world.openfoodfacts.org/data/data-fields.txt)。DuckDB/Parquet 抽样，不是直播搜 40 条 API。 |
| **Open Icecat** | 免费账号后：`files.index.csv.gz` 全量索引 → 按品牌/类目过滤 → 下 XML datasheet → 解析成 JSON（[icecat-harvester](https://github.com/alexander-marquardt/icecat-harvester) 就是 Extract/Transform 两步，源 XML 留盘可复跑）。 |
| **Enthusiast（本仓 reference）** | Shopify Admin API **同步商家真目录**，不是假 SKU。 |
| **ai-customer-service-agent** | `knowledge_base/` 真文档 + `ingest_docs.py`：load → chunk → 索引。目录仍薄，但灌的是文档不是生成器。 |
| **PIM（UnoPim / 电商 RAG 文）** | 供应商 feed 进 PIM：规范化属性 → 质量闸（必填/条码）→ 才发布。SKU 与规格绑定成一块，不把 HTML 整页丢进向量库。 |

对本仓应对齐的流水线（已有：登记 → 机洗 → 人洗 → 发布）：

```
OFF CSV dump（或 Icecat XML）抽样
  → 清洗：有条码、有品名、有净含量/品牌；中文优先；空行丢弃
  → 每 SKU 用 dump 里的真字段拼规格字节（quantity/brands/ingredients 原文，不编造保质期）
  → register_asset 走既有通道
  → 机洗抽得到的确认，抽不到的弃权（0009），不把「见包装」冒充源数据
  → 发布写回 spec_values（0010）
```

第 33 刀 Must 按这条，**禁止**再手写保温杯/手机 txt 当主数据。

---

## 6. 建议落到哪一刀

1. **下载 OFF 公开 dump（CSV.gz）**，DuckDB/标准库抽 50–200 条：`code,product_name,brands,quantity` 齐。
2. 清洗后灌 `products` + 规格文档（字节=dump 列拼出的真标签文本）→ 发布写回测试。
3. Wikidata 仅作 3C 补属性（P176/P2067），有值才生成规格正文。
4. Icecat（要账号）/ JDDC（要授权）观察项。

来源：

- [Open Food Facts API](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api) · [ODbL](https://opendatacommons.org/licenses/odbl/)
- [Wikidata P176](https://www.wikidata.org/wiki/Property:P176) · [P2067](https://www.wikidata.org/wiki/Property:P2067)
- [Open Icecat](https://icecat.com/structured-data-content-users/) · [Open Content License](https://iceclog.com/open-content-license/)
- [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/)
- [JDDC 2.0](https://arxiv.org/abs/2109.12913) · 下载 [jddc.jd.com](https://jddc.jd.com)（须授权）
