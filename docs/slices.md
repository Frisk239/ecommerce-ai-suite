# 近几刀

完整产品仍是 `docs/goal.md`：七块共用契约，①④⑦ 加厚主线，②③⑤⑥ 契约证人（不做模型微调，ADR 0028）。当前完成定义是 goal §6.2 面试级。推进方式是 **Slice Owner：一刀一条可验证契约**，关刀看测试/报告再排下一刀。这里只排近几刀，不是八块路线图，也不是一次铺开。

上一刀：**第 34 刀 CI 门禁**（`feat/ci-gate-final`，closeout 见 `docs/progress/ci-gate-closeout.md`）：双 job（ruff/npm 与 pgvector 全量测试，**skip-green 显式失败**）+meta 测试钉门禁；带 DB 全量从本地习惯变合并必绿（goal §6.2.2 工程信用前提）。第 33 刀数据契约+交接修复已合并（PR #41：两轴评审重跑 P1×2 实修/goal §6.1 原文恢复+§6.2 签核/审计裁决落地）。

当前阶段：**面试级**（goal §6.2；施工 `docs/roadmap.md`）。下一刀：**第 35 刀 RAG 评测尺**（golden 四分布+recall@k+拒答率报告）→ **审计刀 6（第 35 刀后，三路审 26–35）** → 36 同义词 → 37 客服真 loop。

## 怎么切

- 一刀 = 一条可验证的契约路径（测试/报告能钉，需要时操作者也能走完）。该路径用到的层同一刀齐。浏览器点穿是工程验收，不是面试交付物。
- **产品阶段先于工程第 1 刀**：纯前端 mock，锚定视觉和 UX；用浏览器点穿。
- **工程第 1 刀例外：脚手架**（薄前驱）。仓库能跑起来，业务路径在第 2 刀。短对齐里必须点名第 2 刀候选，避免脚手架变成「为以后搭平台」。
- 不做横向铺八个空入口。
- 关刀后根据债务和演示缺口重排，不在开干前锁死第 4 刀以后。

## 产品阶段（已验收）

施工单：`docs/product-phase.md`。交付：`prototype/` 可点击控制台，功能 mock，浏览器验收清单全部点过。Intake 有条件通过（2026-09-06）；交互与状态机冻结，mock 不冻结。

## 工程第 1 刀：脚手架（已合并 main，PR #1）

技术栈见 `docs/adr/0011-fastapi-vite-react.md`。

**路径：** 开发者 `compose up` 后，打开控制台壳、打到健康检查、跑通一次测试。已验收：三容器绿、`/health` 200 connected、浏览器健康卡双绿、pytest 13 passed（证据见 `docs/progress/scaffold-impl-closeout.md`）。

**Must：** 单仓布局、API、控制台壳、Postgres、对象存储本地适配器、环境变量样例、一条冒烟。

**Out：** 商品/资产业务、客服、MCP 工具、八个模块入口。

## 第 2 刀：治理发布写回（已交付已合并）

候选 A。登录 + 第一批表（ADR 0022）+ 上传登记（0013）+ 机洗（弃权/防过抽取）+ 发布闸门（0019 两类缺项）+ 单事务发布写回（0005/0006/0010）+ 治理台 UI。Owner 浏览器点穿验收，集成测试 78 passed。证据见 `docs/progress/governance-publish-closeout.md`。

## 第 3 刀：客服引用（已交付）

候选 B。检索索引（0017，发布事务切块+词法打分）+ SSE 会话引擎（0021 预览面，UX-NOTES §四冻结状态机）+ 拒答转人工（0018）+ 引用带版本锚定（0007）+ 会话回流登记对话资产（0013）。Owner 浏览器点穿闭环（问→引用→拒答→回流→发布→再问命中），集成 106 passed。证据见 `docs/progress/service-citation-closeout.md`。

## 第 4 刀：知识缺口闭环（已交付，`feat/knowledge-gap` 待 PR）

**路径：** 拒答 → 治理台待办 → 登记补文档（可带缺口 ID）→ 发布事务内缺口 resolved → 同一问法再问能答。已验收：浏览器点穿全闭环（拒答 G-0001 → 补文档 → 发布 → 缺口已解决 → 再问命中 `A-0002 · v1`）；集成 125 passed。证据见 `docs/progress/knowledge-gap-closeout.md`。

