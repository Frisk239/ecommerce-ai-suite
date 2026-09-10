# 近几刀

完整产品仍是 `docs/goal.md`：七块共用契约，①④⑦ 加厚主线，②③⑤⑥ 契约证人（不做模型微调，ADR 0028）。当前完成定义是 goal §6.2 面试级。推进方式是 **Slice Owner：一刀一条可验证契约**，关刀看测试/报告再排下一刀。这里只排近几刀，不是八块路线图，也不是一次铺开。

上一刀：**第 48 刀 CSAT + 反馈闭环补全**（`feat/csat-48`，closeout 见 `docs/progress/csat-closeout.md`）——roadmap v3 第二梯队末项（迁移 **0024**）：会话级 `session_ratings`（一会话一评）+ `POST /api/customer/sessions/{id}/rating`（闸序同发问；422 分值/留言、409 非 active、409 已评、并发靠唯一约束兜）；顾客页页脚常驻评分条（**点星即提交、评过即收**，不做弹窗也不等尚不存在的「顾客结束会话」事件）；**thumbs-up 补收**（40 刀 422 的反向，只记不诊）；打回素材可带理由（≤200，写进 `last_error` 详情段）；仪表加 CSAT 段（近 7 日、均分 1 位小数且**无样本为 None**、1–5 分布、最近 3 条**掩码**留言）+ 客服页 ★ 徽章。集成 856→878 passed；真浏览器验收：真问一句 → 打 4 星带手机号留言 → 库里原文、总览显示 `1********00`；thumbs-up 只记不诊；客服页 #84「★ 4」。第 47 刀可观测最小版（PR #73）、第 46 刀切片真链路（ADR 0047 + 迁移 0023）与更早各刀、审计刀 6/7/8 均已合并 main。

当前阶段：**第三阶段产品硬ening（第二梯队 46/47/48 已全部走完）**（施工权威 `docs/roadmap-product-hardening.md`）。**审计刀 8 已完成**（三路并行审计 **P0 全零**，P1×5 实修；证据 `docs/progress/audit-8-closeout.md`）。下一刀：**审计刀 9**（按「每五刀一审计」节奏，覆盖第 46–48 刀 + UI/UX 后续；第 49/50 刀若按第三梯队「对标增强」按需开则顺延）。**待 Owner 裁决**：审计刀 8 记债的四条产品面缺口（商品价 3/115、多来源在产品面不可见、工作队列首屏 183 条原始灌入、widget 不落宿主 origin）。

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

## 第 35 刀：RAG 评测尺（已交付，`feat/rag-eval-ruler`）

**路径：** 评测尺两层——CI 回归集（golden.json 13 条零改动）+动态大集（96 条四分布，直调 retrieve/compose 零 LLM 依赖，双跑逐位一致）；首份实测报告进 `docs/research/rag-eval-report.md`：recall@1 63.7%、@3 72.5%、拒答组拒答率 100%、正例误拒 2.5%、**同义改写组比正例低 14pp——第 36 刀同义词接线的 before 基线**；synonyms.py（15 对）落库（第 36 刀接线检索侧）。内置浏览器实证线上引用与评测 expect 一致（A-0245）。集成 611 passed（593→611）。证据见 `docs/progress/rag-eval-ruler-closeout.md`。

## 第 36 刀：检索同义词接线（已交付已合并，PR #45）

**路径：** goal §6.2.2 before/after 纪律首战——替换式归一 after 净负（正例 -5pp/混淆 -13.3pp）按「无提升不留」改**并集扩展**（原∪归一，ES synonym 惯例；分子只增不减）；after 全分布非降：正例/混淆零漂移、同义组 @1 56.0→60.0/@3 64.0→72.0、overall 63.7→65.0/72.5→75.0；闭包局限声明（表内 16 组自测+表外探针 0/461）。审计刀 6 P1×5 全修。内置浏览器实证「保温瓶的容量」双改写命中 A-0009。集成 617 passed。证据见 `docs/progress/synonym-wiring-closeout.md`。

## 第 37 刀：客服真 loop（已交付已合并，PR #46）

