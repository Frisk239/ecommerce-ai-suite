import type { Asset } from '../store/types'

// 状态徽章语义（UX-NOTES 锚点）：已发布=安静的绿（默认态不抢戏），
// 待人洗=琥珀（等人做事），已接入=中性灰（尚未进入治理），失败=红。
export function StateBadge({ state }: { state: Asset['state'] }) {
  if (state === '已发布') return <span className="badge-published">已发布</span>
  if (state === '待人洗') return <span className="badge-review">待人洗</span>
  return <span className="badge-ingested">已接入</span>
}

// 已发布资产正在修订：状态显示待人洗，但保留「已发布版仍在线」的线索
export function StateBadgeWithRevision({ asset }: { asset: Asset }) {
  if (asset.state === '待人洗' && asset.publishedV != null) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className="badge-review">待人洗 · 修订中</span>
        <span className="text-xs text-caption font-mono tabular-nums">线上 v{asset.publishedV}</span>
      </span>
    )
  }
  return <StateBadge state={asset.state} />
}

export function KindBadge({ kind }: { kind: Asset['kind'] }) {
  return <span className="badge-neutral">{kind}</span>
}
