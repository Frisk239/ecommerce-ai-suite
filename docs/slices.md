# 近几刀

完整产品仍是 `docs/goal.md`：七块共用契约，①④⑦ 加厚主线，②③⑤⑥ 契约证人（不做模型微调，ADR 0028）。当前完成定义是 goal §6.2 面试级。推进方式是 **Slice Owner：一刀一条可验证契约**，关刀看测试/报告再排下一刀。这里只排近几刀，不是八块路线图，也不是一次铺开。

上一刀：**第 71 刀 评分可改**（`feat/rating-edit-71`，closeout 见 `docs/progress/rating-edit-closeout.md`）——Owner 裁决（完善路线批：评分可改=允许/分币种=维持拒答/默认视角=维持现状）。**迁移 0029**（session_ratings.updated_at）：已评 → UPDATE **覆盖式留最新**（score/comment 整体覆盖 + updated_at，NULL=首评未改），唯一约束不动；前端评过**不再折叠**——星星回显当前分、留言框回填、点星即再提交（「已提交 N 星，已更新，可随时修改」）；`csat_ratings_total` 转事件计数口径。浏览器实测：4 星→填留言→改 5 星，DB 单行覆盖 + updated_at。两轴评审 P1×1/P2×5 全实修（README/模块 docstring/前端类型旧口径、updated_at 统一 DB 钟、改评测试补面、conftest 按 login 先例放宽顾客闸——csat 模块 20+ 发撞 30/60s IP 闸边缘）。集成 1157→1158 passed；前端 lint 7/0、build 绿；检索金标逐位相同。审计刀 14（PR #103）已合并 main。**下一刀=第 72 刀 演示素材修复（G4/G5）**。

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

**路径：** 产品自称「可被追溯」，运行时却完全不可观测——没有指标端点、没有结构化日志、没有请求关联 id；「拒答率多少 / 首字多慢 / 模板回退占几成」只能翻库手数。本刀（**无新表无迁移**；新依赖 `prometheus-fastapi-instrumentator` + `structlog`）：①`observability.configure_logging` 用 structlog JSON 接管 stdlib（**既有 9 个模块 logger 的 `logging.getLogger(...)` 调用一行未改**即变 JSON 行，带 `correlation_id`；`LOG_LEVEL` 可调）；②`CorrelationIdMiddleware` **纯 ASGI**（`BaseHTTPMiddleware` 会给 SSE 再包一层 anyio 流——顾客主路径就是 SSE）：接受合规 `X-Request-Id`（8–64 位 `[A-Za-z0-9._-]`，否则丢弃重生成）→ contextvar → 响应头回显；③instrumentator HTTP RED（`/metrics`、`/health` 排除；`should_exclude_streaming_duration=True` 让 SSE 只计到响应首字节）；④三个自定义，**标签值全为有限集合**：`chat_requests_total{channel,kind,generated}`（`generated=false` 即模板/工具回答——顺手把「闸回退率」从记债变成可观测量）、`ttft_seconds`（请求进入中间件 → 厂商首个增量，只记生成路径）、`llm_tokens_total{direction,model}`（厂商 usage，缺了不记、不用字数估算冒充）；⑤`/metrics` 走新设置 `METRICS_TOKEN`（**空 = 一律 401** fail-closed，常量时间比对，不收 cookie），端点由 `.expose(dependencies=[...])` 注册；⑥compose 可选 `prometheus`（`metrics` profile，默认不启）+ `ops/prometheus.yml`；⑦`migrations/env.py` 加 `configure_logger` 闸——程序化迁移不再让 alembic 的 `fileConfig` 顶掉 JSON 配置（CLI 直跑不变）。

验收（真容器 + 真浏览器）：`/metrics` 无/错 token 401、对 token 200；连打数接口后 `/metrics` 里**没有** `/health`、`/metrics` 样本；浏览器顾客真问「保温杯的净含量是多少」→ 真答 500ml + 引用 `A-0009 · v1` → `chat_requests_total{channel="customer",generated="true",kind="answer"} 1`、`ttft_seconds_sum 8.38`、`llm_tokens_total` 输入 455 / 输出 331；`X-Request-Id` 合规原样回显、脏值换 32 位 hex；容器日志同请求链（httpx→网关→uvicorn.access 三行）共用一个 `correlation_id`；启动首屏 JSON 含 alembic 迁移行（证明 `fileConfig` 那条路被堵住）。**两轴独立评审 P0 无 + P1×5 全修**（畸形 usage 会把「降级」变 500／token 接线删掉也全绿／customer 与 handoff 两条记账无测试／correlation_id 进日志无钉子／令牌文档与事实不符），并顺手堵掉 instrumentator「同名指标已存在即静默放弃全部 RED」的多实例陷阱（改每 app 一份 registry）。集成 816→856 passed；ruff 全过；前端未改（build 绿、lint 7/0 回归确认）。**可选抓取也真跑通了**：`--profile metrics up` 后 Prometheus 目标 `suite-api` health=up、查询返回我们的自定义指标（令牌走文件——实测 Prometheus 不展开配置里的 `${VAR}`，intake 裁决 10 已订正）。证据见 `docs/progress/observability-closeout.md`。

## 第 48 刀：CSAT + 反馈闭环补全（已交付，`feat/csat-48`）——roadmap v3 第二梯队末项

**路径：** 反馈面只有半边且是哑的——顾客只有「没有帮助」（thumbs-down），**thumbs-up 是 40 刀明文 422**；打回素材不记理由；仪表没有满意度口径。本刀（**迁移 0024**）：①新表 `session_ratings`（`session_id` **唯一**、`score` 1–5、`comment`、`created_at`）+ `POST /api/customer/sessions/{id}/rating`（闸序同发问：IP 闸 → 401 → 409 非 active → 422 分值/留言超长 → **409 已评**；并发双提交靠唯一约束兜成 409）；②顾客页页脚**常驻评分条**（1–5 星 + 可选留言，**点星即提交、评过即收**）——**订正 roadmap 字面**：本仓顾客通道没有「顾客关闭会话」事件，等它出现等于永远不出现，故不做弹窗（回退点写在 intake 裁决 2）；③**thumbs-up 补收**：`helpful=true` 记档但**不分诊**（正反馈不是「证据要复审」），`false` 仍逐 citation 撤销验证；④打回素材收可选 `reason`（≤200，超长 422；不传 body 仍可打回）→ 写进 `last_error` 详情段（复用既有列与展示口径）；⑤仪表 `csat` 段（近 7 日、均分 1 位小数且**无样本为 None 不返回 0**、1–5 分布、最近 3 条**先掩后截**的留言）+ 总览一条 panel 行（无图表库、无 `.stat-card`）+ 客服页会话行 ★ 徽章（批量 group-by，不 N+1）。**低分不联动任何写动作**（不撤销验证、不建缺口/工单——CSAT 可能因为物流慢，要复审走 thumbs-down）。

验收（真浏览器 + 真容器）：顾客真问一句 → 填「客服很快，回电 13800138000」点 4 星 → 「谢谢反馈：你给这次服务打了 4 星」；`session_ratings` 库内**原文**、总览显示掩码 `1********00`；thumbs-up → 「已反馈 · 感谢」（库里 `helpful=true`、分诊为空）；客服页 #84「顾客 ★ 4」；素材抽屉「打回」展开理由输入（未真打回）。**两轴独立评审 P0×2 + P1×2 全修**：换会话不重置评分态（新会话永远评不了分、旧分还安到新会话头上）／打回理由跨任务残留（A 的理由会写进 B 的失败原因）／掩码顺序的钉子原是伪钉（号码在截断线内，两种顺序同输出）／端到端证据在 closeout 落盘前缺失；另统一越界分口径、均分改四舍五入、IntegrityError 只翻译唯一约束冲突等 6 条 P2。集成 856→886 passed（新增 30 例：纯函数 + 集成 + 评审补钉）；ruff 全过；前端 build 绿、lint 7/0。证据见 `docs/progress/csat-closeout.md`。


## 审计刀 9（已交付，`feat/audit-9`）——审计刀 8 后三刀（第 46–48 刀，迁移 0023–0025）

