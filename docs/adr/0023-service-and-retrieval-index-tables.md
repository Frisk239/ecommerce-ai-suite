# 客服会话与检索索引表：领域 ADR 的直接映射

状态：随 `feat/service-citation` 提出，PR review 把关。与 0022 同纪律：**只做已锁语义映射，不引入新数据语义**；发现夹带即拦下走 grill。

## 映射表

| 表 | 领域来源 | 关键列与约束 |
| --- | --- | --- |
| `retrieval_chunks` | CONTEXT「检索索引=已发布资产切块后的派生视图，不是中台对象」；ADR 0017（索引归中台、客服与连接层共用）；ADR 0004（只检索已发布） | id、asset_id、version_no、seq、chunk 文本；**只在发布事务内写入**（切块写入=发布的一部分，CONTEXT「已发布」词条）；发布事务外无写入路径；不含待人洗/已接入内容 |
| `service_sessions` | CONTEXT「会话=客服里进行中的对话…结束后由操作者回流登记为资产（种类=对话）」；ADR 0021（预览与顾客接口同一引擎） | id、status（active/ended/registered——运行态流转；**closed 从未实现**，0002 docstring 笔误，ended 为第 80 刀顾客主动结束态）、created_at、closed_at（顾客结束或操作者回流登记时落值，之后不改写）；**运行态实体，不是第三个中台对象**（词条 Avoid）；回流登记后状态置 registered 并指向登记出的资产 |
| `service_messages` | 同上；引用（ADR 0007） | id、session_id、role（customer/agent）、content、citations JSONB（`[{asset_id, version_no}]`，仅 agent 消息）、kind（answer/refusal/handoff——拒答与转人工显性 0018）、created_at |

## 语义对照要点（不许偏）

- 检索单元=已发布版本的切块；命中集合永远 ⊆ 当前已发布版本（0004/0006 指针语义）。
- 引用必含版本号（0007）；同一会话旧消息的引用不随后续发布漂移（回放按当时版本）。
- 拒答是消息种类（refusal）不是异常：无证据→拒答消息+handoff 标记，引擎不得编造（0018）。
- 回流登记复用第 2 刀登记路径：字节（转写文本）先落对象存储，才建 kind=dialogue 资产行（0013）；对话无规格必填（0019）。
- 不为顾客通道、LLM、工具调用、多轮记忆预埋列（防 speculative generality；后续刀按需增列再议）。

## 工程选型（工程级，非领域）

检索=中文词法打分的 DB 内查询（无向量、无外部模型）；SSE 用 StreamingResponse；回答第一版=证据组装模板。
