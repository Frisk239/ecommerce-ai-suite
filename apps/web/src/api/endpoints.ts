// 端点函数表：页面只 import api.*，路径与形状集中在这一处。

import { request, requestText, streamSse, type SseEvent } from './client'
import type {
  AssetDetail,
  AssetLineage,
  AssetListItem,
  AssetVersion,
  AuditEntry,
  ClipBindResult,
  ClipCandidate,
  ClipRecording,
  ClipTranscribeResult,
  ClipAsrStatus,
  ClipFrameStatus,
  CoachQuestion,
  CoachQuestionKey,
  CoachRecord,
  ComposeTask,
  ComposeTemplate,
  ComposeTtsStatus,
  ComposePublishResult,
  ConfirmReturnResult,
  CustomerAnswerComplete,
  CustomerSessionCreated,
  CustomerSessionEnded,
  CustomerSessionResume,
  CsvImportReport,
  FeedbackResult,
  FrameCandidatesResult,
  FrameRegisterResult,
  HandoffTicket,
  HandoffTicketCreate,
  HandoffTicketResult,
  KnowledgeGap,
  KnowledgeGapStatus,
  MaterialImggenStatus,
  MaterialTask,
  Operator,
  OpsRun,
  Product,
  ProductCreate,
  ProductUpdate,
  QaPair,
  ServiceAnswerComplete,
  ServiceSession,
  ServiceSessionDetail,
  ServiceSessionSummary,
  SessionRating,
  StatsOverview,
  ToolCallRecord,
} from './types'

/** 客服发问的 SSE 回调（thinking → [tool] → delta* → complete 状态机，
 * UX-NOTES §二点八；tool 仅订单工具路径出现，第 13 刀/ADR 0036）。 */
export interface AskHandlers {
  onThinking: (text: string) => void
  /** 工具调用条数据（{name, arg, result}）：complete 前到达，工具条即时呈现。 */
  onTool: (record: ToolCallRecord) => void
  onDelta: (text: string) => void
  onComplete: (payload: ServiceAnswerComplete) => void
}

/** 顾客发问的 SSE 回调：同状态机，complete 载荷无 gap_id（服务端白名单裁剪；
 * tool 不裁剪——两通道同形状，0036）。 */
export interface CustomerAskHandlers {
  onThinking: (text: string) => void
  onTool: (record: ToolCallRecord) => void
  onDelta: (text: string) => void
  onComplete: (payload: CustomerAnswerComplete) => void
}

