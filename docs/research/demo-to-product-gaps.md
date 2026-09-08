# 从样机到产品：数据供给与产品缺口的业界对照

接 `docs/research/data-flywheel.md`（那份管「知识怎么分类、飞轮怎么转」；本文管「数据从哪个通道进来、产品缺口业界怎么补」）。背景：当前六刀 + 厂商生成 + 顾客通道交付后，仓库是高质量单店样机；离产品差三件事——数据从哪来、产品缺口、真实用户。本文调研前两件的业界做法，供 `/grill-with-docs` 决策。

结论先讲：

1. **知识接入是竞品的一等功能，不是附属**。Gorgias/Zowie/Zendesk/Intercom 都把「自动同步 + 批量导入 + 工单挖掘」做进产品首屏；本仓库六种来源设计是对的，但只通了人工上传这一条最贵的通道。
2. **冷启动业界解法 = 三通道**：自动同步（网站/帮助中心/商品目录）、批量导入（CSV/文档）、历史工单→QA 草稿（人审入库）。第三条与本仓库「会话回流」惊人接近，差的只是回流后自动抽 QA。
3. **工具调用（订单/库存）有清晰可抄的工程模式**：两个好工具胜过十个薄工具、写操作两阶段、资格校验在代码不在 prompt、转人工触发器显式化 + 结构化交接。
4. **顾客通道嵌入有业界标准形状**：loader script + 跨域 iframe + publishable key + 短时会话令牌。ADR 0021 的 API 设计已经是这个形状，缺 widget 层。
5. **数据源现实性**：国内平台（淘宝开放平台）要企业资质 + 聚石塔入驻，个人求职项目走不通；Shopify dev store 免费、可生成测试数据、Admin API 全开——是「接一个真实电商平台」的唯一现实选项。

---

## 1. 知识接入：竞品把「同步」做成一等功能

| 产品 | 接入通道 | 值得注意的设计 |
| --- | --- | --- |
| **Gorgias** | 手写 guidance / 帮助中心（自动含入）/ 店铺网站自动同步 / 单页 URL / 文档上传 / Shopify 商品目录（单独管理） | **首次开启 AI Agent 时自动同步店铺网站**（品牌故事、隐私政策、FAQ），并从同步内容**自动生成 QA snippets**；AI 自己从真实会话里发现知识缺口和矛盾内容 |
| **Zowie** | 手动 / 复制粘贴 / 网站导入（**CSS selector + 预览**，排除导航页脚）/ API / Zendesk/Kustomer/Salesforce 导入 | 导入后有「保持同步」vs「断链转 native」二选一——断链后可自由编辑不影响源；变更日志 + 版本恢复 |
| **Zendesk AI** | 自家帮助中心 / Salesforce/Freshdesk 帮助中心 / Confluence（24h 自动同步）/ **CSV（title+content 两列）** | 官方明确：「AI 搜的不是活数据，是导入快照 + 定期重导」；**电商商品页不建议爬，建议做集成实时取** |
| **Intercom Fin** | 原生创建 / 公开 URL 同步（每周）/ Zendesk 导入 / **历史会话与工单（给 Copilot）** | 官方建议原生优先：原生内容即时可检索，外部 URL 每周才更一次 |

对照本仓库：`source_kind` 六种（上传/回流/切片拣选/素材生成/连接层登记/种子）已锁（ADR 0025），设计比上面几家更严格（全部落已接入、人才能发布）；缺的是**自动化通道**——业界证明「同步网站」「CSV 导入」「URL 导入」是标配，且都有「同步 vs 断链」「定期重导」的治理语义。

---

## 2. 冷启动：三通道 + 历史对话挖 QA

冷启动鸡生蛋（空库→全拒答→无回流→无新资产）业界有成熟解法，按投入产出排序：

### 2.1 信号源（Paperchat 的排序）

