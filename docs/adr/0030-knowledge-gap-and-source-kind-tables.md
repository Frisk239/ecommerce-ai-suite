# 知识缺口表与来源列：领域 ADR 的直接映射

状态：随 `feat/knowledge-gap` 提出，PR review 把关。与 0022/0023 同纪律：**只做已锁语义映射，不引入新数据语义**；发现夹带即拦下走 grill。

## 映射表

| 表/列 | 领域来源 | 关键列与约束 |
| --- | --- | --- |
| `knowledge_gaps` | ADR 0024；CONTEXT「知识缺口=无证据拒答时留下的待补项，可挂商品。不是资产：不能检索、不能发布」 | id、question（顾客原问）、product_id 可空、status（open/resolved）、resolved_by_asset_id 可空、resolved_at 可空、created_at；**无检索/发布路径**；精确幂等：同 question 文本且 open 时不新建（工程裁决，防重复拒答灌水，0024 未锁去重策略） |
| `assets.source_kind` | ADR 0025；CONTEXT「来源=登记时必填：上传、会话回流、切片拣选、素材生成、连接层登记、种子」 | NOT NULL 枚列：`upload / session_backflow / clip_pick / material_generated / mcp_registered / seed`；存量回填 `upload`；由**登记端点语义**决定（上传接口=upload、回流端点=session_backflow），不让调用方自由填报 |
| `service_messages` ↔ 缺口 | 0024（拒答产生缺口） | **不加列**：SSE `complete` 事件运行时返回 `gap_id` 供前端芯片跳转；缺口表自存 question 原文即留痕。避免为 UI 便利在消息表加关联列（0023「不预埋」纪律） |

## 语义对照要点（不许偏）

- 缺口只在**无证据拒答**路径产生（0018 refusal）；工具失败转人工不产生（本刀无工具，代码路径只挂 refusal，契约由测试钉住）。
- 「补文档」仍是普通登记：`assets.register` 增可选 `knowledge_gap_id`（登记关联，原型 `fillsGapId` 语义）；**发布事务内**把该缺口标 resolved（resolved_by_asset_id=该资产）——解决动作随发布发生，不提供独立「手动关闭缺口」端点（0024：操作者从缺口补文档或开修订；修订流属下一刀）。
- 会话回流仍是独立按钮（0024「整段会话回流是另一件事」），不因缺口存在而自动回流。
- source_kind 不进检索、不生成新对象（0025/0026）；资产详情「来源」徽章是血缘派生视图的第一环（0026），本刀只落这一环，不做完整血缘拼装。

## 工程选型（工程级，非领域）

迁移 0003：新表 + `assets.source_kind` 列回填；旧端点行为兼容（register 缺省 source_kind 时服务端按端点语义定值，不破坏既有调用）。
