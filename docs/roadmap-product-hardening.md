# 第三阶段 roadmap：产品硬ening（2026-09-10 立项）

日期：2026-09-10。来源：`docs/research/product-gap-analysis.md`（用户走查×竞品对标×内部盘点三路汇总）+ 两路方案调研（本地参考仓挖掘 + 公网最佳实践，带链接证据）。状态：**Owner 立项，施工权威**——上一阶段（面试级 goal §6.2）已收官，本文件接管排期；`docs/roadmap.md` 降级为上一阶段存档。

## 北极星

**产品立得住，且每一处立得住的地方经得起 grill——两者不冲突，本阶段同时要。**商家第一次打开就能自己上架商品；顾客的第一问（卖什么/多少钱/找真人）不再被拒或被困；操作者对店铺的客服表现有一眼可见的仪表——这是「产品立得住」。同时，上一阶段靠评测数字/ADR/审计撑起来的 grill 防线**不松反紧**：本阶段补掉的每一处「纸糊」都是旧的 grill 弱点（切片是时间码文本、退货状态不动、令牌永不过期——这些被追问一句就穿），所以每刀交付的必须是**真实现**而非新一层 mock，且延续证据纪律（动检索/生成必 before/after；工具语义变更必 ADR；安全面变更必有钉测）。治理闭环（三态/版本锚定/缺口验证闸——竞品确认的独有优势）作为底座不变，本阶段全部新增面都接进既有治理/血缘/审计，不开第二套体系——这既是产品架构选择，也是 grill 时「为什么不用 Onyx/Chatwoot」的答案。

## 排期原则

- 三梯队按「痛 × 量级」排：第一梯队四刀全部小-中量级、每刀独立可演示；纸糊四项其次；对标增强按需（评测数据/演示需要触发）。
- 每刀仍走 slice owner 全流程（intake→ADR/spec→实现→内置浏览器验收→两轴评审→PR→closeout）；每五刀一审计刀（审计刀 8 于第 45 刀后）。
- **grill 证据随刀沉淀**（与上一阶段同强度，不因转产品阶段而松）：动检索/生成路径的刀必须 before/after 同报告（`docs/research/rag-eval-report.md`）；每刀 CI 全量绿；**「真实现」自我检查**——交付物若仍是 mock/固定值（如 46 刀切出的必须是真 mp4 字节、44 刀状态必须真迁移），closeout 里如实写「这处还是 mock」而不是让它看起来像真的；安全面变更（45 刀令牌 TTL）必有越权/过期钉测。

---

## 第一梯队：补最痛（第 41–44 刀）

### 第 41 刀：商品可运营 + 目录可答

**痛点**（差距分析最痛#1）：无 POST/PATCH products（加商品只能改种子）；91 Wikidata 商品空壳；无价格字段——「多少钱」永远拒答并生成死缺口；「你们卖什么」拒答；下拉 114 选 1。

**方案**（调研：Shopify 最小商品只需 title；Medusa 价格挂变体级单一 price；单店最小 = product + variant 级单一价）：
- products 加 `price_cents int + currency str(3) default "CNY"`（单店单币种，不建价目表/变体表——变体留 Out）；迁移 0018 + 存量种子补价（演示库 Wikidata 商品从 OFF/Wikidata 无可靠售价，演示价=类目基准价脚本生成并如实标注）。
- `POST /api/products`（name/category/price_cents/spec_schema 按 category_schema 模板校验）+ `PATCH /api/products/{id}`（操作者鉴权）；web 商品页「上架商品」抽屉 + 规格模板逐项表单（复用机洗确认的模板渲染）+ 编辑；下拉加搜索/过滤。
- **目录可答**：商品创建/改价后自动登记一份「在售商品清单」文档资产（source_kind=seed？——裁决：**新增不进枚举，复用 upload 语义由服务端定值**……不行，0025 锁死六枚举——裁决：**目录清单是派生视图自动再登记，source_kind=upload 服务端代操作者登记并打标**，或干脆作为检索的第三数据源（商品字段直接进索引，ADR 0017 已有「confirmed 字段块」先例——**商品目录进检索索引**：发布切块时 confirmed 的价格/规格字段已入块，缺的是「哪些商品在售」的目录面——最小做法：`retrieve` 无命中时对「卖什么/有什么/目录」类问句回落商品目录列举，或在治理台提供「生成在售清单」按钮登记一份文档资产）。**开刀前 grill 定案**。
- 必开 ADR：价格字段语义 + 目录可答路径（动数据契约）。

