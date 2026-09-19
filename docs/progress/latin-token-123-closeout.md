# 第 123 刀 closeout：拉丁整词 token + Wikidata 规格名头行 + 字段行定向重排

分支 `feat/latin-word-token-123`。触发：生产级数据重灌（OFF/Wikidata/Commons/ABCD/Dell，`scripts/reseed_production.py`）后七站重验，第一站客服即翻车——「Nutella的净含量是多少」检索有命中却拒答。三层病根逐层剥出，全部修根。

## 病根与修法（三件）

### 1. 检索权重修根：拉丁词 bigram 膨胀（`services/retrieval.py`）

统一滑窗 bigram 把拉丁词切 O(len) 个单元：Nutella=6 个（Nu/ut/te/el/ll/la），中文属性词「净含量」=2 个。问「Nutella的净含量是多少」时品牌块词法分（1.604）恒压数值块「净含量：400g」（0.816），top-k 与 prompt 证据窗（各前 2）被标题/品牌块占满，模型只见标题块如实自述未覆盖 → 第 58 刀收口成拒答。中文合成数据时代（保温杯=2 bigram，与属性词对称）不暴露；拉丁品牌名的生产数据必炸。

修法：`_token_spans` 在**原始串**上扫描——ASCII 字母/数字段按整词一个 token（小写归一；字母↔数字边界切分 so1002→{so,1002}；空格/逗号天然断词，先 normalize 会把「Nutella, Ferrero」拼成巨型 token）。CJK 段照旧跨标点 bigram（逐 token 等价）。顺带修了旧 bigram 的大小写敏感洞（nutella≠Nu）。**索引无需重建**（打分实时算）。

OOV 的「最长连续零出现串」从 bigram 位扫改为 token 字符位扫（CJK 逐位等价：雀巢咖啡→4；拉丁词整段计长：Nutella→7），实体串回切原始串保留大小写。

### 2. 数据断层修根：Wikidata 规格正文补商品名头行（`scripts/realdata/publish_digital_specs.py`）

Wikidata 规格正文只有翻译值（品牌：森海塞尔），与拉丁商品名零词法交集——旧评测的「正确命中」实为 800↔2009 共享 bigram「00」的**假阳性**（min-max 归一放大到 1.0）。`spec_text` 补「{name} 规格」头行（OFF 先例形态），名字即实体锚。

### 3. 证据窗收口：字段行定向重排（`_field_directed`）

前两件后实测仍两个真实问句拒答：「Sennheiser HD 800 是哪年上市的」「Coca-Cola 可乐的配料有什么」——数值行词法分天然低（长值串撑大 sqrt(块单元) 分母），证据窗被同资产名头行/图片描述与兄弟商品头行占满。

- **先试打分乘数（×10）被否决**：同品牌兄弟商品的短字段块（品牌：LU）被抬到第 1，跨资产次序打乱，评测 84.1→79.7（-4.4pp，三条同形态全坏）。
- **定稿为零分值改动的保序重排**：问句点名了字段时，top-1 资产自己的该字段块提到最前，跨资产次序与所有分数不动。资产级 recall/MRR 与重排前逐位一致，prompt/引用（各前 2）看见数值行。

## 评测（生产重灌库，golden 89 条 = `scripts/eval/out/123-golden-production.json`，旧分词生成保持基线口径）

| 配置 | overall@1 | positive@1 | paraphrase@1 | 拒答率 | 误拒率 |
|---|---|---|---|---|---|
| 旧统一 bigram（before） | 88.4% | 100% | 100% | 100% | 0% |
| 新分词（after） | 84.1% | 95% | 100% | 100% | 0% |

-4.3pp 全部逐条定位（`123-cases2-*.json` 逐条 dump）：pos-011/pos-029「X 商品图怎么样」（生成器从图片资产标题造题）下规格文档名头行块以微弱词法优势压过图片描述块——**同商品跨资产引用翻转**，两引用都指向同一商品，顾客语义无差别；conf-010「来自怎么样」是纯噪声彩票（问句本身即生成器垃圾，新旧 top3 全是 1.0 平票）。演示库 golden（230 条，`golden_large.json`，保温杯语料）已恢复原样入库不受影响——该基线属于演示语料时代，生产 golden 另存。

## 端到端实测（顾客 SSE 通道，生产库）

| 问句 | 结果 |
|---|---|
| Nutella的净含量是多少 | ✓ 答案 400 g，引 A-2 |
| Sennheiser HD 800 是哪年上市的 | ✓ 答案 2009 年，引 A-15（重排后单锚，不再引兄弟 A-16） |
| Coca-Cola 可乐的配料有什么 | ✓ 配料表（西语成分由模型转写中文），引 A-6 |
| 保温杯的材质是什么 | ✓ 钛钢/316 不锈钢，引 A-27 |
| 森海塞尔的耳机是什么类目 | ✓ 耳机，引 A-15 |
| 星巴克咖啡的配料是什么 | ✓ OOV 正确拒答 + 工单 H-0003（「本店暂时没有这款商品」） |

前三条在本刀之前全部拒答。

## 重灌链自包含（`scripts/reseed_production.py`）

- wikidata 步骤并入（fetch --digital-only --load + publish_digital_specs，各自幂等；此前带外跑，重置即丢）
- OFF 步骤资产级幂等（(title, product_id) 锚——商品级同名跳过挡不住半路失败重跑的双份资产，实测 A-1/A-3 双份的根因）
- 对话步骤 `--register` 开关用法修正（写成 `--register 5` → unrecognized arguments，对话静默没灌进去）；demo_verify 补 --db

## 测试

新增钉子：`test_query_terms_latin_word_is_single_token`、`test_score_chunk_attribute_value_outranks_brand_title`、`test_oov_verdict_latin_entity`、`test_field_line_named_shape_gate`、`test_field_directed_promotes_top_asset_value_chunk`；`test_realdata_scripts` 的 spec_text 钉子更新（名头行）。全量 **1709 passed**。

## 记债

- 兄弟商品字段块进引用第二锚（Lu 两条目的品牌互引）——证据多样性（同品牌 SKU 消歧）是 rerank 级课题，无评测数字不上
- conf-010 类噪声问句的 min-max 平票序不稳定——生成器问题不是检索问题
- 生产 golden 无 oov_syn/sem_neg 探针（依赖演示库会话采集）——生产语料跑不出第五/六分布，观察口径暂以演示库 golden 为准
