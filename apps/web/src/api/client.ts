// 统一 fetch 层：credentials 恒为 include、15s 超时、错误结构化。
// - 401（非登录接口本身）广播 auth-expired 事件，路由守卫收到后跳登录；
// - FastAPI 的 detail（字符串或对象）解析进 ApiError.detail，发布 422 的
//   {code:"publish_gate_failed", missing, unconfirmed} 由 parsePublishGate 取出。

const API_BASE: string = import.meta.env.VITE_API_URL ?? '/api'
const DEFAULT_TIMEOUT_MS = 15000

const AUTH_EXPIRED_EVENT = 'suite:auth-expired'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError
}

/** 会话失效广播的订阅（AuthContext 用）；登录接口自身的 401 不触发。 */
export function onAuthExpired(listener: () => void): () => void {
  window.addEventListener(AUTH_EXPIRED_EVENT, listener)
  return () => window.removeEventListener(AUTH_EXPIRED_EVENT, listener)
}

function emitAuthExpired(): void {
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}

function detailToMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim() !== '') return detail
  if (status === 401) return '登录已失效，请重新登录'
  if (status === 0) return '网络请求失败或超时，请重试'
  return `请求失败（HTTP ${status}）`
}

/** 把任意抛错转成可展示文案。 */
export function detailText(err: unknown): string {
  if (isApiError(err)) return detailToMessage(err.detail, err.status)
  return '请求失败，请重试'
}

export interface PublishGateDetail {
  missing: string[]
  unconfirmed: string[]
}

/** 发布 422：detail = {code:"publish_gate_failed", missing:[], unconfirmed:[]}。 */
export function parsePublishGate(err: unknown): PublishGateDetail | null {
  if (!isApiError(err) || err.status !== 422) return null
  if (typeof err.detail !== 'object' || err.detail === null) return null
  const gate = err.detail as Record<string, unknown>
  if (gate.code !== 'publish_gate_failed') return null
  return {
    missing: Array.isArray(gate.missing) ? gate.missing.map(String) : [],
    unconfirmed: Array.isArray(gate.unconfirmed) ? gate.unconfirmed.map(String) : [],
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)
  const isForm = init.body instanceof FormData
  const headers: Record<string, string> = {
    ...(init.body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}),
    ...(init.headers as Record<string, string> | undefined),
  }
  try {
    const resp = await fetch(`${API_BASE}${path}`, {
      credentials: 'include',
      ...init,
      headers,
      signal: controller.signal,
    })
    if (!resp.ok) {
      let detail: unknown = null
      try {
        const body: unknown = await resp.json()
        detail =
          typeof body === 'object' && body !== null && 'detail' in body
            ? (body as { detail: unknown }).detail
            : body
      } catch {
        detail = null
      }
      if (resp.status === 401 && !path.startsWith('/auth/login')) emitAuthExpired()
      throw new ApiError(detailToMessage(detail, resp.status), resp.status, detail)
    }
    if (resp.status === 204) return undefined as T
    return (await resp.json()) as T
  } catch (err) {
    if (err instanceof ApiError) throw err
    // AbortError（超时）与 TypeError（网络不可达）统一归一
    throw new ApiError('网络请求失败或超时，请重试', 0, null)
  } finally {
    window.clearTimeout(timer)
  }
}

export { request }