### 第 42 刀：转人工真闭环

**痛点**（最痛#2）：意图不识别（实测显式要求转人工被当知识题）；无联系方式通道；无后续预期。

**方案**（调研：Chatwoot pending→open 于显式要真人；Intercom 升级信号=直接要真人/负面情绪/连续未解决；无队列时业界标配=回执给工单号+时间窗+离线表单 name+email+message）：
- 意图识别加「转人工」双路径：词表快路径（人工/真人/投诉/举报）+ 模型提议路径（propose_prompt 加第三「转人工」选项）；命中→kind=handoff 消息（复用现有）+ **工单号生成**（G-xxxx 同款序列或独立 H-xxxx）。
- 顾客面：handoff 消息下带联系方式捕获表单（姓名/邮箱/电话，可跳过——两档：required name+message / optional 全部）；提交落 `handoff_tickets` 新表（迁移 0018 一并或 0019）。
- 回执话术：「已记录工单 H-0001，工作时间 4 小时内回复」（固定窗口，无坐席队列）。
- 操作者面：客服页「转人工」过滤+置顶（badge 显示待处理工单数）；工单解决动作（置 resolved）。
- 词条「转人工」修订（v1 无队列→v2 工单回执+联系方式）：Edit 精确替换，必做。

### 第 43 刀：操作者仪表 + 通知

**痛点**（最痛#3）：零通知零仪表——「我的客服怎么样」回答不了。

**方案**：`GET /api/stats/overview` 聚合端点（近 7 日会话数/拒答数/缺口 open-resolved 流向/thumbs-down 计数/回答引用率——全部现有表聚合，无新表）；总览页「近 7 日」迷你趋势卡（SVG sparkline 手绘或纯 CSS 柱状，不引图表库）；侧栏角标（open 缺口数/待抽检数/未处理工单数——AppShell 导航项 badge，页面加载时随列表请求带出计数，不做轮询）；「没有帮助」反馈汇总卡（哪个资产被踩最多→一键跳详情重新验证——与 39 刀 unverify 闭环咬合）。

### 第 44 刀：退货确认迁移订单状态

**痛点**（纸糊#3）：确认退货后 order.status 纹丝不动——「退货进度怎么样」追问一句就穿帮。

**方案**（调研：commerce-agents 官方 OrderStatus StrEnum 含 return_initiated/refunded——退货是状态机成员）：orders.status 定义合法值集（已发货/运输中/已签收/退货中/已退款——种子现状 + return_initiated）；`confirm_return` 成功时 status→「退货中」+events 追加（既有）；`get_order_status` 工具输出含状态字段（已有）；钉测：确认前「已发货」→确认后「退货中」→进度问句答「退货中」。

---

## 第二梯队：纸糊变单薄（第 45–48 刀）

### 第 45 刀：顾客令牌 TTL + 嵌入 widget

- `customer_token` 加 expires（迁移 + 签发即 24h + 过期 401 与无效同文案——安全面补齐，审计刀 2 以来在案）。
- **单文件 embed.js**（调研：Crisp loader 实测 7.6KB 量级；iframe 面板最强隔离 + launcher Shadow DOM 是 Intercom 双保险；reference/web-widget 的 esbuild iife 单文件 + Custom Element + 第一方 localStorage 访客 id + origin allowlist 全套可抄）：~2KB loader（动态 createElement iframe 指向独立 `/widget` 轻构建——iframe 内复用 /customer 逻辑或裁剪版）；宿主一行 `<script src=".../embed.js" data-origin>`；postMessage open/close；**origin allowlist 是唯一闸**（服务端配置，非白名单 403——抄 web-widget 纪律）；访客身份第一方 localStorage uuid（抄 anythingllm-embed 模式）。README 写 CSP 放行清单。

