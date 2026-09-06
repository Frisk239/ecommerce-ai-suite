// 领域类型，词汇跟 CONTEXT.md：资产、登记、发布、修订、引用、弃权、写回。
// 原型只有一份内存状态，所有页面读同一批 ID。

export type AssetKind = '文档' | '图片' | '视频' | '对话' | '素材'

// 资产状态只有三态（ADR 0004）
export type AssetState = '已接入' | '待人洗' | '已发布'

// 版本角色：published=当前已发布；review=待人洗工作版本（新资产或修订）；archived=旧已发布
export type VersionRole = 'published' | 'review' | 'archived'

export interface ExtractField {
  key: string // 保质期 / 净含量 / 材质 / …
  value: string | null // null = 弃权（ADR 0009：找不到就留空并记录，禁止编造）
  abstainReason?: string
  confirmed: boolean // 操作者是否已确认
  required?: boolean // 该资产的发布必填项（由种子 schema 决定，如食品才有保质期）
  inherited?: boolean // 修订版从已发布版继承且未被改动（确认状态随之继承）
}

export interface AssetVersion {
  v: number
  role: VersionRole
  createdAt: string
  content: string // 正文/转写摘要
  fields: ExtractField[]
  washNote?: string // 机洗说明（失败原因等）
}

export interface Asset {
  id: string // A-0001
  kind: AssetKind
  title: string
  state: AssetState
  productId?: string // 可挂商品，也可独立存在
  source: string // 血缘：从哪来
  sourceContent?: string // 登记时的原始内容（mock 对应对象键指向的字节）
  createdAt: string
  machineWash: { status: 'pending' | 'done' | 'failed'; reason?: string }
  versions: AssetVersion[]
  publishedV?: number // 当前已发布版本指针（ADR 0006）
}

export interface Product {
  id: string // P-0001
  name: string
  // 该商品适用的规格字段（按类目定 schema：食品才有保质期，杯类是材质）
  specSchema: string[]
  // 已发布写回的确认值（ADR 0010：未发布不写商品）
  specs: Record<string, string>
  sellingPoints: string[]
  stock: number
  stockUnit: string // 瓶 / 只（库存单位，查询接口口径）
}

// 底座模型配置：客服等模块通过「设为客服底座」选择当前使用的模型
export interface ModelConfig {
  id: string // M-01
  name: string
  type: 'base' | 'finetuned'
  provider: string // 提供方/部署方式
  endpoint: string
  params: { temperature: number; maxTokens: number }
  source?: string // 微调模型的血缘（来自哪次导出）
  createdAt: string
}

export interface Citation {
  assetId: string
  v: number
}

export interface ChatMessage {
  id: string
  role: 'customer' | 'agent'
  text: string
  citations?: Citation[]
  toolCall?: { name: string; args: string; result: string }
  refused?: boolean // 检索无命中 → 拒答，不幻觉
  handoff?: boolean // 转人工
  model?: { name: string; type: 'base' | 'finetuned'; source?: string }
  streaming?: boolean // 正在逐字输出（thinking=未出首字）
  pendingFull?: string // 流式的完整文本（刷新时兜底补全）
  interrupted?: boolean // 操作者点了停止，保留部分文本
  at: string
}

export interface ServiceSession {
  id: string
  startedAt: string
  messages: ChatMessage[]
  registeredAssetId?: string // 结束回流登记后指向新资产
}

export interface MaterialTask {
  id: string
  productId: string
  brief: string
  status: '生成中' | '已完成'
  origin?: 'ops' | 'clip' // 运营 Agent 编排发起 / 直播切片汇入
  output?: { title: string; body: string; refs: Citation[] }
  registeredAssetId?: string
}

export interface ClipCandidate {
  id: string
  timecode: string
  duration: string
  topic: string
  transcript: string
  productId: string
  registeredAssetId?: string
}

export type OpsStepStatus = 'pending' | 'running' | 'done' | 'failed'

export interface OpsStep {
  key: string
  name: string
  via: string // 走的是中台接口
  detail: string
  status: OpsStepStatus
}

export interface OpsRun {
  productId: string
  steps: OpsStep[]
  delivery: null | { at: string; channel: string }
  // compose 步生成的投放文案（跟当时已发布素材走，不是写死的）
  output?: { title: string; body: string; refs: Citation[] }
}

export interface ExportRecord {
  id: string
  createdAt: string
  // 导出时冻结的 资产ID · 版本号（ADR 0007：回放依据当时的版本，指针前移不漂移）
  refs: { assetId: string; v: number }[]
  size: string
}

// mock 训练任务：由导出自动生成（原型不实现真实训练），产物可注册为微调底座
export interface TrainingTask {
  id: string
  exportId: string
  assetCount: number
  modelName: string
  status: '已完成'
  createdAt: string
  registeredModelId?: string
}

export interface CoachScenario {
  id: string
  source: Citation // 场景必须来自已发布对话资产
  title: string
  opening: string // 顾客开场白
  rubric: { dim: string; max: number }[]
}

export interface CoachRecord {
  id: string
  scenarioId: string
  at: string
  scores: { dim: string; score: number }[]
  dialog: { role: 'customer' | 'trainee'; text: string }[] // 回放快照
  model?: string // 考核时的顾客扮演底座（与客服同一份模型配置）
}
