// 后端契约类型（apps/api routes 的响应模型对照，改动需与 API 票同步）。

export interface Operator {
  id: number
  username: string
}

export interface ProductRef {
  id: number
  name: string
  category: string
}

export type AssetStatus = 'ingested' | 'pending_review' | 'published'

/** 来源（CONTEXT「来源」词条，0025 血缘第一环）：登记端点按语义定值，前端不填报。 */
export type AssetSourceKind =
  | 'upload'
  | 'session_backflow'
  | 'clip_pick'
  | 'material_generated'
  | 'mcp_registered'
  | 'seed'

export interface AssetListItem {
  id: number
  title: string | null
  kind: string
  status: AssetStatus
  source_kind: AssetSourceKind
  product: ProductRef | null
  last_error: string | null
  current_published_version_no: number | null
  revising: boolean
}

// ---------- 知识缺口（routes/knowledge_gaps.py 契约） ----------

export type KnowledgeGapStatus = 'open' | 'resolved'

export interface GapProductRef {
  id: number
  name: string
}

/** 缺口不是资产：无检索/发布路径，解决只随发布发生（resolved_by_asset_id 指向那版资产）。 */
export interface KnowledgeGap {
  id: number
  question: string
  product: GapProductRef | null
  status: KnowledgeGapStatus
  resolved_by_asset_id: number | null
  created_at: string
  resolved_at: string | null
}

/** 对话 QA 对（第 12 刀/ADR 0035）：qa_pairs 字段的数组值，每对 {q, a} 非空串。 */
export interface QaPair {
  q: string
  a: string
}

/** 字段值三形状：机洗 {value,source:"machine"} / 弃权 {abstained:true} / 人洗 {value,source:"human"}。
 * 修订继承的确认值带 inherited，改动后丢掉。value 允许结构化值：dialogue 的
 * qa_pairs 为 QaPair[]（空数组=人洗确认「没有 QA」）。 */
export type FieldEntry =
  | { value: string | QaPair[]; source: 'machine' | 'human'; inherited?: boolean }
  | { abstained: true }

export interface AssetVersion {
  version_no: number
  object_key: string
  extracted_fields: Record<string, FieldEntry>
  confirmed_fields: Record<string, FieldEntry>
  published_at: string | null
}

export interface Publishability {
  publishable: boolean
  missing: string[]
  unconfirmed: string[]
}

export interface AssetDetail extends AssetListItem {
  versions: AssetVersion[]
  publishability: Publishability
}

export interface SpecRule {
  required?: boolean
}

export interface SpecValueEntry {
  value: string
  source: { asset_id: number; version: number }
}

export interface Product {
  id: number
  name: string
  category: string
  spec_schema: Record<string, SpecRule>
  spec_values: Record<string, SpecValueEntry>
}

export interface AuditEntry {
  id: number
  operator_id: number
  asset_id: number
  version_no: number
  action: string
  created_at: string
}

// ---------- CSV 批量导入（routes/assets.py 契约，第 9 刀） ----------

/** 导入报告的创建行：row 为 1 起数据行号（表头下一行 = 第 1 行）。 */
export interface CsvImportCreatedRow {
  row: number
  asset_id: number
  title: string
}

/** 导入报告的跳过行：行号 + 原因（空值/超长/登记失败，后端为准）。 */
export interface CsvImportSkippedRow {
  row: number
  reason: string
}

export interface CsvImportReport {
  created: CsvImportCreatedRow[]
  skipped: CsvImportSkippedRow[]
}

// ---------- 客服会话（routes/service.py 契约） ----------

/** 会话状态：active=进行中（可发问/可回流）；registered=已回流登记（只读）。 */
export type ServiceSessionStatus = 'active' | 'registered'

export interface ServiceSession {
  id: number
  status: ServiceSessionStatus
  created_at: string
  closed_at: string | null
  registered_asset_id: number | null
}

export interface ServiceSessionSummary extends ServiceSession {
  first_question: string | null
  message_count: number
  /** 会话来源（ADR 0021）：operator=控制台预览；customer=顾客通道 /customer
   * 签发（后端判据：customer_token 非空，不加 origin 列）。 */
  origin: 'operator' | 'customer'
}

/** 引用（CONTEXT 词条：指向一条证据 = 资产 ID + 版本号，检索用当前已发布版）。 */
export interface ServiceCitation {
  asset_id: number
  version_no: number
}

/** 工具调用记录（第 13 刀/ADR 0036）：SSE tool 事件、complete.tool 与消息表
 * tool 列同形状；result 是后端生成的一行摘要（灰底工具条「参数→结果」）。 */
export interface ToolCallRecord {
  name: string
  arg: string
  result: string
}

export interface ServiceMessage {
  id: number
  role: 'customer' | 'agent'
  content: string
  /** 仅 agent 消息有（refusal 为空数组，customer 为 null）。 */
  citations: ServiceCitation[] | null
  /** answer | refusal | handoff（仅 agent 消息；handoff=工具查无/故障转人工）。 */
  kind: 'answer' | 'refusal' | 'handoff' | null
  handoff: boolean | null
  /** 仅订单工具路径的 agent 消息非空（0036：随消息落库，重载还原工具条）。 */
  tool: ToolCallRecord | null
  created_at: string
}

export interface ServiceSessionDetail extends ServiceSession {
  messages: ServiceMessage[]
}

/** SSE complete 事件的负载（与后端 event_stream 尾事件一致）。
 * gap_id 仅 refusal 时非空（ADR 0030：运行时返回，消息表不加列——重载会话后
 * 芯片不重现，属契约口径）。fallback 仅 answer 且厂商生成失败降级模板时为
 * true（第 7 刀，同 gap_id 运行时口径）。tool 是第 13 刀新增：非订单路径恒为
 * null，两通道同形状不裁剪（单号由提问者自己给出，无内部敏感字段）；与
 * gap_id/fallback 不同——工具调用记录同时落进消息表（回放还原工具条）。 */
export interface ServiceAnswerComplete {
  message_id: number
  citations: ServiceCitation[]
  kind: 'answer' | 'refusal' | 'handoff'
  handoff: boolean
  gap_id: number | null
  fallback?: boolean
  tool: ToolCallRecord | null
}

// ---------- 顾客通道（routes/customer.py 契约，ADR 0021/0033） ----------

/** POST /customer/sessions 签发：令牌只在此响应完整出现一次，顾客侧自行保存。 */
export interface CustomerSessionCreated {
  session_id: number
  token: string
}

/** 顾客版 SSE complete：事件序与操作者版相同，但 gap_id 被服务端载荷白名单
 * 裁剪（顾客不暴露内部缺口 id）——类型上即不存在该字段。 */
export type CustomerAnswerComplete = Omit<ServiceAnswerComplete, 'gap_id'>
