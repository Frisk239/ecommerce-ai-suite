# 自进化高级设计调研：业界模式与升级路线

日期：2026-09-09。调研途径：公网检索（三路调研之路 B）。用途：把我们已有的基础飞轮（拒答→缺口→人补→发布；回流→QA 草稿→人洗）升级成「更高级的自进化」——看业界在飞轮之上还装了什么仪表。基调不变：**人在环里的自进化，不是无人化**（业界共识恰好一致）。

结论先讲：业界「自进化」的共同骨架是**需求驱动的复审调度 + 反馈分诊 + 版本化评测门禁**；我们缺的是仪表（热度/保鲜/分诊），不是缺新环。

---

## 1. 知识缺口优先级/热度排序

- **Zendesk automation potential report**：分析历史会话，按「可自动化工单量」排缺口主题，指导先写哪篇（[官方](https://support.zendesk.com/hc/en-us/articles/9877546283930)）。
- **Intercom Fin Content Gap Recommendations**：把未解决/升级会话归因为 missing / unclear / duplicated / **contradictory** 四类并给编辑动作（[官方](https://www.intercom.com/help/en/articles/11394959)）。
- **Guru**：Answers 分析按 thumbs up/down 定位改进点；My Tasks 用 relevancy score 排「最该先复审的条目」（[分析](https://help.getguru.com/docs/viewing-answers-analytics)、[验证](https://www.getguru.com/features/verification)）。

**对本仓库**：缺口队列是平的——缺「会话量×重复度×新近度」聚合打分与缺口类型标签（缺失/含糊/过期/冲突）。

## 2. 过期检测（staleness/freshness）

- **Guru 验证体系**：Card 带 Verification/Expiration date、Last verified、Verifier、Interval 四字段；Knowledge Agents 每晚按行为/内容/分析三类规则自动 verify/unverify（7 天冷却），决策进 Quality Log 可人工 override；长期无人用且未验证的 auto-archive（[验证机制](https://help.getguru.com/docs/verifying-and-unverifying-cards)、[自动化质量](https://www.getguru.com/features/automated-knowledge-quality)）。
- **Salesforce Knowledge**：API 58.0 起原生 `NextReviewDate`；KCS 插件按类目配复审周期与 staleness 阈值自动 flag（[Trailhead](https://trailhead.salesforce.com/content/learn/modules/knowledgecentered-service-in-lightning-knowledge/manage-article-states)）。

**对本仓库**：资产发布后即「永生」——缺 `last_verified/next_review` 元数据 + 过期自动降权/移出检索。

## 3. 回答质量反馈闭环（thumbs down 回流）

- **Intercom**：系统性审阅全部 negative CSAT 会话→定位内容缺口→更新知识库，靠此纪律维持 75%+ AI 解决率（[社区实践](https://community.intercom.com/topic/show?tid=10873&fid=60)、[官方视频](https://www.intercom.com/blog/videos/the-secret-to-sustaining-75-ai-resolution-rates/)）。
- **Guru**：thumbs down **直接驱动验证自动化**——负反馈触发对应条目复审/unverify，而非只进新缺口队列（[分析文档](https://help.getguru.com/docs/viewing-answers-analytics)）。
- **Zendesk**：negative ratings 与 CSAT 关联定位内容问题（[指南](https://support.zendesk.com/hc/en-us/articles/8357751836314)）。

**对本仓库**：业界共识是负反馈要**分诊**（内容错→复审既有条目；内容缺→缺口队列）。我们有拒答信号，缺顾客侧负反馈通道和分诊逻辑。

## 4. 评测驱动持续改进（知识变更→自动评测）

- **CI 评测门禁**：RAGAS 进 CI/CD 回归（[CircleCI](https://circleci.com/blog/automated-rag-pipeline-evaluation-and-benchmarking-with-ragas/)）；三级实践=PR 轻量检索断言→nightly 全量 LLM-judge→canary 5-10% 流量同 rubric（[实战](https://dev.to/kartik-nvjk/how-i-set-up-rag-evals-in-cicd-so-they-actually-catch-regressions-46hb)）。
- **评测集与语料版本配对**：语料更新后自动跑 anchor check（旧评测集 retrieval 漂移检查），超阈值触发评测重生成；语料与模型变更不同时上线（[RAG Eval Invalidation Paradox](https://tianpan.co/blog/2026/05/07/rag-eval-invalidation-corpus-update-paradox)）。
- **生产失败回流**：线上失败 trace 一键转测试例，周度清洗进 golden dataset（[niteagent](https://niteagent.com/blog/2026-06-12-rag-evaluation-pipeline-guide/)）。

**对本仓库**：资产已版本化（对象键/血缘）天然支持「发布→只跑受影响评测子集」；缺 golden dataset 与发布门禁（与路 C 的评测刀合流）。

## 5. 知识运营自动化（KCS 升级版）

- **KCS 双循环**：Solve Loop（reuse is review、flag it or fix it）+ Evolve Loop（对会话集合做模式分析驱动内容健康）（[双循环](https://library.serviceinnovation.org/KCS/KCS_v6/KCS_v6_Practices_Guide/030/025)）。
- **需求驱动调度**：「没有需求就复审=不做 KCS」——只复审被使用/被问的内容（[Content Health](https://library.serviceinnovation.org/KCS/Knowledge-Centered_Success_Practices_Guide/301-Evolve_Loop/Practice_5_Content_Health/Technique_5.2)）。
- **Guru 落地版**：每周定时汇总（过期/被踩/缺口 Top N）推送摘要，人只看摘要（[最佳实践](https://help.getguru.com/docs/best-practices-maintaining-your-knowledge-agent-over-time)）。

**对本仓库**：缺「谁在什么时候复审什么」的周报视图（血缘已有「被谁用过」，可作需求信号源）。

## 6. 多来源知识冲突

- **ConflictRAG**（[arXiv:2605.17301](https://arxiv.org/html/2605.17301)）：detect-classify-resolve 管线；冲突分 factual/temporal/opinion 三型分别处置；embedding 粗筛+LLM 精判两级检测省 62% 成本。
- **DRAGged into Conflicts**（Google，[arXiv:2506.08500](https://arxiv.org/abs/2506.08500)）：让 LLM 显式推理并**向用户呈现冲突**（而非静默选边）显著改善。
- **Madam-RAG**（[arXiv:2504.13079](https://arxiv.org/html/2504.13079v2)）：每文档一个 agent 辩论后聚合；产品侧 Intercom Fin 已把 contradictory 列为 gap 归因类型。

**对本仓库**：多来源灌入后（数据刀）冲突问题会真实出现——最实用入口是**发布/机洗时同主题两两比对标记冲突**，回答期 temporal 型按血缘+时间戳择新。

## 自进化升级路线（按性价比，进优化计划第二阶段）

| # | 升级项 | 做法 | 改动量 |
|---|---|---|---|
| 1 | 缺口队列打分排序 | 拒答计数（归一化问句累加 hit_count）×新近度打分排序+类型标签（缺失/含糊/过期/冲突，仿 Fin 四分类） | 小 |
| 2 | 条目保鲜元数据 | 已发布资产加 `last_verified/next_review`；过期自动降权或移出检索；「重新验证」= 复审动作（仿 Guru） | 小 |
| 3 | 负反馈分诊回流 | 顾客 thumbs down+拒答统一分诊：内容错→复审队列（指到资产版本），内容缺→缺口队列（仿 Guru） | 小-中 |
| 4 | 发布触发回归评测 | golden set 绑资产版本；发布跑受影响子集+检索漂移 anchor check（与 RAG 评测刀合流） | 中 |
| 5 | 发布时冲突检测 | 同主题/同商品资产两两 LLM 比对标记冲突进待人洗；temporal 型自动择新 | 中 |
| 6 | 周度知识健康报告 | 定时汇总缺口/过期/冲突/负反馈 Top N（KCS Evolve Loop 产品化） | 中 |