**路径：** 三路独立只读子代理（设计符合性 / 技术债与健壮性 / 功能缺口与产品面真实性），结论均要求 file:line 或命令回显。**设计轴与健壮性轴 P0 全零**；14 条载荷性声称逐条对账 13 条属实。**P0×2 实修**：①CSAT 评分条对「只有拒答/转人工」的会话硬不可达——闸门写成「有过 kind=answer 的回答」，满意度只统计满意的人（演示库 86 会话里 31 个永远看不到评分条；后端本来只要求会话 active）→ 改成「有过一次 AI 回答（任何 kind）」；②切片「上传即绑定」在演示库已是空操作且绑定**不可重绑** → README 演示前检查给出复位命令，重绑列为建议下一刀（改绑定模型属产品决定）。**P1×6 实修**：`open_revision` 漏传 key_suffix（旧路径切片开修订会写出 `.mp4` 键装文本——第 46 刀堵了 `PUT …/bytes` 漏了姊妹端点）→ 修订沿用源版本扩展名；拣选无并发闸（并发可登记出两份资产）→ 条件 UPDATE CAS 占位 + 失败放回；上传在事件循环里做 200MB 哈希/落盘 → 改同步路由走线程池；迁移 0023 未回填存量切片的 `transcript` 字段（重发布即正文归零）→ **迁移 0025** 回填；46/48 的产品动作在 47 的观测面不可见 → 加 `clip_cuts_total{result}` / `csat_ratings_total{score}` + 三处结构化日志；CI 未声明 ffmpeg → 显式安装 + skip-green 守卫。**文档 9 处漂移**一并订正（测试计数、陈旧 docstring、README 补 CSAT 与演示前检查、绝对声明改口径）。**记债**：源录像重绑/替换、审计刀 8 结转四条产品面缺口（本次复核全部仍成立，数字已更新）、切片内存/索引/口径重复等工程债。集成 886→892 passed；ruff 全过；前端 build 绿、lint 7/0；迁移 0025 演示库回填 3 行且往返可逆。证据见 `docs/progress/audit-9-closeout.md`。


## 第 49 刀：源录像可改绑 + 回执指名来源（已交付，`feat/clip-rebind-49`）——审计刀 9 P0-2 的修复刀

**路径：** 第 46 刀的绑定模型是「上传即绑『尚无源录像』的 pending 候选，已绑不改」——演示库 27 条 pending 全绑在旧录像上之后，**再上传任何新录像都绑 0 条**，而 `routes/clips.py` 只有三个端点：绑错一份录像 = 永久锁死（审计刀 9 P0-2），且 README 演示稿「先上传再拣选」照做即自相矛盾。本刀（**无新表无迁移**，**修订 ADR 0047 §2 / 第 46 刀裁决 2**）：①`POST /api/clips/recordings/{id}/bind`（`candidate_ids` 缺省 = 全部待拣；**允许改绑已绑的 pending 候选**；**已登记候选一律不动**——指定即 409 且一行不写，校验先于任何写入）；②`GET /api/clips/recordings`（created_at 降序、≤20）——没有列表就只有「刚上传的那份」可选，等于把锁死换成「只能改成最新」；③上传回执多 `bound_count`（后端 rowcount 真值，前端不再拿上传前的候选数猜）；④拣选回执**指名来源**「均已从《xxx.mp4》切出真 mp4 片段」；⑤前端页头加录像下拉 + 改绑按钮（勾了候选就只改勾选的）；⑥拣选在 CAS 占位后重读绑定并按 id 兜底回查录像（否则并发改绑下「切自哪一份」与库里不一致、或误报「源录像不存在」）。

验收（真浏览器 + 真容器）：旧录像 `acceptance-30min.mp4`（256x144）上绑着 27 条 → 上传新录像 `rebind-320x240.mp4`（320x240，7.0 MB）→ 一键「改绑全部待拣」→ 回执「已把 27 条待拣候选改绑到《rebind-320x240.mp4》」→ 拣选一条 → 资产 267 的片段分辨率 **320x240**（旧录像切出是 256x144）、`ffprobe` 时长 **39.000s** = 候选时窗 00:01:20→00:01:59——字节确实来自新录像。集成 892→903 passed（新增 11 例）；ruff 全过；前端 build 绿、lint 7/0。**两轴独立评审 P0 无 + P1×2 实修**（`refresh` 后取了预检快照会误报 422；ADR 未追加修订段）+ P2 顺手四条（回执字段溢出、空数组防御、20 上限补钉、空 label 过滤）。证据见 `docs/progress/clip-rebind-closeout.md`。


## 第 50 刀：多来源可见 + 演示价回填（已交付，`feat/source-price-50`）——审计刀 8/9 结转的两条产品面缺口

**路径：** 演示库有四份真实数据集（Wikidata 91 商品 / OFF 20 商品+20 规格资产 / 在线购物评论 200 资产 / WANDS 30 切片候选），但**四条灌入路径都没在库里记来源**——来源只活在脚本常量与标题命名里；资产侧于是 232 条全叫「上传」，商品侧连来源列都没有。同时 115 件商品只有 3 件有价（ADR 0045 只给两个种子字面量），「多少钱」与目录列举几乎无货可列。本刀（**迁移 0026**）：①`SOURCE_KINDS` 增 `review_import`（评论导入）/ `open_dataset`（开放数据集）；②`products.source_kind` 新列，按形态回填 Wikidata 91 + OFF 20 + WANDS 承载 1 → `open_dataset`、两条种子 → `seed`，**POST/PATCH 不收该字段**（来源是既成事实，可改就成可造假的溯源）；③资产按脚本写死的标题形态回填（`%评论 ·%` 200 条、`%规格（OFF）` 20 条，只动 `upload`）；④**演示价按类目基准回填**（新增单一来源常量 `seed.CATEGORY_DEMO_PRICES`，9 类目，**mock 演示价非真实售价**，只写 NULL 不覆盖手改价）→ `115/115` 有价；⑤前端加两词 + 商品卡来源 chip + 抽屉只读来源行；⑥**报价不看命中**（ADR 0045 §二修订）：`try_price_answer` 在「报价意图 + 命中商品名 + 问句主体就是『商品名+问价』（纯度闸）」时直接答实时行价——老口径要求 `retrieve == []`，而演示库 56 条已发布资产让带商品名的问句常有命中，**补齐的 115 行价永远问不出来**（实测「钛钢保温杯多少钱？」答「证据未覆盖价格」）；列举仍走空命中闸，政策/规格/要真人不受影响（实测「保温杯刻字怎么收费」不被抢答、「退货运费多少钱」照旧走 RAG）。

验收：资产来源 200/20/12（原 232 upload）、商品来源 112/2/1、价格 115/115（两条种子价未动）；顾客问「钛钢保温杯多少钱？」→ catalog 工具式「售价 129元」，「你们卖什么？」→「共 115 件，均已定价」+ 前 8 件；商品页与资产列表的来源可见。集成 909→917 passed；ruff 全过；前端 build 绿、lint 7/0；大集评测 65.0/75.0 零漂移（结构性不入回落分支），已补进评测报告。**两轴独立评审 P0×1（迁移降级会误清迁移前就存在的种子价）+ P1×2（报价抢答服务问句 / 核心改动缺真钉子）全部实修**，另修 3 条 P2。证据见 `docs/progress/source-price-closeout.md`。


## 第 51 刀：工作队列来源筛选（已交付，`feat/workqueue-51`）——审计刀 8 最后一根产品面缺口

**路径：** 审计刀 8 起记债的「工作队列首屏 183 条原始灌入」在审计刀 9 复核为 193 行（183 条 `upload`）——操作者第一屏被导入货淹掉。第 50 刀刚把来源词做出来（`review_import`/`open_dataset`），本刀把「按来源看」补上（**无新表无迁移**）：①`GET /api/assets?source_kind=`（取值受 `SOURCE_KINDS` 约束、未知 422、与既有 `status`/`kind` 可组合；控制台内仍客户端过滤，该参数是服务端能力并被接口式用例钉着）；②资产列表加**来源筛选 chips**（只列当前 tab+范围下真有的来源与条数，点选筛选、再点取消；状态进 `?source=`，与 `?status=`/`?view=` 同一套函数式 updater）；③抽 `rowsInTab` 助手让 tab 谓词单一来源（此前 `filtered` 与来源计数各抄一份）。

实测：默认「待人洗」页签 chips 显示 `全部 / 评论导入 180 / 切片拣选 6 / 会话回流 5 / 上传 3`；点「上传 3」→ URL `?source=upload` → 3 行。集成 917→919 passed；ruff 全过；前端 build 绿、lint 7/0。**评审 P0 无 + P1×3 全修**：清除入口会被门控藏掉（切到单一来源 tab 时空屏像无数据）／新过滤用例是同义反复（夹具只有一种来源，删掉后端过滤也绿 → 补「合法但不存在的来源返回空」钉子）／组合过滤用例可零数据通过（`all([])` 恒真 → 自带数据 + 断言非空）；P2 顺手六条（死代码、谓词复制、422 文案口径、a11y、chip 选中态不加粗、注释数字）。**默认视角未改**（筛选是工具不是改默认——「默认是否只显示人工来源」属 Owner 观感裁决）。证据见 `docs/progress/workqueue-source-filter-closeout.md`。


## 审计刀 10（已交付，`feat/audit-10`）——审计刀 9 后三刀（第 49–51 刀，迁移 0025–0026）

