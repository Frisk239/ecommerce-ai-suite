# 两阶段写工具与忠实度闸：资格在代码、确认在人、覆盖不足不生成

状态：第 40 刀短对齐产出（Owner 裁决，goal §6.2.3/§6.2.8 收官）。映射 0018（无证据不调生成）、0043（模型提议/代码授权）、0007（citations 服务端定）、0024（缺口通道分叉），写工具新语义本文锁定。

## 语义

### 一、两阶段写工具（check_return_eligibility → 人确认 → mock create_return）

- **阶段一 资格查询（只读）**：模型可提议 `check_return_eligibility {"order_no": "SO-..."}`——代码校验（order_no 格式+订单存在+窗期规则在代码：签收后 15 天内[对齐演示库退货政策资产口径]）→ 返回结构化资格判定（eligible/ineligible+原因）。**资格校验在代码不在 prompt**（调研 §3：越狱的模型也发不出非法创建）。
- **阶段二 创建（写，人确认闸）**：模型**不能**直接提议 create_return（注册表不含它——写工具不在模型可提议集，0043 授权语义延伸）；阶段一返回 `confirmation_token`（一次性、绑定资格结果哈希、10 分钟有效）；**操作者在控制台确认**（会话挂起待确认卡片）→ 确认动作携带 token 调 `create_return`（代码再验 token+资格未过期）→ mock 执行（orders 表加 return 状态行或 memory mock——裁决：**orders.events 追加一条 return_requested 事件**，不改表结构）。
- **顾客通道不触发阶段二 UI**（无操作者确认面）：顾客问退货→资格查询+「已生成退货申请，等待客服确认」消息；确认动作归操作者（v1 单操作者语境）。工具轨迹：两步各落 tool 记录（SSE tool 事件）。

### 二、忠实度闸（生成前覆盖检查）

- 检索命中后、生成前：**计算证据覆盖度** = 命中块中与问句共享的有效 bigram 占问句有效 bigram 的比例（复用 query_terms）；**coverage < 0.4 且命中数 ≤1 → 降级证据模板**（不调模型——覆盖不足时模型大概率编造），带「模板回退」徽章（既有通道复用）；**零命中照旧拒答**（0018 不动）。阈值 0.4 为工程初值，评测集可校准（评测报告同报告口径）。
- 钉测：问句只擦边命中（共享 bigram 极少）→ 模板回退而非模型自由发挥。

### 三、prompt 逐句引用约束

- 生成 system prompt 追加：「每个事实句后标注 [1]/[2] 对应引用序号；证据未覆盖的内容不得陈述」；**解析层不强校验**（v1 prompt 约束+评测 faithfulness 抽验——Anthropic/Cohere 逐句引用的最小版，完整 citation span 解析留观察项）。

### 四、顾客 thumbs-down 分诊（反馈闭环，路 B 升级项 3）

- 顾客通道 AI 回答气泡加「没有帮助」按钮 → `POST /api/customer/sessions/{sid}/messages/{mid}/feedback {"helpful": false}`（顾客令牌鉴权，载荷白名单）；**分诊规则（代码）**：该消息 citations 非空 → **内容错** → 复审队列（assets.last_verified_at 置 null[撤销验证]+该资产详情 stale 徽章立即出现——Guru「负反馈触发 unverify」最小版）；citations 空 → 不可能（拒答无反馈按钮）——按钮仅 kind=answer 且 citations 非空时显示。反馈幂等（同消息一次）。
- 操作者面：撤销验证的资产在治理台「未验证」过滤可见（既有 stale 面盖自然承接）。

## 动机

goal §6.2.3「写操作两阶段、资格在代码」+§6.2.8「死亡问：工具谁授权/答错怎么追」；路 B 调研 Guru/Intercom 反馈分诊；路 C 调研覆盖度-幻觉关联与逐句引用。

## 后面

- 工具注册表加 check_return_eligibility（可提议）；create_return **不在注册表**（只能由确认端点走）；orders 无迁移（events JSONB 追加）。
- Out：真退款执行、坐席队列、citation span 强解析、覆盖率阈值 UI、thumbs-up。
