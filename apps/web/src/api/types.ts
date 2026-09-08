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

// ---------- 血缘视图（GET /assets/{id}/lineage 契约，第 20 刀/ADR 0026） ----------

/** 血缘是派生视图（0026）：资产详情上从既有数据拼出来——无表、只读、
 * 不进检索、不能发布。「从哪来/版本审计」由元数据与留痕面板呈现，
 * 本契约的 usages 三块回答「被谁用过」。 */
export interface LineageOrigin {
  source_kind: string
  /** assets 行不存登记时间（本刀零新列）：恒 null，如实不发明时间。 */
  created_at: string | null
}

export interface LineageAuditEvent {
  at: string
  action: string // publish | confirm | rollback
  version_no: number
  operator: string
}

export interface LineageCitationSample {
  session_id: number
  /** 引用回答对应的顾客问句（后端 60 字截断）。 */
  question: string
  version_no: number
  at: string
}

export interface LineageCitationsBlock {
  /** 同条件计数：引用本资产的消息条数（样例上限之外仍如实）。 */
  total: number
  samples: LineageCitationSample[] // 时间倒序，最多 10 条
}

export interface LineageWriteback {
  at: string
  version_no: number
  operator: string
  product_id: number | null
  /** fields 键不存在：audit_log 不存写回字段名（如实拼装）。 */
}

export interface LineageCoachingUsage {
  record_id: number
  question: string // 题面快照（截断）
  version_no: number
  at: string
}

export interface AssetLineage {
  origin: LineageOrigin
  versions_audit: LineageAuditEvent[]
  usages: {
    citations: LineageCitationsBlock
    writebacks: LineageWriteback[]
    coaching: LineageCoachingUsage[]
  }
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

// ---------- 素材中心任务（routes/material.py 契约，第 17 刀/ADR 0038） ----------

/** 任务五态（0038 状态机；不是资产三态，与 middle-plate 无关）：
 * queued=排队 / running=进行中 / pending_qc=待抽检（规则质检过线等人）/
 * registered=已登记（终态，asset_id 指向登记出的素材资产）/ failed=失败（可重试）。 */
export type MaterialTaskStatus =
  | 'queued'
  | 'running'
  | 'pending_qc'
  | 'registered'
  | 'failed'

export interface MaterialTask {
  id: number
  product_id: number
  product_name: string
  status: MaterialTaskStatus
  title: string | null
  content: string | null
  last_error: string | null
  asset_id: number | null
  created_at: string
}

// ---------- 直播切片候选（routes/clips.py 契约，第 18 刀/ADR 0014/0015/0039） ----------

/** 候选两态（0039 单向状态机；不是资产三态）：pending=待拣选 /
 * registered=已登记（registered_asset_id 回执锚指向登记出的视频资产）。
 * 候选不是中台对象（0014）：transcript 是种子 mock 的 ASR 转写，
 * 登记字节=「[timecode_start-timecode_end] 转写」文本（非 mp4，0039）。 */
export type ClipCandidateStatus = 'pending' | 'registered'

export interface ClipCandidate {
  id: number
  product_id: number
  product_name: string
  status: ClipCandidateStatus
  timecode_start: string
  timecode_end: string
  transcript: string
  source_video_label: string
  registered_asset_id: number | null
  created_at: string
}

// ---------- 销售考核（routes/coach.py 契约，第 19 刀/ADR 0040） ----------

/** 题源锚（0007 引用口径：题面=该资产该已发布版本里的内容）。
 * source=qa：confirmed 问答对逐对成题（pair_index 定位）；
 * source=transcript：弃权/空时转写首问兜底（standard_answer 为 null）。 */
export interface CoachQuestionKey {
  asset_id: number
  version_no: number
  source: 'qa' | 'transcript'
  pair_index: number | null
}

/** 三维分（原型 RUBRIC 冻结口径，0040）：口径准确 40 / 证据贴合 30 / 服务语气 30。 */
export interface CoachScore {
  accurate: number
  evidence: number
  tone: number
  comment: string
}

export interface CoachQuestion {
  key: CoachQuestionKey
  question: string
  standard_answer: string | null
  asset_title: string | null
}

/** 考核记录不是中台对象（0027）：status 由 score 是否 NULL 派生——
 * unscored=LLM 未配置/失败/坏输出（无降级，last_error 记原因），可重评。 */
export interface CoachRecord {
  id: number
  operator_name: string
  question_key: CoachQuestionKey
  question_text: string
  standard_answer: string | null
  trainee_answer: string
  score: CoachScore | null
  model_name: string | null
  last_error: string | null
  status: 'scored' | 'unscored'
  created_at: string
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