**路径：** 三路独立只读子代理（设计符合性 / 技术债与健壮性 / 功能缺口与产品面真实性）。**P0×2 实修**：①**报价纯度闸只装在两条报价路径中的一条**——`try_catalog_answer` 的空命中报价分支没有闸，实测「家具送货安装怎么收费？」（该问句 0 命中）被答成「WANDS 家具（演示）售价 899元」；第 50 刀验收没抓到只因「保温杯刻字怎么收费」恰好有命中才落到被闸的那条路。修法：三道判定（命中商品名 + 有价 + 纯度）收敛到共用 `_quote_answer`。②**演示库两份已发布退货政策打架**（asset 6「7天」vs asset 13 v3「15天无理由（升级版）」）→ 顾客答「存在两个口径」；走平台治理路径（asset 13 开修订→换正文→发布 v4）对齐，复核后只答「签收后7天内可申请退货」。**P1×6 实修**：纯度闸只认全名字串导致部分名问价（「保温杯多少钱」对「钛钢保温杯」）静默挡回 RAG → 新增 `stock_tools.lcs_fragment` 同源剔除命中片段；ADR 0045 补记纯度闸并订正「规格问由四道闸挡」的错记；ADR 0047 如实订正「多源留 Out」（bind 已让 API 层可达，UI 仍单源）；三个导入脚本补写 `source_kind`（否则重置库复现不出多来源可见）；来源筛选的「隐身过滤器」（切 tab 后无 chip 高亮 + 空态说反话）→ 选中来源恒渲染 + 空态区分筛选所致、给清除按钮；`?source=` 取值校验（未知值当未筛选）。**P2 六条顺手**（评测集 `什么` 疑问词漏项导致 `catalog-quote-new-product` 变红、`CONTEXT` 自相矛盾与来源词条补两值、WANDS 候选逐卡显示源录像标签、rebind 测试顺序独立与脏状态…）。集成 919→921 passed；ruff 全过；前端 build 绿、lint 7/0。**记债**：按类目问价仍答不出（新能力，列为下一刀首选）、四份数据在产品面只区分到「评论 vs 其余三份」、widget origin、CSAT 深链/改评、迁移判据靠形态等。证据见 `docs/progress/audit-10-closeout.md`。


## 第 52 刀：按类目问价（已交付，`feat/category-quote-52`）——修审计刀 10 首推

**路径：** 审计刀 10 实测（同一会话）：`你们卖什么？` → catalog「本店在售商品共 115 件，**均已定价**…」；紧接着 `笔记本电脑多少钱？` → **拒答**（kind=refusal、落缺口、建工单）——因为 `match_product` 只认**商品名**，类目名不是商品名（9 个类目全中招）。本刀（**无新表无迁移**）：①`catalog_tools._category_quote` + `render_category_quote`：报价意图 + 命中**类目名** + 纯度闸（扣掉类目名与问价虚词后为空）→ 答「类目共 N 件，其中已定价 M 件，均为 X 元 / 价格 X–Y 元（另有 K 件未定价）；问具体型号给你准确价」；类目价是**商品行现算的聚合事实**（不新增「类目基准价」字段）；②判定顺序：先商品名（纯问价）→ **商品名命中但纯度不足时仍试类目路径**（商品「笔记本 A」与类目「笔记本电脑」可能同时命中）→ 两条都不成立才算实质问句交回 RAG；③判定放在**共用的** `_quote_answer`——空命中回落与「有命中也要报价」两条入口都能按类目答（审计刀 10 P0 的教训：新判定只加在一条路径上等于没加）；④ADR 0045 追加「§二修订之二」。实测：「笔记本电脑多少钱？」→「共 34 件，其中已定价 34 件，均为 4999元」；「食品多少钱？」→「均为 3元」；「笔记本电脑刻字怎么收费？」**不被抢**；「无人机多少钱？」（类目不存在）照旧拒答 + 落缺口。集成 921→926 passed（新增 5 例：区间与件数模板 / 同价「均为」/ 纯度闸 / 无价与未知类目 miss / 真引擎端到端）；ruff 全过；前端未改（build/lint 7/0 回归确认）。证据见 `docs/progress/category-quote-closeout.md`。


## 第 53 刀：CSAT 留言可回溯（已交付，`feat/csat-trace-53`）——审计刀 10 的 CSAT 缺口

**路径：** 第 48 刀的 CSAT 段只给「最近 3 条掩码留言（纯文本）」——操作者看到「物流太慢」也没法回看那次对话，跟进只能去客服页逐行找 ★（审计刀 10 记债）。本刀（**无新表无迁移**）：①`csat.recent_comments` 元素从字符串变 `{session_id, score, comment}`（**契约变更**，已同刀更新唯一消费者控制台总览页）；②总览留言行前加「N 星」、后加 **`#会话号` 链接** → `/service?session=N`（客服页既有深链，纯派生、不自动写库），标题写明「点会话号回看那次对话」；③纯函数 `_build_csat` 行元组加 `session_id`（越界分/均值/分布口径不变）。实测：接口返回 `{98, 2, '答不上来，体验差'}`（掩码仍生效）+ `{84, 4, '客服很快，回电 1********00'}`；浏览器点 `#98` → `/service?session=98` 定位到该会话。集成 926→927 passed；ruff 全过；前端 build 绿、lint 7/0。**未做**：改评（要动产品语义：评分可撤销=满意度历史可修饰，留待裁决）、全量留言页。证据见 `docs/progress/csat-trace-closeout.md`。


## 第 54 刀：嵌入宿主的来源站点落库（已交付，`feat/widget-origin-54`）——审计刀 8 起最后一条产品面缺口

**路径：** 第 45b 刀的来源闸**校验**了 `X-Widget-Origin` 却**不落库**——商家可能把 widget 挂在自己**多个站点**上，只靠 `visitor_id`（宿主自己那边的 uuid）对账，看不出「这条会话来自哪个站」（审计刀 8 记债，审计刀 9/10 两次复核仍成立）。本刀（**迁移 0027**）：①`service_sessions.host_origin`（String(255) nullable）= 过闸来源的**归一值**（小写、去尾斜杠，与 `_widget_gate` 比对口径同源），建会话时落库；**独立访问为 NULL**、**存量不回填**（没有这个事实就不编造）；②`SessionOut/SessionSummary` 带该字段（列表/详情同源，无新查询），客服页会话行在访客徽章旁显示**「站点 shop.example.com」**（展示层剥协议前缀，title 给完整 origin）。实测：演示宿主页 → widget 建会话 #102 → `host_origin=http://localhost:5173` + 访客 id 齐；客服页显示「顾客 · 站点 localhost:5173 · 访客 bc1988d4」；独立访问会话两字段均 NULL；独立访问伪造 `X-Widget-Origin` → 403（闸先行）不落库；`HTTP://LOCALHOST:5173/` 归一后落库。集成 927→930 passed（新增 3 例）；ruff 全过；前端 build 绿、lint 7/0；迁移 0027 往返可逆。**至此审计刀 8/9/10 结转的四条产品面缺口全部处理完**（价与来源→第 50 刀；工作队列→第 51 刀给筛选工具、默认视角待裁决；widget origin→本刀）。证据见 `docs/progress/widget-origin-closeout.md`。


## 第 55 刀：四份数据集在产品面逐个可见（已交付，`feat/dataset-labels-55`）——修审计刀 10 P1-3

**路径：** 第 50 刀给四份真实数据集用了两个词（`review_import` / `open_dataset`）——审计刀 10 指出 **Wikidata / OpenFoodFacts / WANDS 三者在界面上同叫「开放数据集」**，看不出是哪一份。本刀（**迁移 0028**）：①`SOURCE_KINDS` += `wikidata` / `openfoodfacts` / `wands`（`open_dataset` **保留为通用类**，将来接新数据集时的兜底；本刀后库里不再有该值的行）；②迁移按与 0026 **同源、可重复**的稳定形态拆细（资产 `%规格（OFF）` → openfoodfacts；商品「WANDS 家具（演示）」→ wands、六类目 + 空 `spec_schema` → wikidata、食品 + 净含量单字段 → openfoodfacts；**只动 `source_kind='open_dataset'` 的行**，down 原样收回）；③`labels.ts` 加三词（资产/商品展示位第 50 刀已留好，自动出新词）；④三个导入脚本写各自的数据集词（重导/重置库也能逐个可见）；⑤README 来源表改逐数据集。实测：商品 `wikidata 91 / openfoodfacts 20 / seed 2 / wands 1`、资产 `openfoodfacts 20`；商品页出现「Wikidata」「OpenFoodFacts」「WANDS 基准」且**不再出现「开放数据集」**；迁移往返可逆（降级后三值归零、`open_dataset` 回 3 行）。集成 930→934 passed（新增迁移拆细用例；迁移 0026 用例按第 51 刀的教训**钉到 0026 为止**，不再升到 head 验别人的口径）；ruff 全过；前端 build 绿、lint 7/0。证据见 `docs/progress/dataset-labels-closeout.md`（含诚实披露：`open_dataset` 成了有词无用的预留值；拆细靠形态判据）。


## 审计刀 11（已交付，`feat/audit-11`）——审计刀 10 后五刀（第 51–55 刀，迁移 0027–0028）

