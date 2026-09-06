// 界面用词与格式化（CONTEXT.md 词表：已接入/待人洗/已发布；不用「用户」「草稿」「未审核」）。

import type { AssetStatus } from './api/types'

export const ASSET_STATUS_LABEL: Record<AssetStatus, string> = {
  ingested: '已接入',
  pending_review: '待人洗',
  published: '已发布',
}

const KIND_LABELS: Record<string, string> = {
  document: '文档',
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
