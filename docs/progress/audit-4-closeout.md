# 审计刀 4 closeout：三路并行审计（feat/audit-4）

日期：2026-09-08。范围：审计刀 3（8b4d5a9）后五刀（PR #21..#25：第 16 还债刀/17 素材中心/18 直播切片/19 销售考核/20 血缘视图）+ 全仓现状。方法：三路子代理并行 + Owner 亲跑门禁。**不改产品代码**；修复进后续刀施工单。

## Owner 门禁（junitxml 机械计数）

- ruff：All checks passed；无 DB `tests=446 passed=335 failed/errored=0 skipped=111`；带 DB（5433）`tests=446 passed=446 failed/errored=0 skipped=0`。

## 总判定

**设计路 P0 零**（无未发布进检索/MCP、无任务升格、无越权发布；ADR 0001–0040 与迁移 0001–0011 链完整；CONTEXT 事故已完全恢复且无进一步损伤）。**技术债路 1 个 P0：打码出口簇**——第 17 刀修的是「入口」（机洗抽取侧），**出口全裸**：① dialogue 发布后转写切块进索引，命中 chunk 原样进厂商 prompt（`retrieval.py:163`→`llm.py:150`）；② 人洗 confirmed 值无打码，手填手机号裸进 qa_pair 块入索引；③ coaching 打分 prompt 三输入（题面兜底原文/qa 快照/受训者手输）全裸——设计路 P1#3 是③的子项。**缺口路**：能力 6/7，运营 Agent 零代码（goal 承诺「工具轨迹+失败重试+投放确认」，原型 Ops.tsx 有完整规格）；打码出口另缺 MCP export 与血缘问句展示两个面（同簇）。

## P0/P1 排期（Owner 裁决）

| 刀 | 内容 | 依据 |
| --- | --- | --- |
| **第 21 刀：打码出口收口 + 文档/文案小修** | 出口统一 `redact`：检索命中 chunk 进 prompt 前、confirmed 字段值/qa_pair 块落库与切块、coaching 三输入（题面兜底/快照/受训者答案）、MCP export_published 返回、血缘引用问句展示——**版本字节不动，出口必掩**（0038 修订句）；顺手：ClipsPage「机洗切出候选」文案改（踩「机洗/源录像」词条）、「任务」词条漂移修正（实现与 0038 正确：仅 failed 可重试，待抽检经打回转失败——词条句回写）、0038 素材抽字段正名、血缘「导出」词条注记、slices/文档「4 查询」计数回写（实为 5） | P0 簇（安全语义优先）+ 设计路 P1×3/P2 全部可一刀带走 |
| **第 22 刀：运营 Agent 最小厚切** | 照原型 Ops.tsx：选商品→三步轨迹（读商品→复用素材生成服务→拼投放文案）每步落状态（done/failed 可重试）→操作者确认投放（与治理台发布严格区分，词条「发布」_Avoid 投放发布）；复用 material 服务不另起 LLM；顺手 MCP export 留痕一行 audit_log（血缘「导出」环垫底） | 能力 7/7；缺口路排序 1 |
| 第 23 刀 | 总览页+连接层演示页（纯前端演示面，补「七块都有入口」观感） | 缺口路 P2 |
| 第 24–25 刀 | 按债务与缺口重排（GIN 索引、题库单资产推导、前端收口刀等 P1 债池） | 技术债路 P1 |
| **审计刀 5** | 第 25 刀后触发 | 计数线 |

## P1 债池（不阻断，归还债刀/前端收口刀）

- 性能：血缘 containment 查询无 GIN 索引（生产规模唯一实质面）；题库 derive 每作答 O(N) 全量重推导；material/clips 列表 N+1 无分页。
- 一致性：material 四小条（qc 截断口径/retry 死转移/storage 死参数/reject commit 层次）；分派谓词两处；闸门 kind 条件两处复制；kind 分派三处。
- 前端：UiMessage 膨胀 13 字段×4 处、ask 双闭包复制、题源 Link 手写 3 处、action-error 内联 6 文件、web 零测试基建。

## P2（记录在案）

audit-3 P2 全部仍在（token TTL/孤儿字节 storage.delete 零调用方/放弃修订死锁/检索无缓存/HTTP 并发压测）；coaching 三元组散参/question_key 纯序列化；citations/tool JSONB 无服务端校验（字面量三处手钉）；orders.events 下标取键漂移 500 面（无生产写入方）；origin.created_at 恒 null。

## 能力矩阵（6/7）

客服✓ 中台✓ 素材✓ 切片✓ 考核✓ 连接层（协议✓演示面缺） 运营**未启动**。
