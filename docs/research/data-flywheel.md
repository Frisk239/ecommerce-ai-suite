# 数据从哪来、怎么洗、飞轮怎么转

对照 `docs/goal.md` §3 咬合图。调研问题：商家侧 Agent 体系里，**权威知识**如何进入、如何治理、如何因自己跑过而变好——同时不让模型自己写死事实。

结论先讲：图上的三条业务闭环是对的；缺的是把「进来的东西」分类，以及拒答之后怎么回到中台。业界把这件事做成 **人在环里的飞轮**，不是无人自进化。

---

## 1. 不要把所有输入送进同一根管子

图顶上写「真实聊天 / 商品资料 / 直播录像 / 素材成品」一口进中台。生产系统会先拆成三类，否则检索会被噪声和活数据污染。

| 种类 | 例子 | 正确去处 | 错误做法 |
| --- | --- | --- | --- |
| **权威知识** | 规格 PDF、售后政策、已治理的对话口径、拣选后的切片、生成后的素材 | 中台资产：接入 → 机洗 → 人洗 → 发布 → 检索索引 | 未审核就进 RAG |
| **活状态** | 库存、订单、物流 | 工具查询，不进检索索引 | 把「有没有货」写进知识库 |
| **噪声生产流** | 未治理的会话原文、工单线程、弹幕 | 模块内原料；合成或人洗之后才登记 | 整段聊天直接向量化给客服搜 |

