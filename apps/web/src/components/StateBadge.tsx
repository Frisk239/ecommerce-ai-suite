// 状态徽章语义（UX-NOTES 锚点）：已发布=安静的绿，待人洗=琥珀，已接入=中性灰，
// 机洗失败=红（已接入 + last_error 时用失败徽章显性表达，不静默）。

import type { AssetStatus } from '../api/types'
import { ASSET_STATUS_LABEL, kindLabel } from '../labels'

export function StatusBadge({ status, failed = false }: { status: AssetStatus; failed?: boolean }) {
  if (status === 'published') return <span className="badge badge-published">已发布</span>
  if (status === 'pending_review') return <span className="badge badge-review">待人洗</span>
  if (failed) return <span className="badge badge-failed">已接入 · 机洗失败</span>
  return <span className="badge badge-ingested">{ASSET_STATUS_LABEL.ingested}</span>
}

export function KindChip({ kind }: { kind: string }) {
  return <span className="kind-chip">{kindLabel(kind)}</span>
}