**路径：** goal §6.2.3「不是 if 链」——工具注册表（get_order_status/get_stock，TOOL 标记约定+三道校验）+步进循环 max_steps=3（快路径零 LLM 铁证保留→模型提议步[代码授权执行/被拒转人工不缺口]→检索生成照旧）；越狱两形态钉测；LLM 失败降级快路径。ADR 0043 修订 0036/0037。内置浏览器混意图实证：无单号问「订单到哪+退货政策」→诚实双答（物流无证据不编造+退货引 A-0006/A-0013）。集成 642 passed（617→642）。证据见 `docs/progress/agent-loop-closeout.md`。

## 第 38 刀：连接层协议证据（已交付已合并，PR #47）

**路径：** goal §6.2.5 答辩级证据——`mcp_smoke --evidence` 四断言（E0 register 未发布/E1 工具恰四无 publish 集合相等/E2 **未发布不进检索**[登记前后零差异+探针永不出现，分词鲁棒]/E3 活状态不暴露反向断言）结构化摘要逐行输出；pytest 版进 CI。Owner 修复默认探针资产 1→3（修订流转鲁棒）。集成 645 passed（642→645）。证据见 `docs/progress/mcp-evidence-closeout.md`。

## 第 39 刀：自进化仪表（已交付已合并，PR #48）

**路径：** 缺口热度（归一化命中 hit_count+1 不新建，列表热度降序+「被问 N 次」徽章）；保鲜（last_verified_at 发布即置+「重新验证」audit verify+90 天 stale score×0.5——null 不降权，评测基线逐位一致实证）；**0031 修订验证闸**：发布事务内 retrieve(normalized_question) 命中才 resolved——补错文档发布缺口保持 open（闭环自愈从被动再排队升为发布即验证）。迁移 0016。Owner API 全链验收（热度 2/verify 200/错文档 open 对文档 resolved）。集成 652 passed（645→652）。证据见 `docs/progress/evo-dashboard-closeout.md`。

## 第 40 刀：忠实度、反馈与两阶段写（已交付已合并，PR #49）——收官刀

**路径：** ADR 0044 四段——两阶段写（check_return_eligibility 注册表可提议+15 天窗在代码+HMAC token；create_return **不在模型注册表**只能操作者确认端点触发：验签+资格重查+幂等→orders.events 追加确认事件）；忠实度闸（coverage<0.4 且命中≤1→模板回退不调模型，评测逐位一致）；逐句引用 prompt 约束；反馈分诊（顾客「没有帮助」→幂等→citations 资产置未验证→复审队列）。Owner API 全链：资格 token→确认→进度含事件→幂等 409。集成 687 passed（652→687）。**goal §6.2 收官对账表见 closeout**。证据见 `docs/progress/fidelity-feedback-closeout.md`。

## 第 41 刀：商品可运营+目录可答（已交付，`feat/product-operable-41`）——roadmap v3 第一刀

**路径：** ADR 0045——products.price_cents+currency（写回不碰价，价格只在产品档直写+审计）；POST/PATCH+上架编辑抽屉+ProductSelect 搜索三处下拉；**目录回落**：retrieve 空命中→纯列举（**已定价**前 8 件，UX-A2 修订）/商品匹配报价（citations 恒空=工具口径）/miss 照旧拒答留缺口（去补=上新改价同问可答）；三道闸（真人/政策词/规格词）零回归。交接收口：另一执行者主体（门禁全绿+ADR 对齐）+Owner 实修三处（政策词闸/口语词表+字符类 bug/评测工件落盘）。目录问「你们卖什么的？」→「本店在售商品共 115 件…」；「退货运费多少钱」→RAG 政策正确回答。集成 738 passed（688→738）；评测 65.0/75.0 零漂移。证据见 `docs/progress/product-operable-closeout.md`。

## 审计刀 7（已交付已合并，PR #50）——审计刀 6 后五刀（36–40）