**路径：** 三路独立只读子代理（设计符合性 / 技术债与健壮性 / 功能缺口与产品面真实性）。**P0×2 实修**：①**会话详情恒回 `host_origin: null`**——`SessionDetail` 继承字段却没在构造时传，列表有值、详情给 None，而第 54 刀 closeout 明写「列表与详情同源」（三轴同时命中）→ 补构造参数 + 详情断言钉子；②**治理台「待补缺口」里躺着客服当下已能答的问题**（「你们卖什么？」「笔记本电脑多少钱？」两条 open，而顾客同问句分别答出目录列举与类目价）→ 产品面：`run_ask` 的目录回落分支把同问 open 缺口一并收掉（`resolved_by_asset_id` 留 NULL，不假装有文档）+ 存量两条陈旧缺口手工关闭。**P1×5 实修**：类目问价被「商品名含类目名」劫持（商品「WANDS 家具（演示）」含「家具」→ 类目问价被答成单品价、聚合被吞）→ **先类目后单品**；导入脚本不写资产来源（重置库后 220 条资产的来源词消失）→ 两个脚本导入后按形态补写；迁移用例**第三次**踩「升到 head」→ 钉到 0028 并加「无 open_dataset 残留」断言；商品页首屏 **115 张卡**（第 50 刀全量回填价后「未定价才收起」判据失效）→ 按数量收口 24 张 + 折叠区；两处披露订正（「演示看不到区间」不成立——器皿有两价、实测 99–129 元；商品分布漏 1 条手建）。**P2 五条**：host_origin 入库截断 255、类目混币种拒答、类目轨迹价格复用 `format_price`、四处 `open_dataset` 旧注释订正、`CONTEXT` 来源词条补到 11 值。集成 934→937 passed；ruff 全过；前端 build 绿、lint 7/0。**记债**：顾客口语覆盖不足（笔记本别名/怎么卖/有X吗三条拒答）、「证据未覆盖却挂引用」的弱命中原口径、工作队列默认视角、评分可改、分币种报价。证据见 `docs/progress/audit-11-closeout.md`。


## 第 56 刀：类目别名与口语问价覆盖（已交付，`feat/category-alias-56`）——审计刀 11 首推

**路径：** 审计刀 11 实测：`笔记本多少钱？`（类目别名）、`笔记本电脑怎么卖？`（报价词缺口语形态）、`你们有笔记本吗？`（库存只认商品全名，答「未找到商品」+转人工）三条**全拒答**——演示观众用口语即穿帮。本刀（**无新表无迁移**）：①`CATEGORY_ALIASES`（4 个无歧义短称）；②`PRICE_RE` 补 `怎么卖/怎么买/卖多少/什么价/啥价/多钱`；③提纯词表改只收**原子词**且长词在前（复合词会切后缀留残渣）；④库存**类目聚合**（含 `render_stock_answer` 类目行与 `_run_stock_ask` 事实分支放宽——漏一处就会答「库存未设置」甚至 500）；⑤库存路由器补 `有没有/有…吗`，**只做路由**，查不到即回落（「有现货吗」照旧拒答、不误答）。实测：`笔记本多少钱？`/`笔记本电脑怎么卖？`/`手机多少钱？` 全部答出类目价（如「笔记本电脑共 34 件，均为 4999元」）；`你们有笔记本吗？`→「共 34 件，其中有货 34 件，库存合计 1653 件」；服务/规格问仍被纯度闸挡住（「笔记本电脑怎么保养？」照旧拒答）。集成 937→941 passed；ruff 全过；前端未改（build/lint 7/0 回归确认）。**两次自抓**：真栈 `KeyError('stock')` 500（单元测试因测试库无该类目而漏过，端到端探针抓到）→ 模板加类目分支 + 渲染钉子；正则复合词吃后缀 → 词表改原子词。证据见 `docs/progress/category-alias-closeout.md`。


## 第 57 刀：会话详情补齐站点/访客/评分（已交付，`feat/session-detail-57`）——审计刀 11 建议 ③

**路径：** 第 54/56 刀把「站点/访客/评分」做进了会话**列表行**，但会话**详情**只带了 `host_origin`（审计刀 11 P0 修的那一版）——从总览 CSAT 的 `#会话号` 深链进来（`/service?session=N`），右栏看不到站点、看不到访客、也看不到自己刚点的星（审计刀 11 P2-1）。本刀（**无新表无迁移**）：①`SessionDetail` 补 `visitor_id` 与 `rating`（一次 `select score`；一会话一评、未评 None），与列表行**同源**；②详情头部在「会话 #N / 状态 / 开始时间」后显示**站点 / 访客 / ★** 三枚 chip（与列表同行同形同文案；未评分不渲染，不出现「★ undefined」）。实测：`/service?session=102` → 头部「站点 localhost:5173 · 访客 bc1988d4」；`/service?session=84` → 头部「★ 4」；接口 `{host_origin, visitor_id, rating}` 与列表逐字段一致。集成 941→943 passed；ruff 全过；前端 build 绿、lint 7/0。证据见 `docs/progress/session-detail-closeout.md`（含诚实披露：详情仍不带 message_count；站点/访客仍是前端自报+白名单，本刀只渲染不提高可信度；评分不可改）。


## 第 58 刀：弱命中「模型自述证据未覆盖」按拒答收口（已交付，`feat/coverage-gate-58`）——审计刀 11 C-P1-1

**路径：** 审计刀 11 实测：`保温杯刻字怎么收费？`（5 条弱命中）→ 忠实度闸不触发（命中>1）→ 模型生成「当前已发布证据未覆盖刻字收费信息。」→ 落库 `kind=answer`、**citations=[13,9]**、`handoff=false`、**不落缺口**；而同一问句在无命中时是拒答+缺口+转人工——**同一种知识缺失两种系统状态**，且「没答」还挂着引用芯片。本刀（**无新表无迁移**）：①`_NO_COVERAGE_RE` 覆盖声明闸（保守：只认「证据/资料/信息/数据 + 未覆盖/未涉及/未包含/不包含/中没有」与「无法回答/提供/确认/给出」两类说法）；②命中则把 `answer` 收成 `refusal`——citations 清零、handoff=true、`generated=None` 使内容走**既有拒答模板**（固定文案 + 问句摘要 + 缺口号）、缺口照落、工单照建；③`fallback_reason="no_coverage"` 随 complete 带出（与忠实度闸的 `"coverage"` 并列，可观测）。钉子：真引擎 + 替身 LLM（两条命中 + 自述未覆盖）→ refusal/零引用/缺口照落；反向钉子：正常作答（含「根据已发布证据」）不被误判。集成 943→945 passed；ruff 全过；前端未改（build/lint 7/0 回归确认）。**评测基线如实记录一处漂移**（positive recall@1 70.0%→67.5%）：原因是审计刀 11 的治理性订正（asset 13 走「开修订→换正文→发布 v4」对齐退货政策口径）取代了 golden 里 `pos-017` 期望的 `13/v3`；**本刀代码不经检索路径**（run_eval 直调 retrieve+compose），已写进 `docs/research/rag-eval-report.md` 的 After 段并说明「大集基线随演示库数据变化」。证据见 `docs/progress/coverage-gate-closeout.md`。


## 第 59 刀：工作队列导入货折叠（已交付，`feat/workqueue-fold-59`）——审计刀 8 起的首屏债

**路径：** 审计刀 8 起记债的「工作队列首屏被导入货淹掉」在审计刀 9/10/11 三次复核仍是 **194 行里 180 条导入**；第 51 刀给了来源筛选工具但默认视角与首屏没变。本刀（**无新表无迁移、纯前端**）：①`workQueue.isImportedSource`（按**来源词**判「数据集导入」：review_import / open_dataset / wikidata / openfoodfacts / wands——不按标题猜）；②资产表格末尾插一行**折叠头**「数据集导入 N 条（评论语料 / 开放数据集，非人工登记；点开查看）」，默认收起；③展开后逐行与主表**同列同序**（同一 `<tbody>`，列宽不失配）。**明确不动的**：默认 tab/范围（仍是「待人洗 + 工作队列」）、总数与次序、无数据被隐藏——列表计数与来源筛选 chips 照旧按全量算。实测：默认 **15 行**（14 条人工/系统产生 + 1 折叠头）↔ 点开 **195 行**；chips 计数不变（评论导入 180 / 切片拣选 6 / 会话回流 5 / 上传 3）。集成 945 passed（后端未改）；ruff 全过；前端 build 绿、lint 7/0。**诚实披露**：没有改默认视角（「默认是否只显示人工来源」是 Owner 观感裁决，审计刀 8 起挂账）；展开态不持久（与商品页「其余商品」折叠同口径）。证据见 `docs/progress/workqueue-fold-closeout.md`。


## 第 60 刀：golden 过期期望维护（已交付，`feat/golden-refresh-60`）——把第 58 刀的评测漂移查清