**Must：** `knowledge_gaps` 表 + `assets.source_kind`（0030）；无证据拒答才建缺口；同问法精确幂等；发布才关闭（0031）；工程 UI 能看见待办和来源种类。

**Out：** 修订关缺口、MCP、厂商 Chat API、顾客通道、微调。

## 第 5 刀：MCP 只读已发布（已交付已合并，PR #7）

**MCP 只读已发布**（ADR 0001/0020/0032）：同一 FastAPI 进程 `/mcp/` Streamable HTTP；`MCP_BEARER_TOKEN` 空则全部 401，不复用操作者 cookie。工具：`search_published` / `get_asset` / `register_asset`（正文必填，来源=mcp_registered）/ `export_published`（元数据+该版正文）。无 publish。官方 MCP Python SDK。README 给 Cursor `mcp.json`。检索索引已就绪（0017）。

## 第 6 刀：修订流 + 回滚（已交付，`feat/revision-rollback` 待 PR）

**路径：** 已发布资产开修订（新待人洗版，线上指针不动）→ 人洗 → 发布 vN（指针前移，客服/MCP 跟新版）→ 可回滚到曾发布过的旧版（单独移指针，audit rollback）。有已发布规格的缺口，去补默认开该资产修订（0031）。证据见 `docs/progress/revision-rollback-closeout.md`。

**Must：** 部分唯一一个未发布版；status 保持 published；列表已发布=指针非空（修订中双徽章）；回滚不复用 publish。

**Out：** 厂商 Chat API、顾客通道、新 MCP 工具、放弃修订、第四态。

## 第 7 刀：厂商生成（已交付已合并，PR #9 第二段）

**路径：** 客服检索命中 → 厂商模型流式生成（openai 官方包，20s/0 重试）→ 完整落库后 SSE 流式；citations 恒服务端定（0007）；无证据不调模型（0018）；LLM 失败/空产出降级证据模板 +「模板回退」徽章（`complete.fallback`）。Owner 验收揪出 opencode 网关 `x-opencode-session` 硬要求（此前「真模型」实为静默降级模板）。证据见 `docs/progress/vendor-llm-closeout.md`。

**Must：** 密钥只在 `.env`（0033，测试强制空 key）；prompt 证据上限=引用上限（0007 回放口径）；断连=完整落库契约不破。

**Out：** 顾客通道（第 8 刀已做）、多轮记忆、工具调用、评测集、真·逐 token 透传（首字延迟裁决再评）。

## 第 8 刀：顾客通道（已交付已合并，PR #10）

**路径：** `/customer` 无登录签发会话令牌（Bearer，compare_digest）→ 同一引擎（chat_engine 抽取，操作者/顾客同调）流式回答+只读引用芯片；complete 不带 gap_id（载荷白名单）；操作者客服页见「顾客」徽章会话并独占回流登记；登记后顾客再问 409；三道闸限流（会话发问 10/60s、IP 发问 30/60s、IP 建会话 5/60s）429+Retry-After。迁移 0005 `customer_token`（映射 0021/0023，未新开 ADR）。证据见 `docs/progress/customer-channel-closeout.md`。

**Must：** 顾客不登录不用操作者 cookie（0021/0033，fetch credentials:omit）；顾客端点无 GET/列表/回流（0021 锁死）；操作者侧既有测试零改动（引擎抽取零行为漂移）。

**Out：** 顾客账号、多轮记忆、令牌过期/吊销 UI、分布式限流、XFF 信任模式可配置（安全面议题进下一刀）。

## 第 9 刀：数据接入 CSV 批量导入（已交付已合并，PR #12）

**路径：** 资产页登记抽屉「批量导入」页签 → CSV（title,content，utf-8-sig）→ 预览计数（后端为准）→ 逐行独立登记（复用 register_asset：0013 字节、kind=document、不挂商品、source_kind=upload 不动 0025 枚举）→ 报告 created/skipped 带行号 → 人洗发布 → 客服/MCP 可答可引。2MB/200 行/200KB 单行三重防线。冷启动死结（空库→全拒答→无回流）有了批量入口。证据见 `docs/progress/data-ingest-closeout.md`。

