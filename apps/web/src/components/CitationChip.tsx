// 引用芯片：A-{资产ID} · v{N}，等宽、品牌蓝语义底，点击进该资产详情（只读证据语义）。

import { ArrowSquareOut } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'

export default function CitationChip({ assetId, version }: { assetId: number; version: number }) {
  return (
    <Link
      to={`/platform/assets/${assetId}`}
      className="cite-chip"
      title={`查看证据：A-${assetId} · v${version}`}
    >
      A-{assetId} · v{version}
      <ArrowSquareOut aria-hidden size={11} weight="bold" />
    </Link>
  )
}
