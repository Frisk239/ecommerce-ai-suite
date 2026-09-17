# 体系闭环演示手册（第 96 刀）

给「我自己用用」的演示剧本：按体系演进链顺序编排 12 个**已实现**回路（幕），
外加 4 个**在途**回路（标注刀号）。每幕 = 操作步骤 + 预期画面 + 对应主张 +
可指测试文件。所有素材都在演示库（db 5433）里真实发生过至少一次——实录出处
见 `docs/research/real-usage-log.md`（Ⅰ=第 92 刀，Ⅱ=第 96 刀）。

## 演示前（一条命令）

```bash
docker compose up -d            # 演示栈（db 5433 / api 8000 / web 5173）
uv run python scripts/demo_prepare.py --db postgresql://suite:suite@localhost:5433/suite
# 缺什么看 --fix-hint 给的补救命令；它是检查器，不自动重建（治理动作不自动）
uv run python scripts/demo_reset.py --db postgresql://suite:suite@localhost:5433/suite
# 只报告探针噪音（空会话/探针资产）；要清须显式 --apply
```

浏览器开 `http://localhost:5173`（操作者台：会话/资产/缺口/审计四个 tab）；
顾客通道在客服页右下角小组件（95 刀起可嵌入、可续接）。

## 口径

- **已实现幕**：本仓当前分支可现场重演，库内有素材、代码有测试钉。
- **在途幕**：roadmap 已排刀（97/98/98b/99），手册不写「已实现」话术。
- 引用二元组 = `{asset_id, version_no}`——服务端从检索命中定，模型无决定权。

---

## 第一幕：拒答飞轮（缺口 → 补口径 → 发布 → 再问命中）

**主张**：答不上的问题不糊弄——拒答、留缺口、缺口驱动补文档、发布即解决、
同问法立刻命中。这是整个体系的「为什么」。

操作步骤：
1. 客服页问「能刻字吗」→ 拒答（无证据不调模型，0018）。
2. 打开治理台缺口 tab → open 缺口「能刻字吗」（演示库现成例子：**G-78**，
   会话 #331 的实录）。complete 事件带 `gap_id`（操作者口径），拒答消息
   文本带「缺口 G-78」芯片可跳转。
3. 点「去补文档」→ `POST /api/assets/register` 挂 `knowledgeGapId=78`
   上传《定制刻字服务口径》（演示库现成例子：**A-492**）。
4. 发布 → **发布事务内 G-78 自动 resolved**（`resolved_by_asset_id=492`，
   无独立手动关闭端点——解决只随发布，0024）。
5. 同问法再问「能刻字吗」→ answer 引 **A-492·v1**：「塑料外壳不接受刻字[1]…
   不支持七天无理由退货[2]」（会话 #344 实录）。

预期画面：缺口 tab 里 G-78 行从 open 变 resolved；再问的回答带 [1] 引用角标，
点开是《定制刻字服务口径》正文。

可指测试：`apps/api/tests/test_governance_integration.py`（缺口随发布解决/
同问法命中）、`test_system_loop.py`（96 刀整环剧本）。

**演示提示**：不必现场重走全链——直接在会话列表搜 #331（缺口诞生）与 #344
（再问命中），资产页开 A-492，缺口 tab 过滤 resolved 即见 G-78。

## 第二幕：版本指针（修订发布 v2 → 引用跟随）

**主张**：已发布是唯一权威；修订走「开修订 → 人洗 → 发布」，指针前移后
引用自动跟随新版，历史版不丢。

操作步骤：
1. 资产页找 **A-503**（94b 钉子图）：v1 已发布但**无图片描述**（检索面
   空白——未确认的描述不进索引）。
2. 开修订（`POST /api/assets/503/revisions`）→ v2 人洗 PATCH
   「图片描述=显示器侧面带可调节支架，正面三边窄边框」→ 发布 →
   指针前移 v2。
3. 问「有带支架的显示器吗」→ citations 带 **{503, version_no=2}**——
   引用跟随指针（会话 #349/350/351 实录：修订前问句引 501+11，修订后
   503·v2 进引用）。