**Must：** source_kind=upload（CSV=上传通道的批量形态）；逐行 commit 部分成功不回滚（登记不是发布）；报告口径诚实（机洗失败留在已接入）。

**Out：** URL 导入/网站同步、同步-断链语义、挂商品列、增量去重、导入历史。

## 第 10 刀：顾客通道安全面收口（已交付已合并，PR #13）

**路径：** 三项安全语义收口（零新功能面）——XFF 信任模式（`CUSTOMER_TRUST_PROXY` 默认直连 fail-closed，忽略自报头；反代模式才信第一跳）；ask 闸序重排（IP 闸前置省 DB → 401 统一 → 会话闸后置，无效令牌不再替真顾客耗配额）；会话不存在与令牌无效统一 401（自增 id 不可探测）。证据见 `docs/progress/security-hardening-closeout.md`。

**Must：** 顾客正常问答零变化（回归）；闸序「狂刷不碰库」裁决保持（IP 闸仍前置）。

## 审计刀 2（已交付已合并，PR #14）

三路子代理（领域对账/安全密钥/工程债务）审 `7e41a3d..main` 五刀：**P0 零**（密钥纪律全链通过、领域零硬偏离、四项工程嫌疑逐一排除）；P1 聚成一簇——**「单操作者无并发」前提在顾客公开面后失效**（LLM 持连接 20s×池 15、SSE 慢连接钉池、import_csv 冻结事件循环、缺口幂等无索引、login 无限流、limiter 字典无界、retrieve 无序截断）。ADR 0030 回写 gap_id 通道分叉。证据见 `docs/progress/audit-2-closeout.md`。Intake 通过：`docs/progress/audit-2-intake.md`。

## 第 11 刀：并发收口（已交付已合并，PR #15）

**路径：** 演示并发安全——并发顾客提问 / 并发拒答同问 / 导入不再冻事件循环。零新功能面。LLM 等待期不持 DB 事务；ask 路由 SSE 返回前释放 session；`import_csv` 同步进线程池；缺口部分唯一索引 + IntegrityError 兜底（映射 0024/0030，不新开 ADR）；login IP 闸 10/60s；limiter 空 key 清扫；retrieve 按 id 排序再截断。证据见 `docs/progress/concurrency-hardening-closeout.md`。

**Must：** 既有问答事件序/文案/状态码零变化（login 429 是新路径）；不扩连接池。

## 第 12 刀：回流增强（已交付已合并，PR #16）

**路径：** 操作者回流有问有答的会话 → 登记请求内 LLM 从转写抽 QA 草稿（ADR 0035：`qa_pairs` = 对话资产机洗的结构化字段，值是数组，弃权单形状）→ 治理台详情页见转写正文（版本 text 端点）+ QA 编辑器（改/删/增后确认，空数组=确认没有）→ 发布 confirmed QA 对成块「问：…/答：…」入检索 → 再问同问法命中并引用对话资产版本。空 key 降级弃权（既有回流测试零改动）；LLM 坏输出停已接入走重试。评审修真 bug：LLM 分派按 kind 不按字段名（撞名 `qa_pairs` 的 document 在 async 路由会误触 asyncio.run）。集成 237 passed（基线 206 → 237）。证据见 `docs/progress/reflow-qa-closeout.md`。

**Must：** 未确认草稿不进索引；转写按轮切块不变；dialogue 无必填闸门不变；Out：聚组聚类、PII 打码、必填闸门、独立 QA 资产（ADR 0035 已拒）、修订/MCP/顾客面改动。

## 第 13 刀：订单工具（已交付已合并，PR #17）

**路径：** 顾客/操作者问「我的订单 SO-1001 到哪了？」→ 命中订单号模式（分派在代码不在 prompt，ADR 0036）→ 灰底 mono 工具条 `get_order_status(SO-1001) → 已发货 · 2 个物流事件` → 模板组装回答（不调 LLM、citations 恒空）；查无 SO-9999/故障 → kind="handoff" 转人工（交接摘要、不检索、**不产生缺口**——0018/0024 契约落地）；非订单问题 commit 序列/事件序零漂移。迁移 0007 orders 表（工具数据源不升格）+消息 tool 落库回放；两通道同形状。集成 259 passed（基线 237 → 259）。证据见 `docs/progress/order-tools-closeout.md`。