**路径：** 第 58 刀复跑评测出现 positive recall@1 70.0%→67.5% 的漂移，当时的解释是「治理动作改正文→移块→分数变化」。本刀逐条盘点 golden 里「期望版本 ≠ 当前已发布版」的条目 → **只有 2 条**（`pos-008` 退货政策、`pos-017` 净含量），都是 asset 13：审计刀 11 为对齐退货政策口径走了「开修订→换正文→发布 v4」，v3 被取代；runner 比对 `(asset_id, version_no)`，版本对不上即记未命中。把两条的 `version_no` 指到**当前已发布版**（`3 → 4`，期望的资产不变——语义是「该问题应命中这份资产当前的那一版」，不是迁就输出）后复跑：**基线逐位恢复**（positive 70.0/75.0、paraphrase 60.0/72.0、confusion 60.0/80.0、refusal 100%、overall 65.0/75.0）。**结论：那 1.3pp 全部是过期期望，不是检索回归、也不是分数漂移**；`docs/research/rag-eval-report.md` 已加「订正」小节把先前写偏的解释改对，并记下流程缺口「发布类治理动作之后要顺手核对 golden 的版本期望」。本刀**无代码改动**（集成 945 passed 不变）。证据见 `docs/progress/golden-refresh-closeout.md`。


## 审计刀 12（已交付，`feat/audit-12`）——审计刀 11 后四刀（第 57–60 刀）

**路径：** 三路独立只读子代理（设计符合性 / 技术债与健壮性 / 功能缺口与产品面真实性）。**P0×3 实修**：①**过度拒答回归（最重）**——第 58 刀的覆盖声明闸按**整段**判，模型对内置建议问句「怎么退货？」常输出「签收后 7 天内可申请退货。[1][2] 具体退货操作证据未覆盖。」→ 整条被吞成拒答（实测 2/3 概率 + 落缺口 + 建工单 H-0019），把已发布证据支撑的那句也丢了；改成**按句判 + 摘句作答**（有实质内容就照常答；只剩免责句或只剩 `[1][2]` 引用标记才拒答）。②**闸的误判面**：「配料信息中不包含任何防腐剂」这类**事实否定句**会被当免责句（误判后果比漏检更重）→ 主语收紧为证据类名词、宾语紧贴动词。③**第 60 刀的「订正」本身写错事实**（`pos-017` 的 top-1 是 asset 3、13 排 rank 3；那 1.3pp 只来自 `pos-008` 一条过期期望）→ 四处逐一订正 + 补记「同分并列的位次脆弱」。**P1×6 实修**：「未提供/未说明」等说法漏检 → 谓语面补齐（有按句逻辑兜底）；**缺口收口从目录路径泛化到任何答上的出口**（库存/RAG 已答的缺口现在会关——实测缺口 18/24 转 resolved）；折叠行补齐第 9 列并恢复行尾「重试机洗」（此前导入货机洗失败在列表里没法重试）；筛选/搜索只命中导入货时自动展开（不再只剩一条折叠头）；`fallback_reason` 按 `gap_id` 同一白名单只给操作者通道（此前顾客能看内部闸口径）；「缺口照落」的钉子从恒真的 `db.add.called` 改成真断言。**P2 六条**：评测 runner 加**过期期望守卫**（cite 版本 ≠ 当前已发布版即非零退出并点名）、`CONTEXT` 清掉横跨 6 刀的自相矛盾（「widget 不落宿主 origin」已由第 54 刀交付）、测试计数订正等。集成 945→**957 passed**；ruff 全过；前端 build 绿、lint 7/0；大集评测复跑 70.0/75.0、overall 65.0/75.0。**记债**：演示库被探针重度污染（会话 122/空 33、pending 工单 18、审计行 842）、类目别名只覆盖 4/9 类目、折叠组在所有 tab 生效、`fallback_reason` 不进指标。证据见 `docs/progress/audit-12-closeout.md`。


## 第 61 刀：演示库探针清理脚本（已交付，`feat/demo-reset-61`）——审计刀 12 建议 ①

**路径：** 审计刀 12 实测演示库被长期写入的冒烟/审计/端到端探针污染——会话 122 条里 **33 条空会话**（客服页一屏「未开始」）、探针资产 16 条（`mcp-smoke` / `evidence probe` 机器行）、pending 工单 18 张、`audit_log` 842 条（export 750）。本刀（**无表无迁移**）：①新增 `scripts/demo_reset.py`——**默认 dry-run、`--apply` 才写库**（清理不可逆，不该顺手跑就写库），清**两类特征行**：**空会话**（`service_sessions` 中无消息/工单/评分/缺口/回流锚）与**探针资产**（`source_kind='mcp_registered'` 且标题 `^(mcp-smoke|evidence probe)`，判据与前端 `workQueue.isWorkProbe` 同源）→ **置 discarded**（ADR 0042 语义，列表/检索/导出不再出现），**不删行不删字节**；**知识缺口与工单只报告不清**（它们是「拒答留缺口→去补→再问命中」与「转人工闭环」的演示素材）；②README「演示前检查」补一节（先 dry-run 看数、再 `--apply`，写清清什么/不清什么）；③3 例真库测试（只清特征行、有消息/有评分的会话与正常资产不误伤、幂等、CLI 不带 `--apply` 绝不写库）。实测：dry-run `33 / 16` → `--apply` 后 `empty_sessions 0 / probe_assets 0 / sessions 124→91`，缺口 20 与工单 20 原样；接口复核 `mcp_registered` 可见 0 条、空会话 0。集成 957→960 passed；ruff 全过；前端未改（build/lint 7/0 回归确认）。证据见 `docs/progress/demo-reset-closeout.md`（含诚实披露：脚本只做特征命中、不按时间/按人猜；本刀对演示库执行了一次 `--apply` 作验收）。


## 第 62 刀：类目别名扩到 9/9 类目 + 类目问句纯度闸补到库存路径（已交付，`feat/category-alias-62`）——审计刀 12 C-P2

**路径：** 审计刀 12 记 C-P2「类目别名只覆盖 4/9 类目（电脑/书 多少钱仍拒答）」。开刀实测（直调演示库）还揪出更重的一条：**库存路径的类目聚合根本没有纯度闸**——第 56 刀把别名表接进了 `_category_stock`，闸却只装在报价那条（`price_residual`），于是「手机壳有货吗」被答成「智能手机共 10 件」、「电视柜有货吗」答电视机、「平板支撑有货吗」答平板电脑、「笔记本电脑包有货吗」答笔记本电脑（**第三次踩「同一道闸只装两条入口路径中的一条」**）。本刀（**无表无迁移**）：①`CATEGORY_ALIASES` 6→18 条（+电脑/本本、彩电、智能机、零食/吃的/食物、书/书籍/书本、杯子/水杯，覆盖 7/9 类目的口语短称；洗衣机/家具类目名即顾客用词走字面命中，**按「顾客的说法能不能问到」覆盖 9/9**）；②歧义面**改由纯度闸承担**——`category_question_residual`（命中字面**字面剔除** + 问价虚词）非空即拒答，第 56 刀不敢收的「书」（怕撞「说明书」）本刀实测闸在就能收，「说明书/书桌/书签/证书/电脑包/手机壳/电视柜/平板支撑/洗衣液/食物保鲜盒 多少钱」全部拒答；③类目 token 从 `price_residual`（`lcs_fragment` + 最小长度 2）改**字面剔除**——修掉「书多少钱」因 fragment 长 1 < 2 永远剔不掉、单字别名全体失效的缺陷；④`category_targets(categories)` 成候选表**单一真源**（别名在前、库内类目名在后长名优先），报价与库存**共用表、各装各的闸**（问价/问货两张虚词表，合并会互相放水）；⑤库存侧补 `stock_residual` + 55 条虚词表（**原子词 + 长词在前**：手写顺序时「没有货吗」会被「有」先吃掉剩残渣），修掉上述 4 例误答；⑥库存侧**类目先于单品**（对齐报价侧审计刀 11 P1：商品名含完整类目名时先走单品会把「你们有家具吗」答成这一个商品）。**收不了的**（能力边界非保守）：「保温杯」→器皿 会吞掉「保温杯多少钱」的单品行价（精度倒退），不收。**浏览器实测**（playwright-cli 真栈新会话）：零食多少钱→「食品共 21 件…均为 3元」、书多少钱→「图书共 19 件…均为 59元」、杯子有货吗→「器皿共 2 件，其中有货 1 件，库存合计 42 件」、彩电多少钱→「电视机共 16 件…均为 3499元」、手机壳有货吗→**拒答·已转人工**（原先误答「智能手机共 10 件」）。两轴评审（Standards 轴 P1×1+P2×5、Spec 轴 P2×2+P3×4，全数实修）：库存闸负例 fixture 补齐类目（3/6 原是空断言）、裸虚词「店」改收「店里/店铺/门店」（「手机店/书店 有货吗」不再被当成类目、「你们店里有笔记本吗」照旧过闸）、「洗衣液」空负例与死断言删、文档计数订正（别名 18 条 7 类目）、`_PRICE_RE` 补礼貌开场/量词/裸「价」（修「请问一下手机多少钱」「多少钱一台」「什么价」死意图路径）。集成 960→998 passed（+38 例）；ruff 全过；前端未改；评测零漂移（positive 70.0%、overall 65.0%，与本刀开刀前逐位相同——未动检索/生成）。**诚实披露**：库存侧加闸收紧了「笔记本什么时候有货」这类问句（残渣「什么时候」非空→回落，属纠正而非回归：库存工具答不了到货时间；无既有测试覆盖）；浏览器验收对演示库落 1 会话 + 1 open 缺口 + 1 pending 工单（拒答路径的必然产物）；「家电多少钱」是**真缺口**（库里没这个类目），别名不该假装能答。ADR 0037 新增第 56/62 刀修订（原「不做别名表」过时）、ADR 0045 新增二修订之三**并订正**原稿写反的判定顺序（实现早已是「先类目后单品」）。证据见 `docs/progress/category-alias-expand-{intake,closeout}.md`。


