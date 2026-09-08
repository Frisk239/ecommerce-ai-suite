// 界面用词与格式化（CONTEXT.md 词表：已接入/待人洗/已发布；不用「用户」「草稿」「未审核」）。

import type { AssetStatus, MaterialTaskStatus, ServiceSessionStatus } from './api/types'

/** 素材任务五态（第 17 刀/ADR 0038）：与资产三态分词表——任务不是中台对象。 */
export const MATERIAL_TASK_STATUS_LABEL: Record<MaterialTaskStatus, string> = {
  queued: '排队',
  running: '进行中',
  pending_qc: '待抽检',
  registered: '已登记',
  failed: '失败',
}

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
  material: '素材',
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

/** 来源（CONTEXT「来源」词条全枚举）：当前只会出现 upload / session_backflow，
 * 其余为后续刀预留映射；未知值原样显示，不发明词。 */
const SOURCE_KIND_LABELS: Record<string, string> = {
  upload: '上传',
  session_backflow: '会话回流',
  clip_pick: '切片拣选',
  material_generated: '素材生成',
  mcp_registered: '连接层登记',
  seed: '种子',
}

export function sourceKindLabel(kind: string): string {
  return SOURCE_KIND_LABELS[kind] ?? kind
}

/** 界面统一 ID 展示：4 位补零（A-0001 / G-0001）；后端契约仍是裸 int。 */
export function formatAssetId(id: number): string {
  return `A-${String(id).padStart(4, '0')}`
}

export function formatGapId(id: number): string {
  return `G-${String(id).padStart(4, '0')}`
}

/** 素材任务 ID 展示（同 A-/G- 口径补零；后端契约仍是裸 int）。 */
export function formatTaskId(id: number): string {
  return `M-${String(id).padStart(4, '0')}`
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