**路径：** 三路并行只读审计（设计符合性 / 评测数字底座 / Agent 契约）。**P0 零**；评测数字三方复跑逐位吻合、goal §6.2 八条终态对账无虚报、Agent 授权/越狱/两阶段安全契约成立。**P1×3 实修**：slices 36–38 节被后续刀覆盖丢失（重建）、goal 未完成行收官重校、退货词表吞政策问句（补政策排除表 + 四负例钉测）；P2×6 记债。证据见 `docs/progress/audit-7-closeout.md`。

## 第 42 刀：转人工真闭环（已交付，`feat/handoff-loop`）——roadmap v3 第二刀

**路径：** ADR 0046 + 迁移 0020（`handoff_tickets`）。意图双路径：**词表快路径插在引擎分派最前置**（转人工/找人工/要人工/人工客服/真人/投诉/举报；**不含裸「人工」**——不误伤「人工智能」）+ **模型提议第三选项**（`TOOL: handoff {}`，sentinel 先于 registry 识别，注册表仍精确三个只读工具）。**一个会话一张工单**（唯一约束 + SAVEPOINT 兜并发），工单号 `H-{id:04d}` 由主键派生（不落列、无竞态）。凡 `handoff=true` 的出口（词表/提议/提议被拒/订单/库存/退货资格/拒答）**都建单**——「已转人工」徽章背后必须有东西接住；既有各路径文案与 `REFUSAL_CONTENT` 一字未改。顾客面：显式请求回执「已记录工单 H-xxxx，工作时间 4 小时内回复。」+ 联系方式表单（姓名+留言必填、邮箱/电话可选、整表可跳过），提交填本会话工单。操作者面：pending 计数（批量 group-by，无 N+1）+「全部/待处理工单」分段 + 待处理置顶 + 结单端点（404/409）；联系方式**出口必掩**（0038）。工具失败转人工同样建单（既有文案断言全绿）。集成 780 passed；ruff 全过；前端 build 绿、lint 7/0 与基线一致；浏览器全链验收（显式转人工→H-0001→提交联系方式→操作者掩码可见→结单→待处理 2→1）。证据见 `docs/progress/handoff-loop-closeout.md`。

## 第 43 刀：操作者仪表 + 通知（已交付，`feat/ops-dashboard`）——roadmap v3 第三刀

**路径：** 新 `GET /api/stats/overview`（`routes/stats.py`，无新表无迁移）：一次请求喂三处——近 7 日逐日序列（含今天 7 个 UTC 自然日、零填充升序）、7 日标量（拒答与转人工**分列**；缺口新开按 `created_at`、解决按 `resolved_at`；引用覆盖率分母=`kind=answer` 且同时下发分子分母）、三个角标（open 缺口 / 待抽检 / 未处理工单，**0 隐藏**）、被踩资产 top 3。总览页在摘要条之后插一条 **7 日趋势条**（21 根手写 CSS 柱，无图表库）与一条**反馈汇总行**（「去重新验证」深链到 `/platform/assets/{id}?verify=1` → 滚动+高亮、**不自动写库**）；侧栏角标随导航刷一次、无轮询。**UX-E 去卡的形态约束未被违反**（全仓 `.stat-card` 仍为 0）。集成 780→786 passed；ruff 全过；前端 build 绿、lint 7/0。浏览器验收：端点数值与库内独立盘点逐项吻合、角标不闪、同路由只发一次取数。证据见 `docs/progress/ops-dashboard-closeout.md`。

## 第 44 刀：退货确认迁移订单状态（已交付，`feat/return-status`）——roadmap v3 第四刀

**路径：** 纸糊#3——确认退货后 `orders.status` 纹丝不动，追问一句「退货进度怎么样」就答回「已发货」而穿帮。本刀：①`services/order_tools.py` 立订单状态合法值集 `ORDER_STATUSES`（已发货/运输中/已签收/**退货中**/**已退款**）与 `RETURNABLE_STATUSES`（前三个），作为单一来源（`models.py` 注释指向它）；②`return_tools.confirm_return` 在同一事务里既追加事件、又把状态**真迁移到「退货中」**；③**状态闸**：只有前三个可发起退货，已是退货中/已退款 → 409，且**闸在任何写入之前 return**（拒绝即不写）；④判定顺序 `duplicate` 先于 `status_not_returnable`（重复确认文案不变）；⑤顺手把「确认退货」从一击即写补成 **ConfirmDialog 确认**（与发布/结单同形）。**无新表无迁移**。钉测：确认前「已发货」→ 确认后库内「退货中」+ 进度问句答「当前状态：退货中」+ 二次确认 409；「已退款」单被 409 挡下且状态与事件都没动；状态集自检（种子值 ⊆ 合法集）。集成 786→788 passed；ruff 全过；前端 build 绿、lint 7/0；浏览器全链（SO-1002 运输中→退货中；取消不写库）。证据见 `docs/progress/return-status-closeout.md`。