## 第 63 刀：`fallback_reason` 进指标（已交付，`feat/fallback-metric-63`）——审计刀 12 建议 3 / 审计刀 7 起记债

**路径：** 引擎早有 `AskOutcome.fallback_reason`（两值有界：`coverage` 忠实度闸降级（ADR 0044 §二）、`no_coverage` 证据未覆盖按拒答收口（第 58 刀）；普通厂商失败为 None），但观测面只有 `chat_requests_total` 的 `generated=false`——分不出「闸在回退」与「厂商失败降级」，闸回退率只能日志数行（第 47 刀 closeout 原话；审计刀 7 起记债）。本刀（**无表无迁移、不动引擎**）：①`chat_fallbacks_total{channel, reason}`，`other` 为防御位（引擎将来加新 reason 而指标没跟上，宁可落 other 也不让陌生值进标签——标签值一律有限集合的第 47 刀纪律）；**None 不计**——普通厂商失败不是闸在回退，混进来污染闸回退率；②记在 `record_chat_request`（customer/operator 两条 ask 路由既有调用点，route 层记而非 SSE 懒执行——第 47 刀的既有取舍），引擎零改动；③README 业务指标清单五个→六个并写清取值含义（闸回退率=chat_fallbacks/chat_requests，不是 generated=false 占比）。验收：真 `/metrics` 端点 presence、coverage/no_coverage 两枚正向钉子（customer/operator 各一）、普通回答不进闸回退计数、未知值落 other、标签集合 `{channel, reason}` 契约；集成 998→**1000 passed**；ruff 全过；前端未改。诚实披露：闸回退率无历史数据可对比（指标从本刀起累计）；单测用 `_Outcome` 替身直调记账函数（与第 47 刀同口径，引擎侧 fallback_reason 赋值钉子第 40/58 刀已有）。证据见 `docs/progress/fallback-metric-{intake,closeout}.md`。


## 第 64 刀：导入货折叠语义对齐（已交付，`feat/import-fold-64`）——审计刀 12 记债 C-P2

**路径：** 第 59 刀把数据集导入行折叠到表尾时，谓词只有来源词（`isImportedSource`）——折叠组在**所有** tab 生效，于是「已发布」tab 也把 20 条 OpenFoodFacts 规格折进去（published 56 → 可见 36 + 折叠头，审计刀 12 C-P2 记债）。**判据不是观感而是语义**：折叠的动机（第 59 刀）是待人洗队列被待办噪声铺满——折的是**待办**，不是「导入」这个来源；已发布的导入行是**线上证据**（客服引用、连接层读），折进表尾等于让用户去折叠头里翻刚被引用的文档。本刀（**纯前端小刀**）：①`workQueue.isFoldedImport = isImportedSource && !isPublished`（两个既有谓词的合取，不新立口径）；②`AssetsListPage` 的 `importedRows`/`primaryRows` 改用该谓词，折叠头「数据集导入 N 条」→「数据集导入待办 N 条」并注明已发布导入行在主行；③默认视角、总数、次序、自动展开逻辑（搜索只命中未发布导入货时仍自动展开；命中已发布导入货时它们本来就在主行）、来源筛选 chips 全不动。**浏览器实测**（playwright-cli 真栈操作者登录）：待人洗折叠头仍在（「数据集导入待办 180 条」，该 tab 行为不变）；**已发布 tab 折叠头 0 个**，OFF 规格行（A-0245 Cardiofitmd 等 20 条）回到主行；全部 tab 主行含已发布导入行 + 折叠头只收未发布待办。前端 `npm run lint` 7/0（基线）、`npm run build` 绿；后端零改动（集成 1000 基线不动）。诚实披露：「全部」tab 主行变长（不再藏线上证据，语义一致性的自然结果，Owner 若更想折可再收）；折叠计数口径从「全部导入」变「未发布导入」；前端无单测框架（历刀同口径，靠 build/lint + 浏览器点穿）。证据见 `docs/progress/import-fold-scope-{intake,closeout}.md`。


## 第 65 刀：问货/问价口语覆盖收口（已交付，`feat/colloquial-65`）

**路径：** 第 62 刀 Spec 轴评审留下几张「难以定性」问句，其中三类属**自然问法却拒答**：卖完/卖光（问库存是否售罄）、咋卖/咋买（怎么卖的北方式说法）、贵吗（意图词表只有「贵不贵」）。**本刀的层间缝是浏览器验收抓的**：先只给 `_STOCK_FILLER_WORDS` 补了卖完/卖光，直调 `query_stock` 全绿；真栈实测「书都卖完了吗」**仍拒答**——`get_stock(图书)` 只在提议步跑过一次，主路径没走：`STOCK_KEYWORD_PATTERN`（路由词表）没有「卖完/卖光」，问句里无「有」字，`有.{0,12}吗` 不命中，引擎根本不把问句派给库存工具。修法（**无表无迁移**）：①路由词表 += `卖完|卖光`（词表是路由器不是判据：查不到照旧回落，第 56 刀的既有取舍）；②纯度虚词表 += 原子词卖完/卖光；③问价意图 `PRICE_RE` += `咋卖|咋买|贵`（**裸「贵」排在「贵不贵」后面**——长词先匹配，否则贵不贵被贵吃掉剩「不」）；④纯度 `_PRICE_RE` 同步 += 裸贵（意图层认、纯度层剔不掉=死意图路径）；⑤**不收「到货」**——到货了吗/到货了没问的是到货**时间**，库存工具答不了，照旧回落拒答留缺口是正确归宿。**浏览器实测**（真栈顾客页新会话）：书都卖完了吗→「图书共 19 件，其中有货 19 件，库存合计 846 件。」、杯子卖光了吗→「器皿共 2 件，其中有货 1 件，库存合计 42 件。」、手机咋卖→「智能手机共 10 件，其中已定价 10 件，均为 2999元」。负例 9/9 全拒答：电脑桌贵吗/二手手机贵吗/手机膜咋卖/电视机柜咋卖（修饰语靠残字挡）、手机膜卖完了吗/书卖完了怎么办/书卖完了能退货吗（闸挡回回落）、**退货运费贵吗**（政策闸仍在意图层之前，参数化再钉一次）。集成 1000→**1017 passed**（+17，含路由层钉子 `test_stock_pattern_routes_sellout_forms` 与单品闸钉子——**路由层、类目层、单品层各自有钉子**，第 56 刀「真栈 500 单测没抓到」的同款教训）；ruff 全过；前端未改；评测逐位相同（70.0/65.0）。两轴评审无 P0/P1、P2×5 全实修（单品闸/虚词缺口/刀号与会话数订正；「咋样卖/咋价/还有么」等 56 刀词面邻缝移交审计刀 13）。诚实披露：层间缝由浏览器验收而非单测抓到；验收对演示库落 3 会话 18 消息（评审实测订正）。证据见 `docs/progress/colloquial-coverage-{intake,closeout}.md`。


## 审计刀 13：三路并行只读审计第 61–65 刀（已交付，`feat/audit-13`）