**Must：** 既有测试零改动绿（尤其 commit 序列契约）；Out：库存工具、email+zip、写操作、坐席队列、LLM 组织语言、MCP 订单、订单 CRUD。

## 第 14 刀：库存工具（已交付已合并，PR #18）

**路径：** 顾客/操作者问「钛钢保温杯有货吗？」→ 命中库存词表 → 工具条 `get_stock(钛钢保温杯) → 有货 · 42 件` → 模板回答（不调 LLM、citations 恒空）；「瓶装水有货吗」→「暂时无货」（0 件=事实回答）；「小龙虾有货吗」→ 商品未命中转人工（不检索不缺口）；规格/订单问题零漂移（分派序=订单号→库存→检索）。迁移 0008 products.stock（商品 mock 字段，种子仅 NULL 回填不覆盖手改）；LCS ≥2 字商品匹配纯函数。集成 301 passed（基线 259 → 301）。证据见 `docs/progress/stock-tool-closeout.md`。

**Must：** _Avoid_「用规格文档答有没有货」闭环（钉测试）；既有测试零改动；Out：库存写操作、预警、别名表、分词库、多仓、价格。

## 第 15 刀：评测集（已交付已合并，PR #19）

**路径：** 开发者跑评测集测试 → runner 读 `apps/api/evals/golden.json` 13 条 case（引用/拒答/订单/库存/三个 policy edge）直调 run_ask 逐条断言 → 全绿=回归防线；加 case 只改 JSON。ADR 0027 落地：标题锚不锚 id（测试库 id 漂移）、空 key 可复现（不比模型文案）、不建表不做台。产品代码零改动。316 passed（基线 301 → 316）。证据见 `docs/progress/eval-set-closeout.md`。

**Out：** 评测台 UI/API、评测集入表、微调对比、批量报表、真实 LLM 评分、CI workflow 文件（留部署刀）。

## 审计刀 3（已交付，`feat/audit-3`）

三路子代理（设计符合性/技术债/功能缺口）审 `82353cc..main` 五刀：**P0 零**；P1 簇=跨循环 LLM 客户端、回流登记持连接、词表误伤归宿不成立、打码外流、QA 截断丢失、工具吞异常无日志、素材/切片/考核缺席（3.5/7 能力块）。还债排期进 closeout（第 16 工程还债刀 → 17 素材 → 18 切片 → 19 考核 → 20 血缘）。证据见 `docs/progress/audit-3-closeout.md`。

## 第 16 刀：工程还债刀（已交付，`feat/debt-1`）

**路径：** 审计刀 3 P1 六项——LLM 客户端按事件循环懒建（混跑雷拆除，钉测）；回流登记/retry 的 LLM 等待不持事务（对齐刀 11 纪律，双路钉测）；库存词表收窄为 `有货|没货|无货|缺货`+商品双前置（误伤回检索拒答留缺口，已发布库存政策不再被吞，ADR 0037 修订）；发布切块 QA 块保位（先于正文，截断先丢转写）；工具吞异常 5 处 logger.exception；0027/词条回写+P2 注释三条。零新功能面。集成 327 passed（基线 316 → 327）。证据见 `docs/progress/debt-1-closeout.md`。

**Must：** 「钛钢保温杯有货吗」仍走工具；既有断言更新=对称收紧非放松；Out：打码（进 17 刀）、共享缝（第三工具时）。

## 第 17 刀：素材中心（已交付，`feat/material-center`）

**路径：** 操作者打开素材中心 → 选「钛钢保温杯」生成卖点文案（同步 ≤20s，真 LLM）→ 规则质检过线落**待抽检** → 详情抽屉预览 → 「抽检通过 · 登记为资产」→ A-xxxx（kind=material、来源=素材生成、已接入→机洗→待人洗）→ **未确认字段直接发布**（闸门 kind 条件修复：词条「素材没有规格必填」，第 2 刀以来潜伏偏离被本刀激活后修复）→ 客服问素材正文原词命中引用 `A-0010 · v1`。打回→失败(人工打回)→重试→再通过。任务五态机（ADR 0038：「可重试」是属性）；机洗打码步（prompt 输入/qa_pairs 值/文档字段值三接入点，版本字节不动钉断言）。前端建任务 30s 超时（Owner 揪出 15s 坑）。集成 375 passed（基线 327 → 375）。证据见 `docs/progress/material-center-closeout.md`。

