# RAG 准确性工程调研：证据、护栏与方案矩阵

日期：2026-09-09。调研途径：公网检索（三路调研之路 C）。用途：回答三个面试级拷问——①「纯词法检索保修检得到质保吗」②「有实测数据证明检索质量吗」③「怎么保证模型真的按证据回答不胡编」——每个都给业界证据与解决方案。

先回答事实底座（本仓库现状，非调研）：**切块数据是真的存直属库的**——`retrieval_chunks` 表（Postgres），发布事务内切块写入（对话按轮/文本按句/QA 对成块，上限 200 块），检索只 join 当前已发布指针；发布后块可查、A-0011 切片资产发布后即被检索命中（走查实证）。评测集 golden.json 13 条在 CI 全量跑。

---

## 1. 词法 vs 语义 vs 混合检索的实证

- **BEIR**（NeurIPS'21）：BM25 在 18 个 zero-shot 数据集上是稳健基线，平均优于早期 dense 模型；现代 embedding 已在多数集反超，但 **BM25 在领域术语/罕见词语料上仍最强**（[arXiv 2104.08663](https://arxiv.org/abs/2104.08663)、[repo](https://github.com/beir-cellar/beir)）。
- **Hybrid（BM25+向量 RRF 融合）实测**：调优后 NDCG 0.7497，比单路高约 +7.4%（[基准](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026)）；recall@10 约 91% vs 纯 dense 78%（[supermemory](https://supermemory.ai/blog/hybrid-search-guide/)）；RRF 公式 `Σ 1/(k+rank)`（k≈60）免分数归一化（[Vespa](https://docs.vespa.ai/en/learn/tutorials/hybrid-search.html)）。

**对本仓库**：语料封闭、术语规范（SKU/政策文档）正处 BM25 优势区。面试话术：「按 BEIR 结论，封闭域+专有名词场景词法是合理基线，且我用评测集验证过边界」。同义词缺口是 vocabulary mismatch 不是 bigram 的锅（见 §7 零向量解法）。

## 2. 中文分词对词法检索的影响（字符 bigram）

- **Elasticsearch/Lucene 官方 CJK 处理正是 bigram**：`cjk_bigram` token filter 是 ES 内置 CJK analyzer 核心（[ES 文档](https://www.elastic.co/docs/reference/text-analysis/analysis-cjk-bigram-tokenfilter)、[Lucene CJK](https://lucenenet.apache.org/docs/4.8.0-beta00017/api/analysis-common/Lucene.Net.Analysis.Cjk.html)）。
- **jieba 路线**：阿里云 AnalyticDB 等生产系统用 jieba+自定义词典（[文档](https://www.alibabacloud.com/help/doc-detail/2844024.html)）；jieba 弱在 OOV 切错，bigram 免词典天然支持部分匹配。

**对本仓库**：自研字符 bigram 与 Lucene CJK analyzer 同构——**工业界验证过的标准做法而非玩具**，面试可直接引 ES 文档背书。

## 3. 评测协议（回应「没有实测数据」）

- **RAGAS 四指标**：faithfulness / answer relevancy / context precision / context recall，LLM-as-judge（[官方](https://docs.ragas.io/en/v0.1.21/concepts/metrics/)）；坑：分数非确定、judge 有偏、衡量「管线自洽」而非「对用户正确」（[Atlan](https://atlan.com/know/how-to-evaluate-rag-systems-explained/)），要按组合读。
- **工具选型**：TruLens RAG Triad（产线观测，[trulens.org](https://www.trulens.org/getting_started/core_concepts/rag_triad/)）；DeepEval（pytest 原生可进 CI，[deepeval.com](https://deepeval.com/guides/guides-rag-triad)）；ARES（150 条标注+微调 judge，[arXiv 2311.09476](https://arxiv.org/abs/2311.09476)）。
- **小团队 golden set**：50–200 条，从真实失败/产线 trace 抽取，LLM 造 silver 人工精修 gold（[Microsoft](https://medium.com/data-science-at-microsoft/the-path-to-a-golden-dataset-or-how-to-evaluate-your-rag-045e23d1f13f)、[Langfuse](https://langfuse.com/resources/engineering/golden-dataset-evaluation)、[Mistral RAG judge](https://mistral.ai/news/llm-as-rag-judge/)）。

**对本仓库**：最高优先级不是换算法是建评测集。问题分布：正例/**同义改写（保修→质保，专戳软肋）**/跨商品混淆（验已发布过滤）/应拒答组。context recall/precision 单测检索层，faithfulness 测生成层，**拒答率单列**。

## 4. 忠实度/防幻觉护栏（回应「怎么保证不胡编」）

- **Cohere Command R 原生 citations**：训练针对 grounding，答案带 inline 引用 span（[文档](https://docs.cohere.com/docs/command-r)）。
- **Anthropic Citations API**：结构化逐句/claim 级溯源 span，明确面向可靠 RAG（[评析](https://simonwillison.net/2025/Jan/24/anthropics-new-citations-api/)、[Anthropic](https://www.anthropic.com/news/contextual-retrieval)）。
- **拒答阈值调优**：必须同时量假阳性（过度拒答）与假阴性，本身需要 golden set；**refusal rate 应作为一等指标与幻觉率并列**（[Doerfer 指南](https://mbrenndoerfer.com/writing/hallucination-mitigation)、[guarded RAG](https://papers.ssrn.com/sol3/Delivery.cfm/6702638.pdf?abstractid=6702638&mirid=1)）。

**对本仓库**：已有「无证据拒答+引用版本锚定」，升级方向是**逐句引用**（prompt 约束每句标 chunk id）+ 在评测集上画「拒答阈值-幻觉率-过度拒答率」三曲线选工作点。

## 5. Chunking 实证（我们的按轮/按句/QA 成块）

- **Anthropic contextual retrieval**：切块后 LLM 给每 chunk 生成上下文描述再索引，检索失败率降 **49%**，叠加 rerank 降 **67%**（[工程博客](https://www.anthropic.com/engineering/contextual-retrieval)）。
- **LlamaIndex chunk size 实验**：512 token 默认甜点，最优值依语料漂移；小 chunk 提精度大 chunk 保召回（[博客](https://www.llamaindex.ai/blog/evaluating-the-ideal-chunk-size-for-a-rag-system-using-llamaindex-6207e5d3fec5)）。

**对本仓库**：结论支持结构化切块——**QA 对成块的 Q 就是天然 context**（Anthropic 方案的廉价替代）。可再借一招：索引时给 chunk 附加商品名/类目元数据前缀（手动版 contextual retrieval，纯规则）。

## 6. Rerank 的性价比

- cross-encoder rerank 通常白拿约 **5–7 个 NDCG 点**，是 RAG 栈单步最大质量提升（[指南](https://localaimaster.com/blog/reranking-cross-encoders-guide)）；只对 top-k（20–100）打分延迟可控；bge-reranker Apache 2.0 可自托管（[对比](https://futureagi.com/blog/best-rerankers-for-rag-2026/)）；Cohere Rerank 纯 API 零部署。
- **两段式（bigram 召回 top-50 → rerank 精排 top-3）**是业界标准架构，能救回「保修↔质保」语义近邻——需引入 rerank 级小模型但**免向量库**。

## 7. 同义词扩展（零向量解法，直击问题①）

- **Elasticsearch synonym/synonym_graph filter** 是 vocabulary mismatch 的工业标准解：search-time 应用、免重建索引（[官方](https://www.elastic.co/docs/reference/text-analysis/analysis-synonym-tokenfilter)）。
- 在 bigram 打分前对 query/索引做同义词归一（保修=质保=三包），几十行代码，评测集「同义改写组」直接量化收益。**零 embedding 依赖。**

## RAG 准确性方案矩阵（防戳穿价值 × 成本）

| 方案 | 防戳穿价值 | 成本 | 依赖 |
|---|---|---|---|
| **1. golden set 评测报告**（50–200 条四分布，RAGAS 式指标+拒答率） | ★★★★★ 直接回应「无实测」，是一切换动的度量尺 | 低 | 零 embedding |
| **2. 同义词扩展**（保修↔质保↔三包，search-time 归一） | ★★★★★ 直击同义词拷问 | 极低 | 零 embedding |
| **3. 逐句引用+拒答阈值曲线**（每句标 chunk id；三曲线选工作点） | ★★★★☆ 引 Anthropic/Cohere 背书 | 低 | 零 embedding |
| **4. chunk 元数据前缀**（商品名/类目注入 chunk） | ★★★☆☆ Anthropic 49%/67% 数据可引 | 低 | 零 embedding |
| **5. bge-reranker 两段精排**（top-50→top-3） | ★★★★☆ 5–7 NDCG 点数据 | 中 | rerank 级小模型，免向量库 |
| **6. hybrid（向量路+RRF）** | ★★★☆☆ +7~8pp recall | 中高 | embedding+向量索引 |

**面试主线话术**：BEIR 证明封闭域词法是强基线 → bigram 与 Lucene CJK analyzer 同构（工业标准）→ 已知边界是同义词/语义近邻 → **用 golden set 实测画边界** → 零成本项（同义词/逐句引用）先行，rerank/hybrid 按评测数据触发升级。