**路径：** 三轴（A 设计符合性 / B 技术债与健壮性 / C 功能缺口与产品面真实性，C 轴含 live 探针并如实报告写副作用）。**P0×2（C 轴）实修**：①**README 演示口径的指代追问拒答**——「净含量→那它的材质是什么」稳定拒答+转人工：拼接检索让上一问主题块占满 top-k，而 prompt 与引用选取**只看前两条**（`_MAX_PROMPT_EVIDENCE`/`_MAX_EVIDENCE`=2）——修法 `chat_engine.merge_own_hits`（带代词追问时本问裸检索新命中去重后**交错**并入；先做追加版实测无效：尾部追加进不了前两条证据窗，交错窗口恰是「语境+主题」），live 三连测「它的材质是钛钢。[2]」（A-9·v1+A-13·v4）；金标 runner 不经拼接，复跑逐位相同（覆盖面边界已在 rag-eval-report 补记）。②**「到货了吗」弱引用胡答**——live 演示库 200 条评论让词法命中恒存在（实测引英文 account_access 评论），第 65 刀「照旧拒答留缺口」断言只在无评论语料的测试库上成立——closeout 原处订正为「回落检索，归宿取决于命中」，弱引用质量闸（rerank/来源感知阈值）记债。**P1×4**：B 轴「花多少钱」死意图（re 择先按最左起始位，「花多少」早一个字符起吃剩孤字「钱」——「长词在前」管不住前缀起始更早，补裸「钱」+「我想」）；C 轴治理台陈旧缺口 G26/G1（用产品机制关闭：操作者通道问一次即自动收口）、指标演示路径三步化（printf 单步会生成空 token 文件）、端口口径随 PG_PORT 注明。**P2×13** 择要：提议步闸按**原问句**判（LLM 干净 product_name 当问句=闸形同虚设）；词表补售罄/断货/这/那/个/台/本书/多（「这个保温杯还有货吗」此前残字「个」被挡——审计顺带实测）；62 刀门禁假账订正（+23/983 实为 +38/998）；ADR 0037 补第 65 刀+审计刀 13 修订（路由词表两刀没回写、单品闸未记）；demo_reset `~*` 对齐前端 /i、`--apply` 前回显目标库（掩密码）、README 注明空会话判据含刚创建未发问的会话；isPublished 手抄副本收口；SO-1001 口播注释订正。集成 1017→**1034 passed**（+17）；ruff 全过；前端 lint 7/0、build 绿；评测逐位相同。记债：弱引用质量闸（P0-2 根因）、多轮工具/裸追问、G4/G5 闭环断裂、56 刀词面邻缝。证据见 `docs/progress/audit-13-closeout.md`（含 C 轴写副作用忠实清单与 Owner 处置）。


## 第 66 刀：评论证据适用域（已交付，`feat/review-evidence-66`）——审计刀 13 P0-2 根因

**路径：** 审计刀 13 C 轴 P0-2：弱引用胡答（「到货了吗」引英文 account_access 评论、退货运费首引衣服评论）。**测量先行否掉了分数阈值路线**：坏 case 分数 0.4–0.5，金标真命中最低才 0.17——短评论块 bigram 少、sqrt 归一后分数天然高，阈值分不开好坏；分得开的是证据**类别**（顾客评论=商品体验证物）与问句**意图**（服务状态问）。本刀：①`retrieval.excludes_review_evidence(query)`——服务状态词（到货/发货/物流/快递/收货/签收/退货/退款/运费/订单/单号/售后/换货/客服/保修）在场且**无**观点标记（怎么样/如何/体验/好不好/评价/靠谱/值得/推荐/快吗/慢吗…）→ `review_import` 块不作为证据（纯函数，16 例真值表钉死）；②落点在 `retrieve()` 候选集过滤（候选 SQL 随带 `Asset.source_kind`，join 本来就在零额外查询）——引擎、缺口新鲜度检查、MCP `search_published`、评测 runner **全出口同语义**（「什么算证据」只有一处定义，ADR 0018 修订）；③观点豁免是零回归关键：金标 19 条期望评论资产的 case **全部**带观点标记，大集 before/after **逐位相同**（70.0/65.0，rag-eval-report 已记）；④带订单号问句在引擎步 1 已被订单工具接走。**live 实测**：到货了吗→拒答·转人工；退货运费多少钱→**拒答留缺口**（退货政策文档确实没写运费——此前那条「答案」引的是别人评论里「还要我自己承担运费」，本来就是假证据；缺口是真的，去补=补政策）；物流怎么样→照常引评论（asset 78，豁免面）。集成 1034→**1051 passed**（+17；synonym/redact 两个 fake-DB 的候选行形状随 5 元组更新并注明）。两轴评审 P1×2/P2×5 全数实修/披露：**拼接缝**（上一问的观点标记曾豁免本问——retrieve 增 gate_question 参数按本问判定，引擎拼接检索显式传本问）、观点标记漏词（如何/体验/快不快/快吗…——「发货快吗」曾 3 命中→0）、服务词漏词（售后/换货/客服/保修）、「不值得/不推荐」豁免侧从宽钉取舍、session_backflow 残留类与「快递包装结实吗」服务词作定语的体感问记为已知边界。诚实披露：退货运费从「能答」收紧为拒答（诚实归宿，想可答去补政策文档）；词面判据非语义理解，两表都有真值表钉子；MCP search_published 行为同步变化（刻意，ADR 记录）。证据见 `docs/progress/review-evidence-scope-{intake,closeout}.md` 与 ADR 0018 修订。


## 第 67 刀：56 刀词面邻缝收口（已交付，`feat/word-seam-67`）

**路径：** 第 62/65 刀评审留下、审计刀 13 移交的词面邻缝——「彩电咋样卖」「手机咋价」（意图层有咋卖没咋样卖、有啥价没咋价）、「这手机啥价」「那个手机咋卖」「这台笔记本电脑多少钱」（价格虚词只收了这个/这款复合词，残字 这/个/台 挡回——库存侧 65 刀已收裸字，价格侧没对齐）、「书还有么」「手机还有呢」（路由 `有.{0,12}吗` 只认吗，纯度层能答、路由不派）。本刀（**无表无迁移**）：①`PRICE_RE` += 咋样卖/咋样买/咋价——**只收复合词**，咋样单独是评价问（「这个咋样」负例钉住仍拒答）；②`_PRICE_RE` += 裸 这/那/个/台（修饰语靠残字挡：电脑包咋样卖/手机膜多少钱 照旧拒答）；③`STOCK_KEYWORD_PATTERN` 的疑问尾 吗→[吗么呢]（路由器语义下放宽安全——查不到照旧回落；「到货了么」无「有」仍不派，65 刀时间问句口径不变）。**live**（真栈顾客通道）：彩电咋样卖→answer 电视机类目价、书还有么→answer 图书聚合。代码级正例 9/9（含那个手机咋卖→智能手机、这台笔记本电脑多少钱→类目价）、负例 4/4。集成 1051→**1063 passed**（+12）；ruff 全过；前端未改；评测逐位相同（65.0/75.0）。诚实披露：词面收口是枚举式的（「咋卖呗」等变体仍在闸外，拒答留缺口归宿诚实）；本刀目标是收掉**已点名**邻缝不是穷举口语。证据见 `docs/progress/word-seam-{intake,closeout}.md`。


## 复审审计（审计清零验收刀，`feat/reaudit-clean`）

**路径：** Owner 目标「打磨到审计清零」。三轴（A 设计+刀 13 修复面复核 / B 对抗+入口×闸矩阵 / C live 复核）。**P0×0 三轴全零**——历刀审计首次（刀 9 起 P0 数：2/2/2/3/2/0）。A 轴：66/67 契约逐项一致、**刀 13 六项修复 6/6 无回退**、测试计数链自洽；B 轴：retrieve 5 出口×闸矩阵全在位（拼接缝双向验证：带观点标记的上一问不能豁免本问、服务态本问照滤）、67 词面两向无活缺陷；C 轴：刀 13 两 P0 live 成立（材质追问「钛钢」引 A-13·v4、到货了吗拒答不引评论）、内置建议 6/6、**open 缺口逐条跑引擎零可答**（治理台待补全部真实）。实修：①P1 观点表 += 咋样/什么样/可靠（「物流咋样」曾饿成拒答而「物流怎么样」正常——67 合法化咋字族后 66 观点表脱节）；②P1 演示库外科清理 6 修复前会话（126/128/129/133-135，本会话审计探针的修复前错误形态；缺口保留仅断来源链）；③P2 服务词 += 包邮/寄件/送货/取件/退回/维修/安装/发票/改地址（零实害防复发）；④ADR 0018 枚举改「以代码为准」；⑤`_PRICE_RE` 注释补记账+去重。观察项记档：提议步规范化名边缘、无单号订单问句引 backflow 自述未覆盖、退货要钱吗半答（句级摘句取舍）、形容词+吗固有边界。集成 1063→**1069 passed**（+6 真值表）；ruff 全过；评测逐位相同（70.0/65.0）。写副作用与 Owner 处置全清单见 closeout。


## 第 68 刀：引擎路径金标进 CI（已交付，`feat/engine-golden-68`）

