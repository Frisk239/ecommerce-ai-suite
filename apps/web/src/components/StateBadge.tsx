// 状态徽章语义（UX-NOTES 锚点）：已发布=安静的绿，待人洗=琥珀，已接入=中性灰，
// 机洗失败=红（已接入 + last_error 时用失败徽章显性表达，不静默）。
// 知识缺口（0024）：open=琥珀「待补」，resolved=安静的绿「已解决」——缺口不是
// 资产，不复用资产三态徽章。

import type { AssetStatus, KnowledgeGapStatus } from '../api/types'
import { ASSET_STATUS_LABEL, kindLabel } from '../labels'

export function StatusBadge({
  status,
  failed = false,
  revising = false,
  publishedVersionNo = null,
}: {
  status: AssetStatus
  failed?: boolean
  revising?: boolean
  publishedVersionNo?: number | null
}) {
  if (revising) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className="badge badge-review">待人洗 · 修订中</span>
        {publishedVersionNo !== null ? (
          <span className="font-mono text-xs tabular-nums text-caption">线上 v{publishedVersionNo}</span>
        ) : null}
      </span>
    )
  }
  if (status === 'published') return <span className="badge badge-published">已发布</span>
  if (status === 'pending_review') return <span className="badge badge-review">待人洗</span>
  if (failed) return <span className="badge badge-failed">已接入 · 机洗失败</span>
  return <span className="badge badge-ingested">{ASSET_STATUS_LABEL.ingested}</span>
}

export function GapStatusBadge({ status }: { status: KnowledgeGapStatus }) {
  if (status === 'resolved') return <span className="badge badge-published">已解决</span>
  return <span className="badge badge-review">待补</span>
}

export function KindChip({ kind }: { kind: string }) {
  return <span className="kind-chip">{kindLabel(kind)}</span>
}