## 第 45 刀（45a 段）：顾客令牌 TTL（已交付，`feat/token-ttl`）——第 45 刀拆两刀走

**路径：** 令牌此前**永不过期**（签发处 docstring 自陈「不做过期/刷新/吊销（本刀 Out）」，审计刀 2 起在案）——泄露即永久可写会话。本刀：①新设置 `customer_token_ttl_seconds`（默认 **24h**，与操作者 cookie 的 7 天分开）；②迁移 **0021** 加 `service_sessions.customer_token_expires_at`，**迁移内回填**存量有令牌会话为 `created_at + 24h`（操作者预览会话保持 NULL）；③鉴权抽成**单一出处** `_authorize_customer_session`（存在 + 恒定时间比对 + 未过期）——三处调用点（发问/反馈/留联系方式）此前各抄一份，现全部收敛；④**过期与无效同 401 同文案同响应头**；⑤`expires_at` 为 NULL 视为不可用（严格，不给静默放行口子）。钉测 7 例（签发带 TTL / 有效可用 / 过期 401 且与无效逐字同文案 / 未来过期仍可用（证明不是一律 401）/ NULL 不可用 / 三处端点一致 / 跨会话越权回归）；迁移回填在演示库实测 38 行全填、0 漏、操作者预览行未误填（CI 空库无法复现该路径，如实记录）。集成 788→795 passed；ruff 全过。**45b 段（嵌入 widget：embed.js + /widget + origin 白名单）待做**，之后审计刀 8。证据见 `docs/progress/token-ttl-closeout.md`。

## 第 45 刀（45b 段）：可嵌入客服小组件（已交付，`feat/embed-widget`）——第 45 刀至此走完

**路径：** 顾客通道此前只能从治理台的 `/customer` 路由进——**商家没有任何办法把它挂到自己店上**。本刀：①`apps/web/public/embed.js` 原生单文件加载器（Shadow DOM 圆形启动钮、首次点按才注入 iframe、postMessage 开关、幂等；零依赖零构建）；②`/widget` 路由复用 `<CustomerPage embed />`（同一份客服逻辑，收窄布局）；③**来源闸** `WIDGET_ALLOWED_ORIGINS`（空=未启用；被嵌入的页面建会话时带宿主来源，**不在白名单一律 403**；闸在限流之前）；④**访客 id** = 宿主域第一方 localStorage uuid → 落 `service_sessions.visitor_id`（迁移 **0022**）→ 操作者客服页显示「访客 xxxxxxxx」；⑤演示宿主页 `apps/web/public/embed-demo.html` + README 的一行嵌入代码与 **CSP 放行清单**（`script-src`/`frame-src`/`style-src unsafe-inline`）。

**评审揪出两个真 P1 并实修**：①「唯一闸」名不副实——原先任何站点直接 iframe `/customer`（不带该头）即可免费嵌入 → 改为**前端把「被框住」(`self!==top`) 作为嵌入判定**（不只看 `/widget`），且宿主 `no-referrer` 剥掉来源时**拒绝建会话**（fail-closed）而非静默降级；②闸序与注释矛盾（放在限流之后）→ 提到限流之前。P2 一并修：独立访问不再收访客头、加载器幂等、README 补 `style-src` 与 no-referrer 注意。