**Must：** 失败不进中台（0029）；任务非中台对象（0012）；Out：图片素材、模板参数、批量、队列 worker、定时、切片。

## 第 18 刀：直播切片（已交付，`feat/clip-picking`）

**路径：** 操作者打开「直播切片」→ 种子候选卡片（时间码+转写+商品标签+源录像 mock 披露）→ 勾选 2 条「拣选登记」→ 已登记 A-0011/A-0012（单向不可再选）→ 治理台待人洗（kind=视频/来源=切片拣选/对象键 clips/）→ video 无字段集直接发布 → 客服问转写关键词命中引用 `A-0011 · v1`；素材中心「切片汇入」页签纯视图。ADR 0039：v1 字节=带时间码转写文本（真视频/ASR 留部署刀）；拒绝原子性（含已登记整体 409 先于首字节）。**前置修复：CONTEXT.md 词条节被第 17 刀脚本误删（-168 行）本刀恢复**。集成 391 passed（基线 375 → 391）。证据见 `docs/progress/clip-picking-closeout.md`。

**Must：** 候选不是资产（0014）；不暗插任务（0015）；Out：自动切出/ffmpeg/ASR/撤销/源录像页/MCP。

## 第 19 刀：销售考核（已交付，`feat/coaching`）

**路径：** 操作者打开「销售考核」→ 题库动态推导（已发布对话的 confirmed qa_pairs 逐对成题，弃权转写首问兜底；题源锚 `A-xxxx · vN`）→ 作答抽屉单轮话术 → LLM 三维 rubric 打分（口径准确 40/证据贴合 30/服务语气 30，无降级：空 key/失败=未评分可重评）→ 三卡得分+评语+「扮演底座」快照徽章 → 记录回放。coach_records 非中台对象（0027：打的是人不是客服引擎）。`GET /api/assets?kind=` 参数。评审实修：打分未评分行先落库+双段事务（debt-1 纪律）。集成 428 passed（基线 391 → 428）。证据见 `docs/progress/coaching-closeout.md`。

**Out：** 多轮追问、语音、上岗体系、批量考试、导出、多受训者、考核入中台。

## 第 20 刀：血缘视图（已交付，`feat/lineage`）

**路径：** 资产详情「血缘」折叠面板：头部汇总「引用 N · 写回 N · 考核 N」；引用样例（问句+版本+会话+时间，JSONB containment 下推限 10）；写回（publish+rollback——0034 回滚也写回，每条带发布/回滚徽章与按版 confirmed_fields 派生的字段名）；考核（题面+版本+时间）。`GET /api/assets/{id}/lineage` 固定 5 查询；零新表零写路径（0026 派生视图）。评审实修两个 P1（回滚写回丢失+fields 派生）+发布 v2→回滚 v1 全链路集成。集成 446 passed（基线 428 → 446）。证据见 `docs/progress/lineage-closeout.md`。

**Out：** 血缘表/写路径/引用计数列/MCP 导出留痕（无留痕表记 debt）/跨资产图。

## 第 26 刀：语义收口小刀（已交付，`feat/semantic-cleanup`）

**路径：** audit-5 P1 簇清零——打码漏网四处（material 生成 prompt/ops_runs 落库含评审实修 output 面/MCP 三出口 title `_mask_title` 落库原文不动）；ops 三修（列表批取/with_for_update 行锁/mcp 首插 SAVEPOINT 兜底）；**血缘导出环拼装**（usages.exports+「导出 · MCP」中文化——0026 最后一环闭环）；gaps.question 出口掩。集成 505 passed（基线 491 → 505）。证据见 `docs/progress/semantic-cleanup-closeout.md`。

## 第 27 刀：演示收官刀（已交付，`feat/demo-finale`）——goal 7/7

**路径：** README「3 分钟口述稿」四段（0:00 接待含缺口/0:45 内容/1:30 MCP/2:20 中台核心——种子通用名例句，评审实修 500ml 对齐种子）；总览「其余能力」四行（原型冻结形状）；拒答交接摘要（REFUSAL_CONTENT 不变+追加段：问句 redact 截 60+G-xxxx；顾客通道白名单延伸到文本）；ruff format 全仓收口（46 文件纯格式，收集数 510=510）。集成 510 passed（基线 505 → 510）。证据见 `docs/progress/demo-finale-closeout.md`（含完成标准 7/7 对账表）。

