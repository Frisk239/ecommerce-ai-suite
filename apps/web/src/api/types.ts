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

/** 字段值三形状：机洗 {value,source:"machine"} / 弃权 {abstained:true} / 人洗 {value,source:"human"}。
 * 修订继承的确认值带 inherited，改动后丢掉。 */
export type FieldEntry =
  | { value: string; source: 'machine' | 'human'; inherited?: boolean }
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
}

/** 引用（CONTEXT 词条：指向一条证据 = 资产 ID + 版本号，检索用当前已发布版）。 */
export interface ServiceCitation {
  asset_id: number
  version_no: number
}

export interface ServiceMessage {
  id: number
  role: 'customer' | 'agent'
  content: string
  /** 仅 agent 消息有（refusal 为空数组，customer 为 null）。 */
  citations: ServiceCitation[] | null
  /** answer | refusal（仅 agent 消息）。 */
  kind: 'answer' | 'refusal' | null
  handoff: boolean | null
  created_at: string
}

export interface ServiceSessionDetail extends ServiceSession {
  messages: ServiceMessage[]
}

/** SSE complete 事件的负载（与后端 event_stream 尾事件一致）。
 * gap_id 仅 refusal 时非空（ADR 0030：运行时返回，消息表不加列——重载会话后
 * 芯片不重现，属契约口径）。 */
export interface ServiceAnswerComplete {
  message_id: number
  citations: ServiceCitation[]
  kind: 'answer' | 'refusal'
  handoff: boolean
  gap_id: number | null
}
