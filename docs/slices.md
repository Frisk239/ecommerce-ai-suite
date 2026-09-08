# 近几刀

完整产品仍是 `docs/goal.md` 里的七块能力（不做模型微调，ADR 0028）。推进方式是 **Slice Owner：一刀一条可演示路径**，关刀看证据再排下一刀。这里只排近几刀，不是八块路线图，也不是一次铺开。

上一刀：**审计刀 1**（`feat/audit-1`，closeout 见 `docs/progress/audit-1-closeout.md`）：三路审计零硬偏离；P0 回滚写回残留已修（ADR 0034 全量写回，集成 141 passed）；P1 进后续施工单。前六刀（脚手架/治理发布写回/客服引用/知识缺口闭环/MCP 只读已发布/修订流+回滚）已全部合并 main。

当前阶段：工程刀。第 7 刀：**厂商生成**（0033，凭证已备，审计裁决）。

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

## 第 9 刀：数据接入 CSV 批量导入（已交付，`feat/data-ingest` 待 PR）

**路径：** 资产页登记抽屉「批量导入」页签 → CSV（title,content，utf-8-sig）→ 预览计数（后端为准）→ 逐行独立登记（复用 register_asset：0013 字节、kind=document、不挂商品、source_kind=upload 不动 0025 枚举）→ 报告 created/skipped 带行号 → 人洗发布 → 客服/MCP 可答可引。2MB/200 行/200KB 单行三重防线。冷启动死结（空库→全拒答→无回流）有了批量入口。证据见 `docs/progress/data-ingest-closeout.md`。

**Must：** source_kind=upload（CSV=上传通道的批量形态）；逐行 commit 部分成功不回滚（登记不是发布）；报告口径诚实（机洗失败留在已接入）。

**Out：** URL 导入/网站同步、同步-断链语义、挂商品列、增量去重、导入历史。

## 更后面（现在不锁顺序，各是独立刀）

| 刀 | 约束 |
| --- | --- |
| **安全面收口** | XFF 第一跳伪造（直连只信 remote addr 可配置）、限流先于鉴权替他人耗配额、404/401 存在性探测——见 customer-channel-closeout 遗留 |
| **审计刀 2** | 约 5 刀后再轮（上轮见 `docs/progress/audit-1-closeout.md`）；不改产品代码，三路对账后排还债 |
| 素材 / 切片 / 考核 | 有接待飞轮和 MCP 证据后再进队。质检、候选不是资产已锁。不做微调（0028） |

**展示约定：** 资产 ID 对外写成 `A-{id:04d}`（如 `A-0001`），库内仍是整数，避免原型口述和工程芯片对不上。

`prototype/` 只是交互规格；真产品改 `apps/`。