**路径：** 检索面金标（run_eval.py）直调 retrieve+compose **不经引擎**——历次审计抓的缺陷几乎都在引擎层（目录闸只装一条入口/路由词表层间缝/拼接挤掉本问/评论证据域），尺子全看不见，靠浏览器验收与子代理对抗才抓到。本刀（**无表无迁移**）：①`apps/api/evals/golden_engine.json` 23 例 × 十面（catalog-price/purity/miss/listing、stock/purity、order、evidence-gate、rag、multi-turn），加 case 以改 JSON 为主；②`apps/api/tests/test_engine_eval.py` 直调 `run_ask` 真引擎断言**确定性产物**（kind/tool{arg}/citations/模板文案）+ schema 自检——**进 CI**；LLM 生成路径按 ADR 0027 不进本层（无 key 可复现）；③module 级种子 fixture（笔记本电脑×2 带价带库存、图书×1、规格/政策文档、评论资产、SO-1001 用 conftest 种子），形态要求写 docstring。**对位关系**：eg-cat-002←62 刀别名、eg-cat-006←13 刀花多少钱、eg-stk-003←65 刀路由层、eg-stk-004←67 刀还有么、eg-evd-001←66 刀评论闸、eg-mt-001←13 刀拼接 merge——**此后同类回归 CI 即红**，不再依赖审计子代理撞大运。两轴评审 P1×2/P2×5 全实修（**真证伪实验**：评审用回退修复法抓到 eg-mt-001 空断言——种子语料太小材质块没被挤出 top-5，merge 去重后成空操作；与 stock-purity 面同义反复——种子无智能手机类目。修法：种子加竞争文档与演示手机，Owner 复验三枚钉子回退全红）。诚实披露：忠实度闸/提议步/LLM 文案不在本层（无 key 不可复现）；多轮 case 暂一枚；种子自含 5 类非演示库镜像。集成 1069→**1093 passed**（+24）；ruff 全过；检索金标复跑逐位相同（两层独立）。同批 Owner 裁决记录：**评分可改=允许**（后续刀）、**分币种类目报价=维持拒答**、**工作队列默认视角=维持现状**。证据见 `docs/progress/engine-golden-{intake,closeout}.md`。


## 第 69 刀：评论闸观点尾白名单扩表（已交付，`feat/opinion-tail-69`）

**路径：** 复审审计观察项：66 刀评论闸按「服务词在场」触发，「客服态度好吗」「快递包装结实吗」类带服务词的**体感问**被错杀成拒答（观点标记表无法枚举形容词）。**本刀设计经评审证伪后反转**——初版用「服务词+事实问尾白名单+无观点标记」三条件合取，评审实测证伪：事实尾是**开集**，「退货运费谁承担」（衣服评论 0.894 夺冠压过退货政策 0.408）、「查物流」「发货地是哪里」「支持退货吗」「收货地址能改吗」等未枚举同义全部漏放并引无关评论——66 刀 P0 的同义复现，**错误答案**比初版要修的**错杀**更贵。终版：**保持 66 刀默认拦**（服务词+无观点标记），错杀改由扩 `_OPINION_RE` 解决（+= 好吗/专业吗/严实吗/结实吗/及时吗/顺利吗/麻烦吗/暴力吗/给力吗/耐心吗/墨迹吗/爽快吗/稳吗/满意吗… 品质形容词+吗 实测家族）——未枚举形容词落诚实拒答留缺口（主动选择的便宜失效模式）。实测：体感问恢复评论证据（客服态度好吗→asset 29 态度评论 0.71）；坏 case 全不变（到货了吗无命中、退货运费三形态都首引政策、查物流诚实拒答）；能力问保持拦（初版曾翻放，评审实测会引无关裤子评论，已回拦）。live：客服态度好吗→answer 引评论。真值表改判/新增 14 行（评审漏放面全部钉住）、引擎金标 +1（eg-evd-005）；集成 1093→**1108 passed**；检索金标 96 例逐例零 diff（70.0/65.0）。证据见 `docs/progress/opinion-tail-{intake,closeout}.md`。


## 第 70 刀：无单号订单问句归宿（已交付，`feat/order-clarify-70`）

**路径：** 复审审计 F3：无单号的订单状态问两个旧归宿都不对——检索弱命中半答（引一条自述「未覆盖订单进度」的回流资产）或拒答+缺口（把「顾客没给单号」记成「知识待补」）。裁决（**ADR 0036 修订**）：缺的是**输入**——引擎步 1（订单号未中后）加澄清路由：短状态问（`查物流|物流信息|到哪了|到货了吗|发货了吗|查订单|订单到哪` 等形态）且 ≤14 字且无观点标记 → `_run_order_clarify_ask`：kind=answer 澄清文案 + `need_order_no` 伪工具记录，**不检索、不落缺口、不转人工**；顾客补单号下一问即走订单工具（自然多轮闭环，引擎金标 eg-mt-002：「到货了吗→SO-1001→答已发货」）。**不触发面**（12 例真值表钉）：裸「订单」（「把所有订单都列出来」越狱注入不能被澄清截胡——评审期实测抓到后收紧）；观点问（物流怎么样——评论正是证据，66/69 刀口径）；超长（混意图「订单到哪了？顺便讲讲保修政策」留给提议步综合，ADR 0043——长度闸）；「什么时候能收到货」（时效政策可由文档答）。**live**（真栈同会话）：「我的订单到哪了」→澄清→「SO-1001」→订单工具答。旧金标 `edge-refuse-order-without-order-no`（refuse 契约）被本刀替换为 `edge-clarify-…`（order-clarify 工具形状）。集成 1108→**1136 passed**（+28）；ruff 全过；前端未改；检索金标逐位相同（70.0/65.0）。诚实披露：长度闸是粗判据（边界问句落旧路径归宿仍诚实）；need_order_no 是伪工具不进 TOOL_REGISTRY；补单号的下一问必须真带单号。证据见 `docs/progress/order-clarify-{intake,closeout}.md` 与 ADR 0036 修订。


## 审计刀 14：三路并行只读审计第 66–70 刀（已交付，`feat/audit-14`）

**路径：** 三轴（A 设计 / B 对抗 / C live）。**P0×1（B 轴）**：`_OPINION_RE` 的裸「如何」是无边界子串——「退货运费如何计算」「售后如何处理」「物流信息如何查询」被当观点问放行评论，实测前者首引衣服评论 0.447 压过退货政策 0.408（66 刀 P0 同义复现）。修法按「漏枚举=选便宜失效模式」重排：去裸「如何」、只收观点复合词（服务如何/体验如何/态度如何/速度如何/感觉如何）——「如何+动词」未枚举形态全部正确被拦（贵方向闭合），裸「这家物流如何」落诚实拒答（便宜方向）。**P1×5**：①A/B 同源澄清裸名词过触发（「包裹破损怎么赔」「订单信息怎么改」「物流信息错误」被截胡）——裸 包裹/订单信息/物流信息 不触发，收状态形态与名词收尾锚定（`物流信息$`）；②催单族漏（「东西怎么还没到」曾落检索引评论）——+=怎么还没/咋还没/还没到/没收到/没到货；③混意图「另外」绕过——+=另外/然后/同时/怎么改/怎么填/错误；④陈旧缺口 #39/#40 用产品机制关闭（澄清收口生效）；⑤SO-1001 演示漂移维持口播注记（CI 测试库重种不受影响）。**P2**：ADR 0036 补记 SSE 状态行与「词表现值以代码为准」；计数订正（23 例真值表/69 分解式）；「退货运费谁承担」结局按 LLM 中介实态订正表述（拒答或政策改述半答，两者诚实、错误答案面已闭合）；登记体感形态（省心吗/准时吗/好评）；demo_reset 清演示库 3 空会话+4 探针资产；**环境注记：并发 pytest 共享 suite_test 库名互踩（module 级 DROP/CREATE 竞态），并行跑门禁须唯一库名**。复核面：复审六项修复 6/6 在；引擎金标 28 passed 且 eg-evd-001 对评论闸**载荷性**实验确认；计数链 1093→1108→1136→1157 自洽；内置建议 6/6 live 零回归；C 轴演示素材注记归档。集成 1136→**1157 passed**（+21）；ruff 全过；评测逐位相同（70.0/65.0）。证据见 `docs/progress/audit-14-closeout.md`。


## 第 71 刀：评分可改（已交付，`feat/rating-edit-71`）

**路径：** Owner 裁决（2026-09-11 完善路线批）：评分可改=允许（顾客常在后续互动后想改分）。**迁移 0029**：`session_ratings.updated_at`（NULL=首评未改）。后端：已评 409 → **UPDATE 覆盖式留最新**（score/comment 整体覆盖；comment 不带即清空——前端提交回填当前留言故不自清，接口语义写在端点 docstring）；`RatingOut` 带 updated_at；唯一约束不动（并发插入竞态的 IntegrityError 兜底保留）。前端：评分条从「评过即收」改**常驻可改**——星星回显当前分（hover 预览覆盖）、留言框回填当前值、点星即再提交，状态行「已提交 N 星（，已更新），可随时修改」；新会话归位含新 state。指标口径：`csat_ratings_total` 计**每次提交**（事件数），CSAT 均分/分布读行数据（恒最新）。**浏览器实测**：问净含量→评分条出；点 4 星→「已提交 4 星，可随时修改」；填留言→点 5 星→「已提交 5 星，已更新」，DB 单行 score=5/comment 保留/updated_at 非空（对照旧行 NULL）。集成 **1157 passed**（改判 1 例：`test_rating_is_editable_latest_wins`——首评 updated_at=None/改评覆盖单行/comment 不带即清空/created_at 不动）；前端 lint 7/0、build 绿；检索金标逐位相同。诚实披露：无评分历史（要看趋势靠事件计数+日志行）；覆盖语义对第三方直调 API 同样成立（不带 comment 清留言）。证据见 `docs/progress/rating-edit-{intake,closeout}.md`。