1. **支持收件箱/聊天记录**：导出最近 200–500 条，**按意图聚类**（「怎么取消」「我想停订阅」「取消按钮在哪」是一个问题）
2. **站内搜索日志**：自己搜索框里零结果的查询是金子
3. **搜索控制台查询**：带来陌生访客的问题就是新客会问的问题
4. **销售电话/onboarding 笔记**：售前问题几乎不进工单，却带着营收

经验律：**约 20% 的问题类型驱动约 80% 的联系量**（Pareto）——小答案集吃大头。

### 2.2 工单→知识的工艺（Agentkit 九步循环）

找重复已解决问题 → 去客户数据 → 分离通用规则与一次性决定 → 对照现有来源验证 → 写窄答案（一个意图一问）→ 测相邻问法 → 发布 → 给过期条件（挂产品/政策变更触发重审）。核心句：「**工单是发现队列**：告诉你缺什么知识 + 顾客怎么措辞」，不是把工单原文灌进库。

### 2.3 历史对话→QA 的自动化（论文级）

[AI Knowledge Assist（arXiv:2510.08149，华为）](https://doi.org/10.48550/arxiv.2510.08149)：LLM 从历史客服对话抽 QA 对 → 聚类 → 每簇构造代表性 QA → **推荐给知识管理员审核后入库** → 实测消除冷启动缺口。Macha 的 AI Knowledge Builder 同型产品化：工单一键变文章（新篇或**合并进已有相似篇**），低价值过滤（自动回复/垃圾/薄线程默认跳过）。

对照本仓库：第 4 刀缺口闭环 = 业界「知识缺口审计」（Grow2.ai：持续扫工单流 vs 现有库，输出 covered/partial/gap/outdated 判定）的同构物，且更严格（发布事务内闭环）。会话回流（0013）已有「对话→资产」通道，**缺的一步是回流后自动抽 QA 草稿供人洗**——现在是整段对话直接当 dialogue 资产，检索粒度粗。

---

## 3. 活状态：工具调用与转人工的工程模式

库存/订单是工具不进索引（ADR 0018 已锁），业界工程模式（[FDEInterviews 的标准答案](https://fdeinterviews.com/q/support-agent-tool-design)、Sierra 公开架构）：

- **两个好工具胜过十个薄工具**：`get_order_status(order_id | email+zip)` 只读返回结构化状态+物流事件；写操作（退货/退款）**两阶段**——`check_eligibility` 出报价/资格判定，`create_return` 要显式确认。
- **资格校验在代码不在 prompt**：退货 API 自身拒绝超窗商品，越狱的模型也发不出非法退款。「authority lives in code; the model only has discretion where policy allows it」。上限熔断：≤$200 自动批，以上转人工。
- **工具描述就是 prompt**；返回**结构化错误**让模型能行动（「订单没找到，请顾客核对邮箱」）而不是栈迹。
- **转人工触发器显式定义**：用户要人 / 情绪敌对 / 连续两次工具失败 / 低置信政策边界 / 法务医疗安全。**交接带结构化摘要**（意图、订单、已做步骤），人不必从头问。转人工率 15–30% 起步逐步下降属正常，**0% 是红旗不是目标**。
- **Sierra 的模型星座**：订单/库存/商品查询这类简单工具调用用低延迟小模型；分类/检索/工具/政策/语气拆成模块化任务，supervisor 模型执行 guardrail。「creative, but in the moments that matter, deterministic safeguards」。
- Sierra/Decagon 都**不替代 helpdesk**：AI 平台与人工工单系统并存，AI 负责自主解决，helpdesk 管人工队列。

对照本仓库：ADR 0018 的「工具失败同样转人工」有了，但**工具本身一个都没有**，转人工 v1 只是消息种类（CONTEXT 已锁）。最小补法业界指得很清楚：一个只读 `get_order_status` + 显式触发器 + 结构化交接。

---

## 4. 顾客通道：嵌入的业界标准形状

多家（Fruxon/AKOBOT/Clanker/Chipp/Gravity Rail）收敛到同一架构：

1. **loader script**：宿主页一段 `<script>` 挂浮动气泡，内嵌**跨域 iframe** 装聊天界面
2. **publishable key**（类 Stripe `pk_`）：公开非秘密，只开一个 agent 的匿名会话，受 **origin allowlist** 门禁
3. **会话令牌**：访客首次打开时 `POST …/bootstrap` 现场签发，短时（约 15 分钟自动刷新），pin 到 session+agent+visitor；**宿主页永远看不到令牌，只有 iframe 持有**
4. **不用第三方 cookie**：iframe 分区存储 + Bearer；父↔iframe 的 postMessage 只传 resize/collapse，宿主页读不到对话内容
5. SSE 流式（reply/typing/status 事件）；`GET /embed/{key}` 免安装全页预览（WordPress shortcode 同款）
6. 转人工可参数化：Clanker 的 `data-escalation-threshold`（N 条消息后提供「找人工」）

对照本仓库：刚交付的顾客通道（`POST /api/customer/sessions` 签发 token + `Bearer` + IP 限流 + SSE 同引擎）**就是业界形状的 API 层**——差的是 widget 三件套：embed key/origin allowlist、loader+iframe 前端、iframe 内的 SSE 消费。这一刀做完，「顾客从哪里进来」就有答案。

---

## 5. 数据源现实性（求职项目约束）

- **国内平台不可行**：淘宝开放平台商家侧 API（订单/商品/客服）需企业资质入驻、应用安全等级、聚石塔内 IP 才取明文 R2 字段（收货人/地址）——个人开发者拿不到。[淘宝开放平台入驻指南](https://developer.alibaba.com/docs/doc.htm?treeId=478&articleId=120867&docType=1)
- **Shopify dev store 可行且免费**：Partner 账号（免费）可建**无限 dev store**（不限时、约 Advanced 计划功能），建店可勾选 **Generate test data**（自动填充商品/订单/顾客/主题），`shopify app dev` 走 OAuth 调 GraphQL Admin API 读全量商品目录与订单，Bogus Gateway 可跑无限测试交易。[Shopify dev stores](https://shopify.dev/docs/apps/build/stores/development-stores)
- 结论：要「接一个真实电商平台」的演示叙事，Shopify 是唯一现实选项（叙事：跨境电商商家）；不接平台的话，业界标配的 **CSV 导入（title+content）与 URL 导入**也足以支撑「数据从哪来」的故事，且工作量小一个量级。

---

## 6. 对本仓库的启示（候选决策，未锁，待 grill）

按「喂给已建成飞轮 > 开新战场」排序：

1. **数据接入刀**（差距一）：URL 导入（爬商家页→登记 source=upload/sync，落已接入走机洗）或 CSV 批量导入。Zendesk 的 CSV（title+content）是最小形状；「同步 vs 断链」语义与本仓库版本指针天然咬合（同步=新版本，断链=修订后脱离源）。
2. **回流增强**（差距一，与 1 二选一或合刀）：会话回流后自动抽 QA 草稿（LLM 抽→人洗→发布），对标 AI Knowledge Assist / Zendesk Knowledge Builder，与本仓库机洗「抽结构化字段」是同一模式的复用。
3. **订单工具刀**（差距二）：只读 `get_order_status` + 两阶段写操作（或 v1 只读）+ 显式转人工触发器 + 结构化交接摘要。数据可来自 Shopify dev store 或 mock 订单表。
4. **顾客通道 widget 刀**（差距二）：embed key + origin allowlist + loader/iframe + SSE。API 层已就绪，纯前端+一小层鉴权。
5. **Shopify 接入**（叙事升级，可选）：真实平台数据源，简历上「接 Shopify Admin API 同步商品目录」比「mock 两个商品」硬一截。

真实用户（差距三）：求职项目语境下弱化为「可演示的完整闭环 + 评测集护航」（evals 先写也是业界标准：golden conversations + policy edges + 对抗案例进 CI）。

### 参考仓已就位（`reference/README.md`）

| 候选刀 | 参考克隆 | 抄什么 |
| --- | --- | --- |
| 1 数据接入 | `enthusiast`（已有） | Shopify Admin API 商品目录/文档源同步；「来源是字段不是连接器」 |
| 2 回流增强 | `faq-extract`（新）+ `qa-extraction-with-human-review`（新） | 前者：抽问→嵌入→聚组→取上下文→生成 QA 的五步流水线（与 arXiv:2510.08149 同构）；后者：QA 人审工作流、源引用、质量过滤 |
| 3 订单工具 | `ai-customer-service-agent` + `commerce-agents`（已有） | 受控工具/引用/拒答阈值；stage/apply 两阶段写闸门 |
| 4 顾客通道 widget | `web-widget`（新）+ `anythingllm-embed`（新） | 前者：无 iframe 派（Shadow DOM + Origin allowlist + 第一方访客 id）；后者：embed-id 派（随机会话 id + 每 embed/每会话限额） |
| 5 Shopify 接入 | `enthusiast`（已有） | 同 1 |

## 来源

- [Gorgias: Knowledge explained](https://docs.gorgias.com/en-US/knowledge-explained-6556817) / [Add your brand's content](https://docs.gorgias.com/en-US/add-your-brands-content-for-ai-agent-1216498)
- [Zowie: Importing Knowledge](https://docs.zowie.ai/docs/importing-knowledge) / [Knowledge](https://docs.zowie.ai/docs/knowledge)
- [Zendesk: Importing knowledge sources](https://support.zendesk.com/hc/en-us/articles/10791195957530-Importing-knowledge-sources-to-power-generative-replies-in-AI-agents-AI-agents-Advanced-only)
- [Intercom: Knowledge sources](https://www.intercom.com/help/en/articles/9440354-knowledge-sources-to-power-ai-agents-and-self-serve-support)
- [Paperchat: Build an FAQ chatbot from existing docs](https://www.paperchat.co/blog/build-faq-chatbot-from-documentation)
- [Agentkit: Turn support tickets into answers](https://agentkit.ai/blog/chatbot-knowledge-base-support-tickets)
- [Grow2.ai: Knowledge Base Gaps](https://grow2.ai/en/automations/support/knowledge-base-gaps)
- [AI Knowledge Assist, arXiv:2510.08149](https://doi.org/10.48550/arxiv.2510.08149)
- [Macha: AI Knowledge Builder](https://www.getmacha.com/docs/ai-knowledge-builder)
- [FDEInterviews: Support agent tool design](https://fdeinterviews.com/q/support-agent-tool-design)
- [Sierra: Constellation of models](https://sierra.ai/blog/constellation-of-models) / [Shipping and scaling AI agents](https://sierra.ai/blog/shipping-and-scaling-ai-agents)
- [Helpshift: Decagon vs Sierra](https://www.helpshift.com/blog/decagon-vs-sierra/)
- [Fruxon: Embed Widget](https://docs.fruxon.com/guides/embed-widget/) / [AKOBOT: Website widget](https://akobot.ai/docs/widget) / [Clanker: Widget & iframe embed](https://docs.clankersupport.com/integrations/widget) / [Chipp: Embed widget](https://chipp.ai/docs/guides/embed-widget.md) / [Gravity Rail: Chat Widget Embed](https://docs.gravityrail.com/developer/guides/chat-widget)
- [Shopify: Dev stores](https://shopify.dev/docs/apps/build/stores/development-stores) / [Generated test data](https://shopify.dev/docs/storefronts/themes/tools/development-stores/generated-data) / [Development stores blog](https://www.shopify.com/partners/blog/development-stores)
- [淘宝开放平台：应用软件开发商接入指南](https://developer.alibaba.com/docs/doc.htm?treeId=478&articleId=120867&docType=1) / [API 安全等级与聚石塔](https://open.alitrip.com/docs/doc.htm?articleId=1002&docType=1&treeId=780)