## 第 28 刀：生命周期出口刀（已交付，`feat/lifecycle-exits`）

**路径：** 优化计划 P1 族三件——修订「上传新正文」换字节（新键写旧键删，机洗重跑 confirmed 保留）；「放弃修订」（删未发布版+解锁回滚/再修订，audit discard_revision）；「废弃」失败资产（仅从未发布的 ingested，discarded_at 标记+清字节，audit discard_asset；已发布 409）。迁移 0014；storage.delete 首次接线。集成 533 passed（基线 510 → 533）。证据见 `docs/progress/lifecycle-exits-closeout.md`。

## 第 29 刀：客服多轮记忆刀（已交付，`feat/multi-turn`）

**路径：** goal ①「必须有：多轮」兑现——同一会话问「钛钢保温杯的净含量」再问「那它的材质是什么」（纯代词）：代词触发检索词拼接（上问主题并入，bigram 口径不变）+最近 4 轮进生成 prompt（指代消解）→引用命中回答；拒答/转人工/工具轮不进记忆、history 过 redact、无证据多轮仍拒答（记忆不制造证据）。stream_chat 向后兼容零改动。证据见 `docs/progress/multi-turn-closeout.md`。

## 第 30 刀：治理体验小刀（已交付，`feat/gov-ux`）

**路径：** 优化计划 P2 治理体验四件——缺口问句归一化幂等（normalize_question：strip/全半角/去尾标点；迁移 0015 加列回填删重换索引——「发票怎么开具？」与「发票怎么开具」同拒答只一条 open，浏览器实证）；「去补文档」提示条（掩码问句+「发布后自动解决」）；登记标题文件名兜底；商品页库存列（操作者只读）。集成 556 passed。证据见 `docs/progress/gov-ux-closeout.md`（含优化计划执行对账）。

## 第 31 刀：多来源真实数据刀 I（已交付，`feat/real-data-1`）

**路径：** 「多来源」设计点首次兑现——Wikidata SPARQL（六类实测 QID，限速退避）拉 **91 个真实商品**幂等灌库；中文电商评论集 6.2 万条抽样 200 **走既有 CSV 批量导入 API**+发布 20；客服问「平板会不会黑屏」**引用真实评论资产 A-0028 · v1** 命中（未发布评论正确拒答——已发布过滤在真实数据上成立）。零产品代码改动（scripts/realdata/ 纯标准库）。集成 572 passed（基线 556 → 572）。证据见 `docs/progress/real-data-1-closeout.md`。

## 第 32 刀：多来源数据 II 收口（已交付，`feat/real-data-2`）

**路径：** ABCD（MIT）会话转写走既有 `register_asset(kind=dialogue, source_kind=session_backflow)`；WANDS Exact 对幂等灌 `clip_candidates`。四源四通道可讲；**停灌、无数据 III**。零产品代码。集成 583 passed（基线 572 → 583）。证据见 `docs/progress/real-data-2-closeout.md`。

## 第 33 刀：数据契约与生产级目录（已交付，`feat/data-contract`）

**路径：** Open Food Facts 公开 TSV dump 流式清洗（条码+品名+可解析净含量）→ 规格正文=源字段 → register/确认/发布写回。不编造保质期。演示库 20 条 OFF 食品净含量写回（Chrome：Chocolate n3 80g · A-0227·v1）。goal §6.2 改为生产级目录。集成 595 passed。证据见 `docs/progress/data-contract-closeout.md`。

## 第 34 刀：CI 门禁（已交付，`feat/ci-gate-final`）

**路径：** GitHub Actions 双 job——lint（ruff apps/packages/scripts+npm lint+build）与 test（pgvector/pgvector:pg16 service；`SUITE_TEST_DATABASE_URL` 必设，**refuse skip-green 显式失败**；`uv run pytest` 全量）；PR+push main 双触发；meta 测试钉 workflow 关键行防掏空；双文件同 commit（修复另一会话悬空）。证据见 `docs/progress/ci-gate-closeout.md`。

## 收官排期（audit-5）
