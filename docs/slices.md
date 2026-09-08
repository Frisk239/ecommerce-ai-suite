# 近几刀

完整产品仍是 `docs/goal.md` 里的七块能力（不做模型微调，ADR 0028）。推进方式是 **Slice Owner：一刀一条可演示路径**，关刀看证据再排下一刀。这里只排近几刀，不是八块路线图，也不是一次铺开。

上一刀：**审计刀 3**（`feat/audit-3`，closeout 见 `docs/progress/audit-3-closeout.md`）：三路子代理审第 11–15 刀——**三路一致 P0 零**；P1 簇=跨事件循环 LLM 客户端雷、回流登记违背连接纪律、库存词表误伤归宿不成立、打码外流面扩大、QA 块截断丢失、工具吞异常无日志、素材/切片/考核三模块整块缺席。**不改产品代码**，修复进后续刀施工单。第 11–15 刀已合并 main。

当前阶段：工程刀。下一刀：**第 16 刀工程还债刀**（P1#1–#6 + 顺手 P2 注释）；审计刀 4 于第 20 刀后触发。

## 怎么切

- 一刀 = 操作者能走完的一条路径（看见 → 理解 → 行动），该路径用到的层同一刀齐。
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

## 更后面（现在不锁顺序，各是独立刀）

| 刀 | 约束 |
| --- | --- |
| 第 16 刀工程还债刀 | 审计刀 3 P1#1–#6 + P2 注释顺手（见 audit-3-closeout 排期表） |
| 第 17–20 刀 | 素材中心 → 直播切片 → 销售考核 → 血缘视图（0012/0029/0014/0015/0026 已锁约束） |
| **审计刀 4** | **第 20 刀后触发** |

**展示约定：** 资产 ID 对外写成 `A-{id:04d}`（如 `A-0001`），库内仍是整数，避免原型口述和工程芯片对不上。

`prototype/` 只是交互规格；真产品改 `apps/`。