4. 历史版不丢：MCP `get_asset(503, version=1)` 仍可取（读权威含历史版）。

预期画面：A-503 详情「版本」列 v1/v2 两行，线上版本=v2；问句引用角标
显示 v2。

可指测试：`apps/api/tests/test_lineage_integration.py`（版本链/指针前移）、
`test_image_integration.py`（图片描述确认→索引）。

**演示提示**：开 A-503 详情页对着版本列表讲；再切到会话 #350 看引用角标。

## 第三幕：三态治理 + 必填闸（登记→机洗→人洗→发布，双审计行）

**主张**：资产三态（ingested → pending_review → published）；机洗只出草稿，
人洗确认才生效；挂商品的规格文档有必填闸；confirm 与 publish 各留一行审计。

操作步骤：
1. 登记《LK201 规格（Wikidata）》挂键盘商品（必填字段=品牌）→ 机洗**弃权**
   （Wikidata 无此属性，如实无草稿）→ 停 ingested/pending_review 待人洗。
2. 人洗 `PATCH /api/assets/497/versions/1/fields` 确认品牌 → **audit 落
   confirm 行**。
3. 发布 → **audit 落 publish 行**；必填闸验证：品牌缺失时发布 422
   （`publish_gate_failed`），确认后才 200。
4. 审计 tab 看 A-497 两行：confirm + publish（操作者、时间齐全）。

预期画面：治理台 A-497 状态徽章流转；审计页两行并排。

可指测试：`apps/api/tests/test_governance_integration.py`（双审计行）、
`test_publish_gate.py`（必填闸 422/确认后过）。

**演示提示**：现成例子 **A-497**（92 刀 §三点五补录的完整链）；误挂的
A-496 以「已废弃」形态同屏可见（运维外科，ADR 0042）。

## 第四幕：回流变知识（会话 → dialogue 资产 → 机洗 QA → 待人洗）

**主张**：对话本身是知识原料；回流登记把会话转写成 dialogue 资产，
LLM 机洗抽 QA 草稿，人洗发布后才进检索面。

操作步骤：
1. 会话页挑一条答得好的会话 → 「回流登记」（`POST /api/service/sessions/
   {id}/register`）。
2. 会话置 **registered** 并指向新资产；资产 kind=**dialogue**、
   source_kind=session_backflow、标题=顾客首问摘要（出口掩 PII）。
3. 机洗抽 qa_pairs 草稿（无 LLM key=弃权降级，照常待人洗）→ 人洗
   `PATCH .../fields` 确认 QA → 发布 →「问：…/答：…」块进索引。

预期画面：会话列表该行状态徽章变「已回流」并带资产链接；资产页对话
种类显示转写正文与 QA 对。

可指测试：`apps/api/tests/test_service_integration.py`（回流登记状态机）、
`test_machine_wash.py`（QA 抽取/弃权）。

**演示提示**：现成例子 **A-493**（会话 #345，Xperia Ear 上市年份问答，
pending_review 待人洗）——正好停在「等人洗」这一步，现场补确认+发布即续。

## 第五幕：图片治理（上传 → 描述 → 发布 → 问句命中）

**主张**：图片不是二进制黑箱——描述是它的检索文本面；VLM 只出草稿，
人确认生效（ADR 0051）；未确认的描述不进索引。

操作步骤：
1. 治理台上传一张商品图（png/jpeg/webp，≤10MB，魔数复验）→ kind=image
   资产登记。
2. 无 VLM key：图片描述=弃权（不写假值）；有 key：VLM 草稿落 extracted
   （source=machine）。
3. 人洗 PATCH 写/确认描述 → 发布 → 切块=描述文本（不是二进制）。
4. 顾客问图片内容词（如「有带支架的显示器吗」）→ 命中带引用。

预期画面：资产详情「图片描述」字段从草稿态到确认态；问句回答引用该图。

可指测试：`apps/api/tests/test_image_integration.py`（94a 全契约：
草稿不进索引/409/商品素材聚合）。

