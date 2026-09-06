// 引用芯片：A-{资产ID} · v{N}，等宽、品牌蓝语义底，点击进该资产详情并锚定到
// 引用所指的那一版（?v=N，ADR 0007 引用回放：同资产发新版后旧引用不漂移）。

import { ArrowSquareOut } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'

export default function CitationChip({ assetId, version }: { assetId: number; version: number }) {
  return (
    <Link
      to={`/platform/assets/${assetId}?v=${version}`}
      className="cite-chip"
      title={`查看证据：A-${assetId} · v${version}（锚定该版本）`}
    >
      A-{assetId} · v{version}
      <ArrowSquareOut aria-hidden size={11} weight="bold" />
    </Link>
  )
}
