// 端点函数表：页面只 import api.*，路径与形状集中在这一处。

import { request, streamSse, type SseEvent } from './client'
import type {
  AssetDetail,
  AssetListItem,
  AssetVersion,
  AuditEntry,
  CustomerAnswerComplete,
  CustomerSessionCreated,
  KnowledgeGap,
  KnowledgeGapStatus,
  Operator,
  Product,
  ServiceAnswerComplete,
  ServiceSession,
  ServiceSessionDetail,
  ServiceSessionSummary,
} from './types'

/** 客服发问的 SSE 回调（thinking → delta* → complete 状态机，UX-NOTES §二点八）。 */
export interface AskHandlers {
  onThinking: (text: string) => void
  onDelta: (text: string) => void
  onComplete: (payload: ServiceAnswerComplete) => void
}

/** 顾客发问的 SSE 回调：同状态机，complete 载荷无 gap_id（服务端白名单裁剪）。 */
export interface CustomerAskHandlers {
  onThinking: (text: string) => void
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
  retryMachineWash: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/retry-machine-wash`, { method: 'POST' }),
  confirmFields: (assetId: number, versionNo: number, fields: Record<string, string>) =>
    request<AssetVersion>(`/assets/${assetId}/versions/${versionNo}/fields`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
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