export const api = {
  // 认证
  login: (username: string, password: string) =>
    request<Operator>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ detail: string }>('/auth/logout', { method: 'POST' }),
  me: () => request<Operator>('/auth/me'),

  // 资产
  // 第 51 刀：`/assets` 支持 `?source_kind=` 服务端过滤（后端有接口式用例钉着）；
  // 控制台内的来源筛选走客户端（一次拉全量、数据量小），故这里不另开方法。
  listAssets: () => request<AssetListItem[]>('/assets'),
  getAsset: (assetId: number) => request<AssetDetail>(`/assets/${assetId}`),
  registerAsset: (form: FormData) => request<AssetDetail>('/assets/register', { method: 'POST', body: form }),
  // CSV 批量导入（第 9 刀）：上传通道的批量形态，逐行登记尽力而为，报告即答案
  importCsv: (form: FormData) =>
    request<CsvImportReport>('/assets/import-csv', { method: 'POST', body: form }),
  retryMachineWash: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/retry-machine-wash`, { method: 'POST' }),
  // 人洗确认：字符串字段值为 string；dialogue 的 qa_pairs 为 QaPair[]（ADR 0035）
  confirmFields: (
    assetId: number,
    versionNo: number,
    fields: Record<string, string | QaPair[]>,
  ) =>
    request<AssetVersion>(`/assets/${assetId}/versions/${versionNo}/fields`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
  // 版本正文（对象存储字节 text/plain）：dialogue 人洗要看见转写（第 12 刀）
  getVersionText: (assetId: number, versionNo: number) =>
    requestText(`/assets/${assetId}/versions/${versionNo}/text`),
  publishAsset: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/publish`, { method: 'POST' }),
  openRevision: (assetId: number, knowledgeGapId?: number) =>
    request<AssetDetail>(`/assets/${assetId}/revisions`, {
      method: 'POST',
      body: JSON.stringify(knowledgeGapId !== undefined ? { knowledge_gap_id: knowledgeGapId } : {}),
    }),
  rollbackAsset: (assetId: number, versionNo: number) =>
    request<AssetDetail>(`/assets/${assetId}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ version_no: versionNo }),
    }),
  // 生命周期出口三件（第 28 刀/ADR 0042）：待人洗版换正文（multipart，同登记
  // 通道）、放弃未发布修订、废弃从未发布的失败资产。后端各自带状态机闸门 409。
  replaceVersionBytes: (assetId: number, versionNo: number, form: FormData) =>
    request<AssetDetail>(`/assets/${assetId}/versions/${versionNo}/bytes`, {
      method: 'PUT',
      body: form,
    }),
  discardRevision: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/revisions/discard`, { method: 'POST' }),
  discardAsset: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/discard`, { method: 'POST' }),
  // 重新验证（第 39 刀保鲜）：仅已发布资产可调（后端 409 闸门）；刷新
  // last_verified_at 并留痕 audit action=verify。返回更新后的资产详情。
  verifyAsset: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/verify`, { method: 'POST' }),

  // 血缘视图（第 20 刀/ADR 0026）：派生拼装只读，详情加载后独立请求
  getAssetLineage: (assetId: number) => request<AssetLineage>(`/assets/${assetId}/lineage`),

  // 商品与留痕
  listProducts: () => request<Product[]>('/products'),
  getProduct: (productId: number) => request<Product>(`/products/${productId}`),
  // 商品维度的素材聚合（第 94a 刀，ADR 0051）：该商品关联的全部资产（图/视频/
  // 文案/文档…同一 AssetOut 形状）。素材库不另建页，聚合做在商品维度。
  listProductAssets: (productId: number) =>
    request<AssetListItem[]>(`/products/${productId}/assets`),
  // 上新（第 41 刀）：登录操作者在控制台建商品；409 重名、422 校验。
  createProduct: (payload: ProductCreate) =>
    request<Product>('/products', { method: 'POST', body: JSON.stringify(payload) }),
  // 改档/改价（第 41 刀）：商品列直写即时生效；改价记 audit 产品档。
  updateProduct: (productId: number, payload: ProductUpdate) =>
    request<Product>(`/products/${productId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  listAudit: (assetId: number) => request<AuditEntry[]>(`/audit?assetId=${assetId}`),

  // 知识缺口（只读列表：产生随拒答、解决随发布，无创建/关闭端点）
  listKnowledgeGaps: (status: KnowledgeGapStatus = 'open') =>
    request<KnowledgeGap[]>(`/knowledge-gaps?status=${status}`),

  // 素材中心（第 17 刀/ADR 0038；第 98 刀内容套件/ADR 0055）：任务不是中台对象；
  // 建任务请求内同步执行 LLM 生成+双闸质检+配图，返回即稳定态。全部操作者鉴权。
  // 建任务/重试的等待面必须宽于默认 15s 超时：文案 ≤20s + LLM 质检 ≤20s +
  // 配图 ≤60s，给 120s（要配图时才用满，纯文案远低于此）。
  createMaterialTask: (productId: number, template: string = 'station', withImage: boolean = false) =>
    request<MaterialTask>(
      '/material/tasks',
      {
        method: 'POST',
        body: JSON.stringify({ product_id: productId, template, with_image: withImage }),
      },
      120_000,
    ),
  listMaterialTasks: () => request<MaterialTask[]>('/material/tasks'),
  getMaterialTask: (taskId: number) => request<MaterialTask>(`/material/tasks/${taskId}`),
  // 文生图配置状态（第 98 刀）：「生成配图」开关禁用判据（无 key 禁用并提示）。
  getMaterialImggenStatus: () => request<MaterialImggenStatus>('/material/imggen/status'),
  // 配图暂存字节预览（操作者 cookie 同源直取；抽检通过登记后 404，去治理台看）
  materialTaskImageUrl: (taskId: number) => `/api/material/tasks/${taskId}/image`,
  approveMaterialTask: (taskId: number) =>
    request<MaterialTask>(`/material/tasks/${taskId}/approve`, { method: 'POST' }),
  // 第 48 刀：打回可带理由（≤200 字）——写进 last_error 的详情段，运营看得见
  // 「为什么被打回」；reason 传 null 与不传等价（无理由）。
  rejectMaterialTask: (taskId: number, reason: string | null = null) =>
    request<MaterialTask>(`/material/tasks/${taskId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  retryMaterialTask: (taskId: number) =>
    request<MaterialTask>(`/material/tasks/${taskId}/retry`, { method: 'POST' }, 120_000),

  // 内容成片（第 98b 刀/ADR 0056）：AI 选材+排版出时间线候选+预览成片+剪映
  // 草稿，人审改后 publish 经双闸复用登记 material 资产。plan 是同步请求
  // （ffprobe 实测+TTS ≤60s+ffmpeg 合成 ≤120s）——超时给 240s，别在服务端
  // 还在合成时先断（断了操作者只看到「网络失败」而库里任务照落）。
  planVideoCompose: (productId: number, template: ComposeTemplate) =>
    request<ComposeTask>(
      '/video-compose/plan',
      { method: 'POST', body: JSON.stringify({ product_id: productId, template }) },
      240_000,
    ),
  listVideoComposeTasks: () => request<ComposeTask[]>('/video-compose/tasks'),
  getVideoComposeTtsStatus: () => request<ComposeTtsStatus>('/video-compose/tts/status'),
  videoComposePreviewUrl: (taskId: number) => `/api/video-compose/${taskId}/preview`,
  videoComposeDraftUrl: (taskId: number) => `/api/video-compose/${taskId}/draft`,
  videoComposeFinalUrl: (taskId: number) => `/api/video-compose/${taskId}/final`,
  // publish 人闸门：可选上传剪映导出的成品 mp4（不传=用预览成片）；文案过
  // 双闸（无 LLM/判定不过 422，任务停在 planned 可改后重发）。
  publishVideoCompose: (taskId: number, finalVideo: File | null) => {
    const form = new FormData()
    if (finalVideo) form.append('final_video', finalVideo)
    return request<ComposePublishResult>(
      `/video-compose/${taskId}/publish`,
      { method: 'POST', body: form },
      120_000,
    )
  },

  // 直播切片（第 18 刀/ADR 0014/0039；第 46 刀真链路）：候选不是中台对象；
  // 上传源录像（.mp4，≤200MB；上传即绑「尚无源录像」的 pending 候选）后，pick
  // 对有源录像的候选真切 mp4 片段登记为 kind=视频/来源=切片拣选资产，无源录像
  // 的仍写时间码转写文本。返回登记结果列表供卡片换「已登记 A-xxxx」。批量含
  // 已登记整体 409（事务不落）；切失败 422 且该候选保持可重拣。
  listClipCandidates: () => request<ClipCandidate[]>('/clips/candidates'),
  // 第 49 刀：源录像列表（改绑选择器的数据源；created_at 降序、≤20）
  listClipRecordings: () => request<ClipRecording[]>('/clips/recordings'),
  // 第 49 刀：改绑——candidateIds 为 null 时改绑全部待拣候选（这是本条把
  // 「绑错就锁死」解锁的动作；已登记候选一律不动，指定了会 409）
  bindClipRecording: (recordingId: number, candidateIds: number[] | null) =>
    request<ClipBindResult>(`/clips/recordings/${recordingId}/bind`, {
      method: 'POST',
      body: JSON.stringify({ candidate_ids: candidateIds }),
    }),
  uploadClipRecording: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    // 上传超时宽于默认 15s：原始录像最大 200MB，慢链路上传会超过默认阈值
    return request<ClipRecording>('/clips/recordings', { method: 'POST', body: form }, 120_000)
  },
  // 第 93 刀（ADR 0050）：自动转写 + 配置状态。
  // 转写是**同步**请求（提音轨 + 逐块云 ASR，服务端总预算 300s——审计 19 起
  // 多块串行共享硬上限，超限服务端先停并保留部分候选）——前端超时给到
  // 320s，别在服务端还在跑时先断（断了操作者只会看到「网络失败」而库里候选照落）。
  getClipAsrStatus: () => request<ClipAsrStatus>('/clips/asr/status'),
  transcribeClipRecording: (recordingId: number, productId: number | null = null) =>
    request<ClipTranscribeResult>(
      `/clips/recordings/${recordingId}/transcribe`,
      { method: 'POST', body: JSON.stringify({ product_id: productId }) },
      320_000,
    ),
  pickClips: (ids: number[]) =>
    request<AssetListItem[]>('/clips/candidates/pick', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  // 第 94c 刀（ADR 0053）：洗帧到素材库——帧打分复用 94a 的 VLM（同一把 key），
  // 无 key 时候选端点 409（fail-closed），此状态给按钮禁用判据。
  getFrameWashStatus: () => request<ClipFrameStatus>('/clips/frames/status'),
  // 洗帧候选是**同步**请求（ffprobe/ffmpeg + 逐帧 VLM，分钟级）——超时宽于
  // 默认 15s，别在服务端还在打分时先断（断了操作者只看到「网络失败」）。
  listFrameCandidates: (assetId: number) =>
    request<FrameCandidatesResult>(
      `/assets/${assetId}/frame-candidates`,
      { method: 'POST' },
      300_000,
    ),
  registerFrame: (assetId: number, atSecond: number, vlmNote?: string) =>
    request<FrameRegisterResult>(
      `/assets/${assetId}/frames`,
      { method: 'POST', body: JSON.stringify({ at_second: atSecond, vlm_note: vlmNote ?? null }) },
      150_000,
    ),

  // 销售考核（第 19 刀/ADR 0040）：题库从已发布对话动态推导；作答/重评请求内
  // 同步 LLM 打分（≤20s，超时宽同素材生成）。打分失败不抛：200 + unscored 态。
  listCoachQuestions: () => request<CoachQuestion[]>('/coach/questions'),
  createCoachAttempt: (questionKey: CoachQuestionKey, answer: string) =>
    request<CoachRecord>(
      '/coach/attempts',
      {
        method: 'POST',
        body: JSON.stringify({ question_key: questionKey, answer }),
      },
      30_000,
    ),
  listCoachRecords: () => request<CoachRecord[]>('/coach/records'),
  rescoreCoachRecord: (recordId: number) =>
    request<CoachRecord>(`/coach/records/${recordId}/rescore`, { method: 'POST' }, 30_000),

  // 运营 Agent（第 22 刀/ADR 0041）：编排轨迹不是中台对象；建任务/重试请求内
  // 同步执行三步（gen_material 含 LLM ≤20s，超时宽同素材生成）。投放=渠道动作。
  listOpsRuns: () => request<OpsRun[]>('/ops/runs'),
  createOpsRun: (productId: number) =>
    request<OpsRun>('/ops/runs', { method: 'POST', body: JSON.stringify({ product_id: productId }) }, 30_000),
  retryOpsRun: (runId: number) =>
    request<OpsRun>(`/ops/runs/${runId}/retry`, { method: 'POST' }, 30_000),
  deliverOpsRun: (runId: number) => request<OpsRun>(`/ops/runs/${runId}/deliver`, { method: 'POST' }),

  // 客服会话（预览与顾客接口同一引擎；传输用 SSE，不用 EventSource）
  createServiceSession: () =>
    request<ServiceSession>('/service/sessions', { method: 'POST' }),
  listServiceSessions: () => request<ServiceSessionSummary[]>('/service/sessions'),
  getServiceSession: (sessionId: number) => request<ServiceSessionDetail>(`/service/sessions/${sessionId}`),
  registerServiceSession: (sessionId: number) =>
    request<AssetDetail>(`/service/sessions/${sessionId}/register`, { method: 'POST' }),
  askService: (sessionId: number, content: string, handlers: AskHandlers, signal: AbortSignal) =>
    streamSse(
      `/service/sessions/${sessionId}/messages`,
      { content },
      (evt: SseEvent) => {
        if (evt.event === 'thinking' && typeof evt.data.text === 'string') {
          handlers.onThinking(evt.data.text)
        } else if (evt.event === 'tool' && typeof evt.data.name === 'string') {
          // 0036 订单工具：{name, arg, result} 直传给工具条（两通道同形状）
          handlers.onTool(evt.data as unknown as ToolCallRecord)
        } else if (evt.event === 'delta' && typeof evt.data.text === 'string') {
          handlers.onDelta(evt.data.text)
        } else if (evt.event === 'complete') {
          handlers.onComplete(evt.data as unknown as ServiceAnswerComplete)
        }
      },
      signal,
    ),

  // 顾客通道（ADR 0021/0033）：无登录，Bearer 会话令牌；同一引擎、同事件序，
  // complete 不带 gap_id。顾客无列表/详情/回流端点。
  createCustomerSession: (headers?: Record<string, string>) =>
    request<CustomerSessionCreated>('/customer/sessions', { method: 'POST', headers }),
  // 结束会话（第 80 刀）：active -> ended，幂等；结束后发问 409，评分/反馈/
  // 联系方式仍可用（「关对话流，不关善后通道」）。无 body。
  endCustomerSession: (sessionId: number, token: string) =>
    request<CustomerSessionEnded>(`/customer/sessions/${sessionId}/end`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }),
  // 会话续接（第 95 刀）：「current」= Bearer 令牌所指会话。active 恢复对话流
  //（消息重放+继续问）；ended/registered 只回放（锁输入，善后照旧）；401
  //（过期/无效）由页面清存档走新会话。
  getCustomerCurrentMessages: (token: string) =>
    request<CustomerSessionResume>('/customer/sessions/current/messages', {
      headers: { Authorization: `Bearer ${token}` },
    }),
  askCustomer: (
    sessionId: number,
    token: string,
    content: string,
    handlers: CustomerAskHandlers,
    signal: AbortSignal,
  ) =>
    streamSse(
      `/customer/sessions/${sessionId}/messages`,
      { content },
      (evt: SseEvent) => {
        if (evt.event === 'thinking' && typeof evt.data.text === 'string') {
          handlers.onThinking(evt.data.text)
        } else if (evt.event === 'tool' && typeof evt.data.name === 'string') {
          // 顾客通道同样有工具条：单号本由提问者给出，无裁剪（0036）
          handlers.onTool(evt.data as unknown as ToolCallRecord)
        } else if (evt.event === 'delta' && typeof evt.data.text === 'string') {
          handlers.onDelta(evt.data.text)
        } else if (evt.event === 'complete') {
          handlers.onComplete(evt.data as unknown as CustomerAnswerComplete)
        }
      },
      signal,
      { Authorization: `Bearer ${token}` },
    ),

  // 顾客对单条回答的 thumbs（第 40 刀 + 第 48 刀）：helpful=false 触发分诊——
  // 逐 citation 资产撤销验证；helpful=true 只记不诊（点赞不进复审队列）。
  // 同消息二次反馈 409（幂等）。401/409 文案由后端 detail 呈现。
  leaveFeedback: (sessionId: number, token: string, messageId: number, helpful: boolean) =>
    request<FeedbackResult>(`/customer/sessions/${sessionId}/messages/${messageId}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ helpful }),
      headers: { Authorization: `Bearer ${token}` },
    }),

  // 会话评分（第 48 刀，CSAT）：一次评 1–5 星 + 可选留言；评分可改（重复提交=覆盖式留最新，第 71 刀）。
  rateSession: (sessionId: number, token: string, score: number, comment: string | null) =>
    request<SessionRating>(`/customer/sessions/${sessionId}/rating`, {
      method: 'POST',
      body: JSON.stringify({ score, comment }),
      headers: { Authorization: `Bearer ${token}` },
    }),

  // 两阶段写阶段二（第 40 刀，ADR 0044 §一）：操作者对资格消息确认退货；
  // 服务端验签+资格重查后写订单事件并落 create_return 轨迹（刷新会话即见）。
  confirmReturn: (sessionId: number, messageId: number, confirmationToken: string) =>
    request<ConfirmReturnResult>(`/service/sessions/${sessionId}/confirm-return`, {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId, confirmation_token: confirmationToken }),
    }),

  // 转人工工单（第 42 刀，ADR 0046）：顾客提交联系方式（bearer 会话令牌，
  // 整表可跳过——不提交也能拿到工单，提交只是让它可回访）；操作者结单
  // （pending -> resolved，非法状态后端 409）。
  submitHandoffTicket: (
    sessionId: number,
    ticketId: number,
    token: string,
    payload: HandoffTicketCreate,
  ) =>
    request<HandoffTicketResult>(`/customer/sessions/${sessionId}/handoff-tickets/${ticketId}`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { Authorization: `Bearer ${token}` },
    }),
  resolveHandoffTicket: (ticketId: number) =>
    request<HandoffTicket>(`/service/handoff-tickets/${ticketId}/resolve`, { method: 'POST' }),

  // 操作者仪表（第 43 刀）：现有表现场聚合，一次请求喂侧栏角标、总览趋势条与
  // 反馈汇总三处；固定近 7 日窗，无轮询（shell 每次导航刷一次）。
  getStatsOverview: () => request<StatsOverview>('/stats/overview'),
}