Amazon 运维技术团队把支援工单做成 RAG 时，明确不在运行时搜原文：先离线合成紧凑知识库（体积降到原文约 3.4%），再接入检索，有用回答从 38.6% 升到 48.7%。见 [arXiv:2506.17484](https://arxiv.org/abs/2506.17484)。

阿里云智能联络中心把同一刀写进产品手册：知识库适合产品说明、服务规则、FAQ；**「不适合存放每位客户的即时状态数据，也不能替代需要实时查询的业务系统」**；库存/订单走系统工具。[AICCS 知识库配置](https://help.aliyun.com/zh/aiccs/user-guide/configure-the-knowledge-base)

本仓库已锁：库存/订单是工具（ADR 0018）；会话不是中台对象，回流后才登记（ADR 0002）；未发布不进索引（ADR 0004）。图上没把这三类画开，讲解时会看起来「什么都进中台」。

---

## 2. 数据从哪来：来源是字段，不是连接器产品

业界把 **source** 做成一等配置，不是一句文案。

- **Enthusiast**（本仓库 `reference/enthusiast`）：商品源和文档源分开，可手动或定时同步，索引给 Agent 用。[Importing Test Data](https://github.com/upsidelab/enthusiast/blob/main/docs/content/docs/getting-started/import-test-data.md)
- **可替换 Provider**（`reference/ai-customer-service-agent-provider`）：订单/商品/政策/知识文档走同一接口，种子和真实库可换。[DATA_PROVIDER.md](reference/ai-customer-service-agent-provider/docs/DATA_PROVIDER.md)
- **Zendesk Knowledge Builder**：用近 90 天已解决工单生成最多 40 篇草稿，**先存 draft，人审再发布**。[官方说明](https://support.zendesk.com/hc/en-us/articles/9409324793498)
- **Salesforce / Microsoft**：检索只吃已发布。Article Answers：「Only published articles… Unpublished edits aren't indexed。」Copilot Studio：「Only published articles are used. Draft or archived content isn't used。」[Salesforce](https://help.salesforce.com/s/articleView?id=service.bots_service_article_answers.htm) · [Microsoft](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-add-unstructured-data)
- **切片**：Stream Clipper 是「自动定位 → 逐条复核 → 导出」，候选不是成品。[README](https://github.com/Cbhhhh211/Stream-Clipper-Factory)

对照本仓库已锁入口（全部落到已接入，没有字节不能登记）：

| 来源种类 | 谁写入字节 | 登记后 |
| --- | --- | --- |
| 上传 | 操作者选文件 | 已接入 |
| 会话回流 | 回流动作写会话正文 | 已接入；操作者要点一下，顾客接口不能自己发布 |
| 切片拣选 | ffmpeg 切开独立片段 | 种类=视频；候选和源录像不是资产 |
| 素材生成 | 生成器写成品 | 种类=素材 |
| 连接层登记 | MCP 必须带文本或字节 | 已接入；不能发布 |
| 种子 | 整包灌入 | 冷启动，可换店 |

v1 不接淘宝/抖音后台（`goal.md` §7）。「散落在 IM、网盘、表格」的解决办法是这六种入口，不是再做一套采集中台。

**血缘**在 `goal.md` 中台必须有（从哪来、被谁用、洗成什么样），`CONTEXT.md` 还没立词。工程里商品写回已有 `asset_id·version` 来源芯片；资产侧还缺锁定的来源种类。

---

## 3. 怎么清洗：机洗出草稿，人洗才成为证据

业界一致的闸门：**模型可以拟，人才能发。**

- Anthropic Commerce Agents：商家侧每次写入先 stage，host 批准才 apply。「Nothing reaches your storefront until a person approves it。」[Building commerce agents with Claude](https://claude.com/blog/claude-for-commerce-agents)；闸门在 [`gates.py`](https://github.com/anthropics/commerce-agents/blob/main/merchant-agent/core/merchant_agent/gates.py)
- Zendesk：工单生成的文章进 draft 列表，有权限的人编辑后才发布（同上官方文）
- AWS「LLM Wiki」实践：人审的是生成产物的逐行 diff，不是「计划做什么」；人工修正做成 pin，源文档冲突不自动覆盖。[AWS 博客](https://aws.amazon.com/cn/blogs/china/llm-wiki-enterprise-practice/)
- 本仓库参考客服仓：知识是 `knowledge_base/` Markdown → `ingest_docs.py` 切块嵌入；业务规则在 `policy/rules.yaml`，LLM 不拥有权限。[architecture.md](reference/ai-customer-service-agent/docs/architecture.md)

映射到已锁三态：

```
来源字节 ──登记──► 已接入 ──机洗──► 待人洗 ──人确认/补全──► 已发布 ──切块──► 检索索引
              │                    │                         │
              │ 失败就地重试         │ 必填=文档×商品规格        │ 客服/MCP/考核/微调只读这里
              └────────────────────┘                         └─ 修订=新版本，指针可回滚
```

机洗：解析、打码、按商品规格字段抽取（可弃权）。**不写入检索索引。**
发布：操作者登录、审计留痕、结构化字段写回商品。连接层和任务不能发布。

这和 Anthropic 的 stage/apply 是同一类闸门，只是对象是资产版本而不是改价单。

---

## 4. 飞轮怎么转：三条业务闭环之外，还要两条「变好」闭环

`goal.md` 已有：

1. **接待**：发布知识 → 客服引用回答 → 会话回流 → 再治理
2. **内容**：商品知识 → 素材/切片 → 登记 → 运营引用已发布
3. **能力**：RAG 不够（口吻/拒答边界）→ 已发布导出微调 → 客服切底座

业界另外两条，本图几乎没画：

### 4.1 缺口闭环（知识变厚）

无命中、转人工、坐席改写，是知识库的生产原料，不是失败后扔掉。

- 覆盖率缺口：低检索置信的查询记下来，每周补内容。[Automely RAG 运维](https://automely.ai/blogs/how-to-build-rag-system-business-knowledge-base)
- 中文智能客服通式：未匹配会话打「知识缺口」→ 人补条目或相似问法 → 再验证匹配。[网易智企·云商](https://b.163.com/cms/ai-ke-fu-wang-yi-qi-yu-2026-9k2yoIuT.html)；腾讯云社区把链路写成「未命中 → 人工解决 → 补充知识库 → 重新索引 → 下次 AI 处理」。[人机协同](https://cloud.tencent.cn/developer/article/2729901)
- Zendesk 坐席在工单里 **Request article**，打开一张打了 `knowledge_request_article` 的票；工单正文**不会**自动变成文章。[Creating and requesting articles](https://support.zendesk.com/hc/en-us/articles/4408835161114)
- 阿里云智能客服「问题调优」列出未解答问题，运营点「添加问题」写成 FAQ，不是模型写进线上库。[网站 AI 套件](https://help.aliyun.com/zh/dws/wais-smart-cs)
- 纠正要可被检索到才算进库，不是贴一条备忘（生产 RAG 上的 nugget 优化，[arXiv:2605.25641](https://arxiv.org/html/2605.25641v1)）

本仓库：ADR 0018 无证据则拒答并转人工，**没有下一步**。会话回流要人点，且回流的是整段对话，不是「这一问缺哪条规格」。飞轮在拒答处断开。

建议形态（仍遵守「只有人能发布」）：

```
拒答 / 转人工 / 工具失败
        │
        ▼
   知识缺口（挂商品或独立；不是中台第三对象）
        │
        ▼
   操作者补文档或开修订 ──登记/待人洗──► 发布 ──► 同一问法下次能答
```

系统自动排队，系统不自动发布。

### 4.2 评测闭环（知道哪里不好）

Databricks Mosaic 的推荐工作流是评价驱动：指标 + 人审过的评价集 → 诊断检索/生成/过期/缺失 → 再部署，生产继续监控。[GenAI Cookbook](https://github.com/databricks/genai-cookbook/blob/main/genai_cookbook/nbs/5-rag-development-workflow.md)；[Agent Evaluation](https://docs.databricks.com/aws/en/generative-ai/agent-evaluation)

本仓库参考客服仓把评测做成可复现产物：110 条、含拒答集、阈值标定、Judge 与人的 κ 不够就不发布分数。[evaluation.md](reference/ai-customer-service-agent/docs/evaluation.md)

本产品的「销售考核」抽的是已发布对话，训练的是销售，**不是**客服引擎的黄金集。能力闭环里的「前后对比」目前停在原型开关。评测集应和微调导出包一样，是已发布资产的派生视图（ADR 0002 已有这个方向）。

坏例分类后再动手，避免乱微调：

| 诊断 | 动作 |
| --- | --- |
| 知识缺失 | 缺口 → 补资产 → 发布 |
| 事实过期 | 修订已发布资产，不微调 |
| 检索切块/阈值 | 工程标定，不动模型 |
| 口吻 / 拒答边界 | 才走微调导出 |

OpenAI 把这件事写成微调红线：SFT **适合**改结构/口吻/复杂指令；**不适合**「Adding entirely new knowledge (consider RAG instead)」。[Fine-tuning techniques](https://developers.openai.com/cookbook/examples/fine_tuning_direct_preference_optimization_guide)

这正是 `goal.md` ⑧ 要讲的：「什么问题不该微调」。

---

## 5. 自进化不是什么

业界明确不做、本仓库也不该做：

- 模型根据聊天直接改已发布规格
- 未发布会话进入检索或微调集
- 连接层 publish
- 把库存快照写进知识库冒充规格
- 无人值守「自动发布」（`goal.md`：成熟后可按种类考虑，默认关，第一版不做）

Anthropic 把「Agent 拟、人签」写成商家 Agent 的默认值；HITL RAG 把审批/修正/拒绝当成训练和评价数据，而不是覆盖草稿。[Targetlytics HITL playbook](https://targetlytics.com/en/blogs/human-in-the-loop-rag-production-playbook)

「越用越好」= 缺口进队列 + 发布进索引 + 坏例进评测集 + 必要时切微调底座。每一步都有人闸门。

---

## 6. 和 §3 图的差

图已经表达：中台是枢纽、MCP 只对外、三条业务闭环。缺四笔：

1. 顶上输入要分类：知识 vs 工具 vs 噪声，不是一筐
2. 中台盒子里要能读到来源种类和血缘，不只是三态
3. 客服拒答要画回中台的「缺口」，否则接待闭环只在人记得点回流时转
4. 评测/坏例是能力闭环的输入，考核不能顶替它

建议把咬合图读成：

```
  上传 / MCP登记 / 种子          库存·订单（工具，不进索引）
  会话回流 / 切片拣选 / 素材成品
                 │
                 ▼
        中台：登记 → 机洗 → 人洗 → 发布
        来源种类 · 血缘 · 检索索引（仅已发布）
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
   客服RAG     考核抽题     微调导出
     │                         │
     │ 拒答→缺口→再治理         │ 评测对比→切底座
     └──────── 回流 ───────────┘
```

---

## 7. 建议锁进词表的三句话（尚未 ADR）

调研建议，需另开 `/grill-with-docs` 才进 `CONTEXT.md`：

1. **来源**：资产的进入通道（上传、会话回流、切片拣选、素材生成、连接层登记、种子）。是资产上的字段，不是中台第三对象。
2. **血缘**：从哪条来源来、洗成哪一版、被哪次检索/导出/考核用过。发布写回商品时带 `资产ID·版本`。
3. **知识缺口**：拒答、转人工或工具失败留下的待补项。挂商品或独立。不是资产；补上的是新登记或修订。系统只排队，人发布。

工程含义：第 2 刀以后的客服引用，Must 里应能从一次拒答看到中台多一条待办；不要把飞轮留到微调那一刀才做。

---

## 来源

- [Building commerce agents with Claude](https://claude.com/blog/claude-for-commerce-agents)
- [commerce-agents gates.py](https://github.com/anthropics/commerce-agents/blob/main/merchant-agent/core/merchant_agent/gates.py)
- [Zendesk: Creating help center content using ticket data](https://support.zendesk.com/hc/en-us/articles/9409324793498)
- [Zendesk: Creating and requesting articles](https://support.zendesk.com/hc/en-us/articles/4408835161114)
- [Databricks GenAI Cookbook — evaluation-driven workflow](https://github.com/databricks/genai-cookbook/blob/main/genai_cookbook/nbs/5-rag-development-workflow.md)
- [Databricks Mosaic AI Agent Evaluation](https://docs.databricks.com/aws/en/generative-ai/agent-evaluation)
- [arXiv:2506.17484 Amazon — unstructured comms to compact KB](https://arxiv.org/abs/2506.17484)
- [AWS 博客：LLM Wiki 企业级实践](https://aws.amazon.com/cn/blogs/china/llm-wiki-enterprise-practice/)
- [腾讯云社区：转人工与知识回流](https://cloud.tencent.cn/developer/article/2729901)
- [Salesforce Article Answers — only published indexed](https://help.salesforce.com/s/articleView?id=service.bots_service_article_answers.htm)
- [Microsoft Copilot Studio — draft/archived not used](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-add-unstructured-data)
- [阿里云 AICCS 知识库配置](https://help.aliyun.com/zh/aiccs/user-guide/configure-the-knowledge-base)
- [OpenAI：微调不用于灌新知识](https://developers.openai.com/cookbook/examples/fine_tuning_direct_preference_optimization_guide)
- [Anthropic anatomy of commerce agents](https://claude.com/blog/the-anatomy-of-effective-commerce-agents)
- 仓库内：`reference/ai-customer-service-agent/docs/{architecture,evaluation}.md`，`reference/enthusiast` 数据源，`reference/commerce-agents/docs/safety.md`