**演示提示**：现成例子 **A-501**（显示器商品图·实拍帧）与 A-503 v2。

## 第六幕：内容自循环（直播 → 切片 → 洗帧 → 素材库 → 客服引用）

**主张**：一场直播沉淀成可检索资产的全链：录像转写候选 → 拣选切片 →
已发布视频洗帧 → 图片资产进素材库 → 顾客问句出**真帧**。

操作步骤：
1. 切片页：录像上传 → 转写候选（见第八幕）→ 人工拣选 → 切出真 mp4
   切片资产（演示库：**A-264**《瓶装水抗压实拍》47s，已发布）。
2. A-264 详情 →「洗帧到素材库」→ 均匀采样（每 5s/上限 24 帧）+ VLM
   打分（≥6 过线/上限 8）候选网格 → 勾选 5s 处 → 确认登记 **A-505**
   （`clip_frame` 来源，标题「瓶装水 · 实拍帧 00:05」，回执
   `cut_from=A-264·v1` 血缘锚）。
3. A-505 人洗确认描述 → 发布。
4. 顾客问「有瓶装水直播实拍的清晰商品图吗」→ citations+media_citations
   含 505，媒体端点 200 出真帧（会话 #353 实录，94c 验收）。

预期画面：切片页候选网格 → 资产页血缘（cut_from）→ 顾客回答内嵌真帧图。

可指测试：`apps/api/tests/test_frames_integration.py`（洗帧契约）、
`test_clips_integration.py`（切片拣选）、`test_real_clips_integration.py`
（真 ffmpeg 全链）。

**演示提示**：A-264 详情页按钮在无 VLM key 时禁用+明文提示——打开按钮
态本身就是「fail-closed」的一幕；素材链讲解顺序 A-264 → A-505 → 会话 #353。

## 第七幕：媒体引用（问句双图直出）

**主张**：文本引用与媒体附件同源派生（citations 的姊妹键
media_citations，ADR 0052）；模型无决定权；重载会话由落库的
media_citations 还原图/播放器。

操作步骤：
1. 顾客问「有带支架的显示器吗」→ complete 恒有 `media_citations` 键
   （无媒体=[]，形态固定）。
2. 命中已发布图片资产时双引用直出：citations
   [{501,1},{503,2}] + media_citations [{501,1,image/jpeg},{503,2,image/jpeg}]
   （会话 #350/#351 实录：**双图直出**）。
3. 点图走鉴权媒体端点 `GET /api/customer/assets/{id}/media`（双通道：
   操作者 cookie / 顾客令牌）；只出**当前已发布指针版**，旧版/未发布一律
   404 同一文案（不泄漏存在性）。
4. 刷新页面 → 图由消息落库的 media_citations 还原，不再依赖重算。

预期画面：回答卡片下并排两张实拍图；Range 播放器对视频资产可用。

可指测试：`apps/api/tests/test_media_integration.py`（94b 全契约）。

**演示提示**：会话 #350/#351 即现成画面；对比 #348（修订前）可见引用
集合随指针迁移。

## 第八幕：ASR 候选（录像 → 转写 → 候选落队 → 拣选）

**主张**：转写只出**候选**，人工拣选闸门之后才切真片（93 刀，ADR 0050）；
无 key 端点 409 诚实拒绝，不假装转写。

操作步骤：
1. 切片页上传录像（93 刀实录：live93.mp4，TTS 合成讲解，三句带 1.6s
   停顿）→「自动转写」→ 云转写（Groq whisper-large-v3-turbo，
   OpenAI 兼容）→ 停顿聚合切句 → **pending 候选三行落队**
   （transcript_source=cloud，时间码与 TTS 句间停顿对齐）。
2. 候选带「转写来源」徽章（cloud/local/manual 三色口径）。
3. 人工拣选 → 真切 mp4（ffmpeg）→ 切片资产登记（A-498~500 实录）。
4. 已有未拣选 cloud 候选时重跑 409；拣选后可再生成一批（幂等口径）。

预期画面：切片页候选列表三行 pending + 来源徽章；拣选后资产页出现视频
资产与时间码。