### 第 46 刀：直播切片真链路（最小）

- 上传一个真 mp4（≤200MB）作为**源录像登记候选的源头**（源录像不是资产、不进检索——词条不变；bytes 存对象存储 `recordings/` 前缀）。
- **ffmpeg 按时间码切出真实片段文件**（调研：`-ss <t> -i in -to <dur> -c copy` 秒级；copy 切点吸附关键帧可接受，重编码留 Out；reference/stream-clipper 的 ffmpeg-python 链式调用 + 原子改名 + ThreadPoolExecutor 并发全套可抄）；切出 mp4 作为资产字节登记（kind=video，对象键 `clips/`——**字节从「时间码文本」变真视频文件**，检索仍靠转写文本进块/qa——转写字段保留）。
- 候选生成：真视频上传→仍按种子/已有候选时间码切（自动切出/ASR 留 Out，faster-whisper 方案已有参考）。演示故事从「时间码文本」升为「真 mp4 切出真片段」。

### 第 47 刀：可观测最小版

- `prometheus-fastapi-instrumentator` HTTP RED 指标 + 3 个自定义：`chat_requests_total`、`ttft_seconds` Histogram、`llm_tokens_total{direction}` Counter（调研：gen_ai 语义约定最小子集=duration/tokens/model/stream）。
- structlog JSON 单文件配置 + correlation-id 中间件（contextvars）；不接 OTel，日志留 trace_id 字段口子。
- `/metrics` 端点（操作者鉴权或内网）；compose 加可选 prometheus/grafana profile（Out 默认关）。

### 第 48 刀：CSAT + 反馈闭环补全

- 会话结束（操作者回流时或顾客关闭）触发 1–5 星评分（顾客面一次性提示；Chatwoot 标配形态）；挂到会话与缺口分析（43 刀仪表加 CSAT 卡）。
- thumbs-up 补收（40 刀 422 明拒的反向）；打回素材补理由输入（reject body 可选 reason）。

---

## 第三梯队：对标增强（第 49+ 刀，按需裁决不预排）

| 刀 | 内容 | 依据 | 量级 |
|---|---|---|---|
| 会话 trace 回放面板 | 每问记录检索命中/证据/耗时/版本快照，详情可回放 | LangGraph time-travel；我们轨迹已落库差快照与 UI | 中-大 |
| 人工标注队列 | 拒答轮/抽查轮进队列打分，回流资产登记 | Langfuse Annotation Queue（与缺口/评测天然咬合） | 中 |
| Generative UI 商品卡 | SSE complete 携带结构化商品数据→前端商品卡 | Vercel AI SDK；依赖 41 刀价格字段 | 中 |
| 知识源连接器 | 飞书文档/企微素材库定时拉取→待人洗 | Onyx 连接器框架；enthusiast SourcePluginRegistry 可抄 | 大 |
| 收件箱+坐席协作 | 转人工会话进收件箱：认领/备注/解决 | Chatwoot；42 刀工单是其最小前身 | 大 |
| checkpoint 编排重放 | 运营编排每步存快照，改输入重跑 | LangGraph；轨迹已落库 | 大 |

## 明确不做（本阶段）

- 多租户/RBAC（单店单操作者语境不变，README 说明）
- 变体/价目表/B2B 定价（41 刀单变体单价足够）
- ASR/自动切出（46 刀真链路先立，whisper 方案参考已备随时可开）
- OTel 全家桶/Jaeger（47 刀最小观测足够答辩）
- 微调（永久，0028）

## 计数与审计

- 刀计数从 41 起；**审计刀 8 于第 45 刀后**（审 41–45）；审计刀 9 于第 50 刀后。
- 本文件由 41 刀 intake 引用生效；`docs/slices.md` 头部推进句与 CONTEXT 同步指向本文件。
