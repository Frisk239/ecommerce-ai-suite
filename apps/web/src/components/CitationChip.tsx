// 引用芯片：A-{资产ID} · v{N}（4 位补零），等宽、品牌蓝语义底，点击进该资产详情
// 并锚定到引用所指的那一版（?v=N，ADR 0007 引用回放：同资产发新版后旧引用不漂移）。

import { ArrowSquareOut } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { formatAssetId } from '../labels'

export default function CitationChip({ assetId, version }: { assetId: number; version: number }) {
  const label = `${formatAssetId(assetId)} · v${version}`
  return (
    <Link
      to={`/platform/assets/${assetId}?v=${version}`}
      className="cite-chip"
      title={`查看证据：${label}（锚定该版本）`}
    >
      {label}
      <ArrowSquareOut aria-hidden size={11} weight="bold" />
    </Link>
  )
}