可指测试：`apps/api/tests/test_asr_integration.py`（替身不打真网：
候选聚合/409/拣选）、`scripts/transcribe_local.py`（funasr 本地兜底）。

**演示提示**：**需 ASR key**（Groq 免费）——无 key 时按钮禁用+明文提示，
以「诚实禁用态」演示（演示库候选 35–37 已拣选、38–40 留 pending 可指）。

## 第九幕：MCP 读权威（外部 Agent 只读四工具）

**主张**：外部 Agent（IDE/自动化）经 MCP 连接层**只能读已发布**；
登记算「已接入」不算知识；活状态（订单/库存）不进连接层。

操作步骤：
1. `MCP_BEARER_TOKEN=... uv run python scripts/mcp_smoke.py --evidence`
   （api 起着、令牌同 compose env）。
2. 输出四断言：**E0** register 返回 status=ingested/pending_review
   （未发布）；**E1** 工具恰四且无 publish（search_published/get_asset/
   register_asset/export_published——集合相等，偷加任何工具即 FAIL）；
   **E2** 未发布不进检索（标记词登记前后命中零变化）；**E3** 无
   order/stock 字样活状态工具。
3. `get_asset` 支持当前版与历史版（配合第二幕讲版本指针）。

预期画面：终端逐行 `PASS E0/E1/E2/E3`；治理台出现一条 mcp-smoke 探针
资产停在待人洗（外部写入也要过人洗闸）。

可指测试：`apps/api/tests/test_mcp_evidence.py`（pytest 版同断言）、
`test_mcp.py`。

**演示提示**：探针资产是「外部写入也逃不过治理」的活证据；演示后可跑
demo_reset --apply 清探针。

## 第十幕：降级诚实（无 key → 模板回退徽章）

**主张**：LLM 不可用不撒谎——证据组装模板兜底、回答仍带引用、前端亮
「模板回退」徽章；每条 LLM 路径各有诚实降级（91 刀 runbook 的时刻表）。

操作步骤：
1. `.env` 置空 `LLM_API_KEY`（或等网关抖动）→ 问政策类问题。
2. 回答 = 证据组装模板，complete 带 `fallback=true` → 前端「模板回退」
   徽章；引用照常（92 刀实录 10 条 fallback）。
3. 对照 `ops/runbook-llm.md` 三步换端点（改 env → rebuild api → 客服页
   验证无徽章）；观测佐证 `llm_tokens_total`。
4. 同族降级：回流机洗 QA 抽不到→停已接入可重试；素材任务 failed
   （素材路径无降级，失败即失败）。

预期画面：回答上方灰色「模板回退」徽章；换端点后徽章消失、回答变生成体。

可指测试：`apps/api/tests/test_llm.py`（降级路径）、`test_answer.py`
（模板组装）。

**演示提示**：不必真拆 key——92 刀日志里 10 条 fallback 的会话实录
（usage-92-records.json 归档）可直接引；或临时停网关容器模拟抖动。

## 第十一幕：OOV 两类分说（不在库不落缺口 vs 知识待补落缺口）

**主张**：问「库里没有的东西」分两类归宿（86 刀）：**不在库**（补文档
补不出来）→ 诚实文案+工单、**不落知识缺口**；**在库资料缺**或一般知识
待补 → 照落缺口（点「去补文档」能补出来）。缺口池不被经营范围问题污染。

操作步骤：
1. 问「支持分期付款吗」→ 实体判 OOV 且商品表无此物 → 文案「本店暂时
   没有「持分期付款」这款商品，已记录并转人工处理」+ 工单建、
   **knowledge_gaps 无此问**（92 刀会话 #331/#332 实录）。
2. 对照问「有没有白色款」→ 不构成 OOV（语料覆盖决定）→ 常规无证据
   拒答 → **G-80 open 落库**（知识待补，补文档可解）。
3. 反向钉：问「雀巢咖啡的配料是什么」（旧症状）不会引别人家商品的
   配料作答——宁可漏判不误杀（82 刀判据三条件）。

