# 近几刀

完整产品仍是 `docs/goal.md` 里的七块能力（不做模型微调，ADR 0028）。推进方式是 **Slice Owner：一刀一条可演示路径**，关刀看证据再排下一刀。这里只排近几刀，不是八块路线图，也不是一次铺开。

上一刀：**知识缺口闭环**（`feat/knowledge-gap`，closeout 见 `docs/progress/knowledge-gap-closeout.md`）。客服引用已关刀；治理发布写回与脚手架已合并 main。

当前阶段：工程刀。第 4 刀已交付（stack 于 PR #5，待人合并）。第 5 刀主题已锁（见下）。

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

## 第 5 刀（短对齐已锁主题，实现待第 4 刀合并）

**MCP 只读已发布**（ADR 0001/0020/0032）：同一 FastAPI 进程 `/mcp/` Streamable HTTP；`MCP_BEARER_TOKEN` 空则全部 401，不复用操作者 cookie。工具：`search_published` / `get_asset` / `register_asset`（正文必填，来源=mcp_registered）/ `export_published`（元数据+该版正文）。无 publish。官方 MCP Python SDK。README 给 Cursor `mcp.json`。检索索引已就绪（0017）。

## 更后面（现在不锁顺序，各是独立刀）

| 刀 | 约束 |
| --- | --- |
| **修订流 + 回滚** | ADR 0006/0016。落地后：有已发布规格则缺口默认开修订关闭（0031） |
| **厂商生成** | ADR 0033。证据约束下的 Chat API；密钥只在 `.env`；不塞进 MCP 刀 |
| **顾客对话通道** | ADR 0021/0033。会话令牌 + 每会话限流 + 每 IP 托底；同一引擎 |
| 素材 / 切片 / 考核 | 有接待飞轮和 MCP 证据后再进队。质检、候选不是资产已锁。不做微调（0028） |

**展示约定：** 资产 ID 对外写成 `A-{id:04d}`（如 `A-0001`），库内仍是整数，避免原型口述和工程芯片对不上。

`prototype/` 只是交互规格；真产品改 `apps/`。