验收（真浏览器端到端）：演示宿主页 → 启动钮 → iframe(`/widget?visitor=…`) → 宿主 localStorage 有 uuid → widget 内「开始咨询」建会话成功 → 问答带引用（500ml · A-0009）→「收起」postMessage 生效；**拒绝路径**：白名单换掉后同路径 403「来源 … 未获授权嵌入」，无输入框。后端钉测 7 例。集成 795→802 passed；ruff 全过；前端 build 绿、lint 7/0。证据见 `docs/progress/embed-widget-closeout.md`。

**第 45 刀（45a+45b）至此走完 → 下一刀按节奏开「审计刀 8」**（第 45 刀后）。

## 审计刀 8（已交付，`feat/audit-8`）——审计刀 7 后五刀（第 41–45 刀 + UI/UX 六包，迁移 0018–0022）

**路径：** 三路独立只读子代理（设计符合性 / 技术债 / 功能缺口）。**P0 全零**；主干契约（价格+目录回落、退货真迁移、令牌 TTL、转人工工单、仪表聚合、嵌入 widget）均真实现且有钉测。**P1×5 实修**：①顾客面剥掉两阶段写的 `confirmation_token`（写动作凭证曾随 SSE 下发给顾客，破 ADR 0044 边界；`customer_safe_tool` 按通道裁剪、库内不动、两侧有钉测）②`_first_question`（对话资产 title，随 MCP/导出外流）从 `redact` 改 `redact_contact`（堵带分隔手机号/短域邮箱漏掩）③令牌过期死路 → 顾客页给「已过期 + 重新开始」并锁 composer ④缺口「去补文档」不再静默开修订（先选「开修订 / 另起新文档」）⑤订正「A5b 全部落地」的超额声明（商品猜测未做）。P2 顺手清：`.env.example`/README 补两个新 env、`triage_asset_ids` 下沉服务层（消 route→route 导入回潮）、widget 来源大小写归一、`confirm_return` 补行锁、两页 Esc 收敛 `useEscapeClose`、8 处文档漂移。**记债**（未修，含 4 条会被人当场 grill 的产品面缺口：**商品价只覆盖 3/115**、多来源在产品面不可见、工作队列首屏 183 条原始灌入、widget 不落宿主 origin）。集成 802→804 passed；ruff 全过；前端 build 绿、lint 7/0。**下一刀=第 46 刀直播切片真链路**。

## 第 46 刀：直播切片真链路（已交付，`feat/real-clips`）——roadmap v3 第二梯队第二项

**路径：** 纸糊#2——切片登记出的「视频资产」字节其实是**一段时间码文本**（0039 自陈「非 mp4」），演示故事停在「文本冒充视频」。本刀（**ADR 0047 + 迁移 0023**）：①新表 `clip_recordings`（`label`/`object_key`（`recordings/` 前缀）/`size_bytes`）+ `clip_candidates.recording_id` nullable FK——**源录像不是中台对象**（不进检索、不能发布、不进治理台、无 MCP 触点），只是拣选时 ffmpeg 的输入源与溯源锚；②`POST /api/clips/recordings` 上传 .mp4（扩展名 422/超 200MB 413/空文件 422），**上传即把「`recording_id IS NULL` 且 pending」的候选绑上**（已登记不改、已绑不改，单源模型）；③拣选**真切**：`ffmpeg -ss <start> -i <src> -t <dur> -c copy -movflags +faststart`（临时文件进出、120s 超时、`-ss` 前置），**切失败 422 且候选保持 pending**（不落半个资产，前序候选逐条 commit 保住回执锚）；④对象键扩展名**跟字节走**（`make_object_key(..., suffix=)`：真路径 `.mp4`、旧路径 `.txt`）；⑤**video 正文改由 `transcript` 字段承载**：登记时预置该字段（`source=machine`），`index_chunks_for_version` 对 video **只从字段取正文、永不读对象字节**（mp4 二进制不进切块/机洗）,字段缺失即正文为空、照常可发布；旧路径同样预置字段，检索能力不回归。前端：页头「上传源录像」+ 绑定回执 +「当前源录像」行 + 拣选回执按绑定状态分三支（全真切/混合/全旧路径）。Dockerfile 装 ffmpeg（CI ubuntu-latest 自带）。