预期画面：缺口 tab 对比——「分期付款」不在列、「白色款」G-80 在列
open；工单 tab 两类都有单。

可指测试：`apps/api/tests/test_oov_gate.py`（判据/两类文案/落缺口
语义）、`test_synonym_wiring.py`。

**演示提示**：语料规模护栏（<100 块不判 OOV）是演示库可用的一部分——
别把测试库小语料误当生产行为。

## 第十二幕：会话续接（reload 恢复 / ended 锁）

**主张**：顾客关页面不是丢会话——widget 存档（localStorage）+ 恢复端点
（95 刀）；ended 不复活对话流但历史/评分仍可用。

操作步骤：
1. 顾客 widget 问一句 → 关页重开 → 自动恢复：active 会话历史重放
   （含工具条/评分条），续问同会话。
2. 顾客点「结束」→ reload：横幅「会话已结束」+ 历史只读 + 评分仍可
   提交（5 星后 reload 回显）；再发问 409。
3. 清 localStorage → 空态新会话；令牌过期/无效 → 401 清档（文案与
   发问同口径，防探测）。

预期画面：widget 三态（恢复续问/结束锁/空态）；恢复端点
`GET /api/customer/sessions/current/messages` 200 回 rating/ticket 锚。

可指测试：`apps/api/tests/test_session_resume_integration.py`（95 刀
11 例：三态 200/401 口径/消息形状防漂移）。

**演示提示**：直接开 `http://localhost:5173` 客服页右下角 widget 演示；
或嵌宿主页演示 `/embed-demo.html`。

---

### 幕 13：考核题源锚（一份数据两用）

- 操作：治理台「销售考核」→ 开始作答 → 观察题面。
- 预期画面：题目抽自已发布对话资产，**题源锚 A-xxx·vN**（同一份聊天数据既喂客服 RAG 又喂考核）。
- 主张：数据中台是唯一权威——考核不私藏题库。
- 可指测试：`apps/api/tests/test_coaching*.py`（题源锚断言）。
- 演示提示：题库薄（数据面：多发布对话即可，56 刀口径）。

### 幕 14：SFT 数据集导出（可溯源微调数据供给）

- 操作：登录操作者 → `curl -X POST -b cookie http://localhost:8000/api/exports/sft -o sft.jsonl`（或脚本调用）。
- 预期画面：JSONL 附件——首行头注释（generated_at/asset_count/sample_count/exported_by/license_note 五字段），其后每条 `{"instruction","output","meta":{asset_id,version_no,source_kind,title}}`——**逐条血缘可追回**。
- 主张：中台是微调数据的生产线（只出人工确认过的问答对）；本产品不做训练。
- 可指测试：`apps/api/tests/test_exports_sft.py`（血缘钉/未发布不出现/audit 本人）。
- 演示提示：演示库 4 份已发布 dialogue 出 6 样本；audit 留痕 action=export_sft。

## 在途幕（不写「已实现」话术，演示时明说「下一刀」）

| 幕 | 刀号 | 一句话口径 |
| --- | --- | --- |
| 质检双闸 | 98 | 回答质量的机器质检+人工复核双闸——现为负反馈进复审队列（39 刀 unverify）单闸 |
| 内容成片 | 98b | 直播内容自动成片（切片→成片→分发）——现为切片+洗帧两段人工拣选 |
| MCP 活状态 | 99 | 连接层暴露受控活状态工具（订单/库存带闸）——现为四只读工具（E3 反向断言） |

---

## 幕间串场（演进链一句话）

拒答留缺口（一）→ 补口径发新版本（二）→ 全程人洗+审计（三）→ 对话回流
再沉淀（四）→ 多模态原料进场（五）→ 直播内容自循环（六）→ 媒体直达顾客
（七/八）→ 外部 Agent 只读接入（九）→ 故障时诚实降级（十）→ 问不存在的
东西也诚实（十一）→ 顾客体验连续（十二）。在途：训练血缘（97）→ 质检
（98）→ 成片（98b）→ 活状态（99）。
