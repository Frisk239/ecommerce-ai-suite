// 界面用词与格式化（CONTEXT.md 词表：已接入/待人洗/已发布；不用「用户」「草稿」「未审核」）。

import type { AssetStatus, ServiceSessionStatus } from './api/types'

export const ASSET_STATUS_LABEL: Record<AssetStatus, string> = {
  ingested: '已接入',
  pending_review: '待人洗',
  published: '已发布',
}

/** 会话状态（CONTEXT「会话」词条：结束后由操作者回流登记为资产）。 */
export const SERVICE_SESSION_STATUS_LABEL: Record<ServiceSessionStatus, string> = {
  active: '进行中',
  registered: '已回流',
}

const KIND_LABELS: Record<string, string> = {
  document: '文档',
  dialogue: '对话',
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** 消息气泡时间戳：只取时分（NN/g 惯例，每条可见）。 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(11, 16)
  return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

/** 历史会话条目的日期（同年省年）。 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10)
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
