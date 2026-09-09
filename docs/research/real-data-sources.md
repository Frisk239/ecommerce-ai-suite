# 公开可用的电商数据源调研（多来源设计弹药）

日期：2026-09-09。调研途径：公网检索（子代理三路调研之路 A）。用途：为「多来源真实数据」设计供弹药——本仓库六种来源通道（上传/会话回流/切片拣选/素材生成/连接层登记/种子）全部已建，但从未灌过真实第三方数据。本文回答「每个通道能灌什么真实数据、许可是否允许」。

结论先讲：**四源组合拳可以零法律负担讲通「多来源」故事**——Wikidata（CC0 商品种子）→ 中文电商评论集（CSV 批量）→ WANDS（MIT 切片候选+相关性标注）→ JDDC/ABCD（会话回流模拟）；直播侧挂 Watch and Buy / Live-Aid 作扩展位。

---

## 1. 直播电商数据（弹幕/回放/对话）

| 资源 | 规模与字段 | 许可 | 适配通道 |
|---|---|---|---|
| [Live-Aid](https://aclanthology.org/2026.findings-acl.1193/)（ACL 2026 Findings，中文） | 1,763 场直播/1,100+ 小时、44 电商类目、8 万对话轮、**343.6K 条弹幕**，弹幕与主播回应时间对齐 | 学术用途（论文附下载） | 弹幕→切片候选种子（带时间戳语料）；主播话术→会话回流模拟 |
| [Watch and Buy（淘宝直播×天池）](https://tianchi.aliyun.com/forum/post/283791)（[OpenDataLab 镜像](https://opendatalab.com/OpenDataLab/%E6%B7%98%E5%AE%9D%E7%9B%B4%E6%92%AD%E5%A4%9A%E6%A8%A1%E6%80%81%E8%A7%86%E9%A2%91%E5%95%86%E5%93%81%E6%A3%80%E7%B4%A2%E6%95%B0%E6%8D%AE%E9%9B%86)） | **70,000 直播视频片段×商品对**，业界最大直播多模态检索集 | 天池学术协议（可演示，注明出处） | 切片候选种子（视频+商品绑定，天然贴合「切片拣选」） |
| [LSEC](https://opendatalab.com/OpenDataLab/LSEC) | 主播讲解商品+观众互动的交易型对话（Small/Large 两档） | OpenDataLab 学术协议 | 会话回流模拟 |
| [LiveRec](https://github.com/JRappaz/liverec)（RecSys 2021） | 直播推荐交互流（用户×直播间×观看时长，动态上下架） | 研究用途，GitHub 直下 | 用户行为流→会话回流模拟 |
| [LiveBot](https://github.com/lancopku/livebot) / [LiveChat](https://github.com/gaojingsheng/LiveChat)（ACL 2023） | LiveChat：**1.33M 中文直播对话**、351 主播画像 | GitHub 开放，研究用途 | 弹幕/话术→切片候选种子 |

补充：抖音/淘宝弹幕**无官方公开集**，只有采集工具（如 [DouyinBarrageGrab](https://github.com/ape-byte/DouyinBarrageGrab)），违反平台 ToS，不入仓库演示。

## 2. 电商评论数据

| 资源 | 规模与字段 | 许可 | 适配通道 |
|---|---|---|---|
| [online_shopping_10_cats](https://github.com/SophonPlus/ChineseNlpCorpus/blob/master/datasets/online_shopping_10_cats/intro.ipynb)（ChineseNlpCorpus） | **6.2 万条中文评论**，10 类目，正/负情感标注 | GitHub 开放语料库，研究用途 | **CSV 批量上传**（zip 直下，字段 cat/label/review，改列名即灌） |
| [DAMO_NLP/jd](https://modelscope.cn/datasets/DAMO_NLP/jd)（ModelScope 京东） | 52 万商品、**720 万条评论+评分**，带类目层级 | ModelScope 社区协议（研究） | CSV 批量上传（抽样） |
| [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/) | 5.7 亿条，含用户/商品/时间戳/结构化属性 | 研究使用（非商业），需引用 | CSV 批量上传（抽样做英文侧） |

## 3. 电商客服/问答对话

| 资源 | 规模与字段 | 许可 | 适配通道 |
|---|---|---|---|
| [JDDC / JDDC 2.0](https://jddc.jd.com/)（京东，[论文](https://aclanthology.org/2020.lrec-1.58.pdf)） | **100 万多轮客服对话、2,000 万句、1.5 亿词**，附 FAQ 知识库+商品属性 | 注册+同意协议，研究用途 | **会话回流模拟**（session_id 分轮，天然回流格式）+ FAQ→切片种子 |
| [E-commerce Dialogue Corpus](https://github.com/cooelf/DeepUtteranceAggregation)（淘宝来源） | 100 万 utterance，多轮检索式对话 | 研究用途 | 会话回流模拟 |
| [ABCD](https://github.com/asappresearch/abcd)（英文，ASAPP） | 1 万+ 人人客服对话、55 种用户意图、动作序列标注 | **MIT（可入仓库）** | 会话回流模拟（售后意图标签直接驱动演示） |
| [SalesBot](https://github.com/miulab/salesbot)（[ACL 2022](https://aclanthology.org/2022.acl-long.425/)） | 闲聊→任务型销售对话过渡 | 未标明 LICENSE，研究用途 | 售前导购→会话回流模拟 |

## 4. 商品目录/规格数据

| 资源 | 规模与字段 | 许可 | 适配通道 |
|---|---|---|---|
| [Open Food Facts](https://world.openfoodfacts.org/data) | **300+ 万食品条目**：品名/品牌/配料/营养成分/条码，CSV 全量+免 key API | **ODbL**（可演示需注明） | **连接层登记**（真实 API 实时拉取——「外部系统经 MCP/HTTP 登记进中台」的最佳演示素材）+商品种子 |
| [Wikidata](https://www.wikidata.org/wiki/Wikidata:Database_download) | 商品类条目（品牌/型号/类目/GTIN），SPARQL 按需过滤 | **CC0（最宽松）** | 商品种子（SPARQL 导 CSV） |
| [Open Icecat](https://icecat.com/content-subscription/) | 免费层 200 万+ 商品数据表、600+ 品牌、70+ 语言 | Icecat 开放内容许可（免费注册） | 商品种子（3C 规格结构化，中文覆盖） |

## 5. 电商搜索 Query（ESCI 之外的补充）

| 资源 | 规模与字段 | 许可 | 适配通道 |
|---|---|---|---|
| [WANDS](https://github.com/wayfair/WANDS)（Wayfair） | **480 query × 43K 商品 × 233K 三档相关性标注**（Exact/Partial/Irrelevant）；product.csv 含类目/属性/评分 | **MIT（可入仓库，保留 LICENSE+引用）** | query→切片候选种子；**标注对→切片拣选/检索评测 golden set** |
| [Taobao UserBehavior](https://tianchi.aliyun.com/dataset/649?lang=en-us)（天池） | **1 亿条用户行为**（点击/收藏/加购/购买，带时间戳） | 天池协议（研究） | 会话回流模拟（行为序列） |
| [OTTO](https://github.com/otto-de/recsys-dataset) | 1,200 万 session、2.2 亿事件 | 研究用途 | 会话回流模拟 |

## 6. 抓取合规路径

- **[Shopify Storefront GraphQL](https://shopify.dev/docs/api/storefront/latest)**：需商店 public access token（dev store 免费 5 分钟可建）；部分 collection 数据在 unauthenticated 下受限（[scopes](https://shopify.dev/docs/api/usage/access-scopes)）。
- **[淘宝开放平台](https://open.taobao.com/)**：上架须企业资质+聚石塔+注册资本≥50 万+软著——**不适合演示，文档里作为「高门槛通道」说明即可**。
- **免 key 友好源**：Open Food Facts API、Wikidata SPARQL、HuggingFace Hub——连接层登记的合规素材。

## 推荐组合拳与落地排期

**故事线一句话：开放知识库（Wikidata CC0）→ 学术评论集（中文电商）→ 行业基准（WANDS MIT）→ 平台真实客服（JDDC/ABCD），四个来源、四种通道、全部可合规入仓库演示。**

落地排期（进 `docs/optimization-plan.md` 第二阶段）：

| 刀 | 内容 | 通道 |
| --- | --- | --- |
| 多来源数据刀 I（商品+评论） | Wikidata SPARQL 导 200 商品种子；online_shopping_10_cats 抽样 2000 条转 (title,content) CSV 走批量导入→机洗→发布→可检索 | 商品种子+CSV 批量 |
| 多来源数据刀 II（对话+切片） | JDDC/ABCD 按 session 切分灌会话回流模拟（登记为对话资产→QA 草稿→人洗）；WANDS query+标注转切片候选种子 | 会话回流+切片拣选 |
| 连接层真实登记演示（可选） | Open Food Facts API 免 key 拉取→MCP/HTTP 登记进中台 | 连接层登记 |