验收（真浏览器 + 演示库）：上传 30 分钟真 mp4 → 回执「已绑定 30 条待拣候选」→ 拣选 C-0004/C-0006 → 回执「均已从源录像切出真 mp4 片段」→ 资产 264/265 键 `.mp4`、字节 `ftypisom`、**ffprobe 时长 47.000s / 39.000s 与时窗逐秒对齐**、transcript 字段在 → 解绑一条走旧路径 → 资产 266 键 `.txt` + 时间码文本字节 → 资产 264 浏览器发布成功 → `retrieval_chunks` 一条块 = 转写原文（发布不读二进制、不报错）。**两轴独立评审报 1 个 P0 + 3 个 P1 并全部实修**：**P0**=已发布切片会让 MCP `export_published`/`get_asset`/版本正文端点整体报错（`read_version_text` 解不出 mp4 二进制）→ 只对 video 回落 transcript 字段（复核：导出 200 / 56 块 / 264 的 content=转写；正文端点 409→200）；**P1**=ffmpeg 跑在 DB 事务里（真切前 commit）、上传先全量读入再判 200MB（改有界读）、治理台对 video 仍开放「上传新正文」（会写 `.mp4` 键装文本并清空转写 → 409 + 前端隐藏）；另修 3 条 P2、记债 4 条（ADR 0047 Debt 段）。**intake 裁决 6 订正**（`-ss` 越过 EOF 不报错，越界硬闸需 ffprobe，留 Out）。集成 804→816 passed；ruff 全过；前端 build 绿、lint 7/0；大集评测零漂移（65.0/75.0，结构性无 video 入集）。证据见 `docs/progress/real-clips-closeout.md`。

## 第 47 刀：可观测最小版（已交付，`feat/observability-47`）——roadmap v3 第二梯队第三项

**路径：** 产品自称「可被追溯」，运行时却完全不可观测——没有指标端点、没有结构化日志、没有请求关联 id；「拒答率多少 / 首字多慢 / 模板回退占几成」只能翻库手数。本刀（**无新表无迁移**；新依赖 `prometheus-fastapi-instrumentator` + `structlog`）：①`observability.configure_logging` 用 structlog JSON 接管 stdlib（**既有 12 处 `logging.getLogger(...)` 调用一行未改**即变 JSON 行，带 `correlation_id`；`LOG_LEVEL` 可调）；②`CorrelationIdMiddleware` **纯 ASGI**（`BaseHTTPMiddleware` 会给 SSE 再包一层 anyio 流——顾客主路径就是 SSE）：接受合规 `X-Request-Id`（8–64 位 `[A-Za-z0-9._-]`，否则丢弃重生成）→ contextvar → 响应头回显；③instrumentator HTTP RED（`/metrics`、`/health` 排除；`should_exclude_streaming_duration=True` 让 SSE 只计到响应首字节）；④三个自定义，**标签值全为有限集合**：`chat_requests_total{channel,kind,generated}`（`generated=false` 即模板/工具回答——顺手把「闸回退率」从记债变成可观测量）、`ttft_seconds`（请求进入中间件 → 厂商首个增量，只记生成路径）、`llm_tokens_total{direction,model}`（厂商 usage，缺了不记、不用字数估算冒充）；⑤`/metrics` 走新设置 `METRICS_TOKEN`（**空 = 一律 401** fail-closed，常量时间比对，不收 cookie），端点由 `.expose(dependencies=[...])` 注册；⑥compose 可选 `prometheus`（`metrics` profile，默认不启）+ `ops/prometheus.yml`；⑦`migrations/env.py` 加 `configure_logger` 闸——程序化迁移不再让 alembic 的 `fileConfig` 顶掉 JSON 配置（CLI 直跑不变）。

