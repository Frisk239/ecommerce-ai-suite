// 端点函数表：页面只 import api.*，路径与形状集中在这一处。

import { request, requestText, streamSse, type SseEvent } from './client'
import type {
  AssetDetail,
  AssetListItem,
  AssetVersion,
  AuditEntry,
  CustomerAnswerComplete,
  CustomerSessionCreated,
  CsvImportReport,
  KnowledgeGap,
  KnowledgeGapStatus,
  MaterialTask,
  Operator,
  Product,
  QaPair,
  ServiceAnswerComplete,
  ServiceSession,
  ServiceSessionDetail,
  ServiceSessionSummary,
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

  // 商品与留痕
  listProducts: () => request<Product[]>('/products'),
  getProduct: (productId: number) => request<Product>(`/products/${productId}`),
  listAudit: (assetId: number) => request<AuditEntry[]>(`/audit?assetId=${assetId}`),

  // 知识缺口（只读列表：产生随拒答、解决随发布，无创建/关闭端点）
  listKnowledgeGaps: (status: KnowledgeGapStatus = 'open') =>
    request<KnowledgeGap[]>(`/knowledge-gaps?status=${status}`),

  // 素材中心（第 17 刀/ADR 0038）：任务不是中台对象；建任务请求内同步执行
  // LLM 生成+规则质检（≤20s，同回流机洗的等待面），返回即稳定态。全部操作者鉴权。
  createMaterialTask: (productId: number) =>
    request<MaterialTask>('/material/tasks', {
      method: 'POST',
      body: JSON.stringify({ product_id: productId }),
    }),
  listMaterialTasks: () => request<MaterialTask[]>('/material/tasks'),
  getMaterialTask: (taskId: number) => request<MaterialTask>(`/material/tasks/${taskId}`),
  approveMaterialTask: (taskId: number) =>
    request<MaterialTask>(`/material/tasks/${taskId}/approve`, { method: 'POST' }),
  rejectMaterialTask: (taskId: number) =>
    request<MaterialTask>(`/material/tasks/${taskId}/reject`, { method: 'POST' }),
  retryMaterialTask: (taskId: number) =>
    request<MaterialTask>(`/material/tasks/${taskId}/retry`, { method: 'POST' }),

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
  createCustomerSession: () =>
    request<CustomerSessionCreated>('/customer/sessions', { method: 'POST' }),
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
}