验收（真容器 + 真浏览器）：`/metrics` 无/错 token 401、对 token 200；连打数接口后 `/metrics` 里**没有** `/health`、`/metrics` 样本；浏览器顾客真问「保温杯的净含量是多少」→ 真答 500ml + 引用 `A-0009 · v1` → `chat_requests_total{channel="customer",generated="true",kind="answer"} 1`、`ttft_seconds_sum 8.38`、`llm_tokens_total` 输入 455 / 输出 331；`X-Request-Id` 合规原样回显、脏值换 32 位 hex；容器日志同请求链（httpx→网关→uvicorn.access 三行）共用一个 `correlation_id`；启动首屏 JSON 含 alembic 迁移行（证明 `fileConfig` 那条路被堵住）。**两轴独立评审 P0 无 + P1×5 全修**（畸形 usage 会把「降级」变 500／token 接线删掉也全绿／customer 与 handoff 两条记账无测试／correlation_id 进日志无钉子／令牌文档与事实不符），并顺手堵掉 instrumentator「同名指标已存在即静默放弃全部 RED」的多实例陷阱（改每 app 一份 registry）。集成 816→856 passed；ruff 全过；前端未改（build 绿、lint 7/0 回归确认）。**可选抓取也真跑通了**：`--profile metrics up` 后 Prometheus 目标 `suite-api` health=up、查询返回我们的自定义指标（令牌走文件——实测 Prometheus 不展开配置里的 `${VAR}`，intake 裁决 10 已订正）。证据见 `docs/progress/observability-closeout.md`。

## 第 48 刀：CSAT + 反馈闭环补全（已交付，`feat/csat-48`）——roadmap v3 第二梯队末项

**路径：** 反馈面只有半边且是哑的——顾客只有「没有帮助」（thumbs-down），**thumbs-up 是 40 刀明文 422**；打回素材不记理由；仪表没有满意度口径。本刀（**迁移 0024**）：①新表 `session_ratings`（`session_id` **唯一**、`score` 1–5、`comment`、`created_at`）+ `POST /api/customer/sessions/{id}/rating`（闸序同发问：IP 闸 → 401 → 409 非 active → 422 分值/留言超长 → **409 已评**；并发双提交靠唯一约束兜成 409）；②顾客页页脚**常驻评分条**（1–5 星 + 可选留言，**点星即提交、评过即收**）——**订正 roadmap 字面**：本仓顾客通道没有「顾客关闭会话」事件，等它出现等于永远不出现，故不做弹窗（回退点写在 intake 裁决 2）；③**thumbs-up 补收**：`helpful=true` 记档但**不分诊**（正反馈不是「证据要复审」），`false` 仍逐 citation 撤销验证；④打回素材收可选 `reason`（≤200，超长 422；不传 body 仍可打回）→ 写进 `last_error` 详情段（复用既有列与展示口径）；⑤仪表 `csat` 段（近 7 日、均分 1 位小数且**无样本为 None 不返回 0**、1–5 分布、最近 3 条**先掩后截**的留言）+ 总览一条 panel 行（无图表库、无 `.stat-card`）+ 客服页会话行 ★ 徽章（批量 group-by，不 N+1）。**低分不联动任何写动作**（不撤销验证、不建缺口/工单——CSAT 可能因为物流慢，要复审走 thumbs-down）。

验收（真浏览器 + 真容器）：顾客真问一句 → 填「客服很快，回电 13800138000」点 4 星 → 「谢谢反馈：你给这次服务打了 4 星」；`session_ratings` 库内**原文**、总览显示掩码 `1********00`；thumbs-up → 「已反馈 · 感谢」（库里 `helpful=true`、分诊为空）；客服页 #84「顾客 ★ 4」；素材抽屉「打回」展开理由输入（未真打回）。**两轴独立评审 P0×2 + P1×2 全修**：换会话不重置评分态（新会话永远评不了分、旧分还安到新会话头上）／打回理由跨任务残留（A 的理由会写进 B 的失败原因）／掩码顺序的钉子原是伪钉（号码在截断线内，两种顺序同输出）／端到端证据在 closeout 落盘前缺失；另统一越界分口径、均分改四舍五入、IntegrityError 只翻译唯一约束冲突等 6 条 P2。集成 856→886 passed（新增 30 例：纯函数 + 集成 + 评审补钉）；ruff 全过；前端 build 绿、lint 7/0。证据见 `docs/progress/csat-closeout.md`。
