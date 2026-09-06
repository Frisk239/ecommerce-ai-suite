import { Link } from 'react-router-dom'
import { ArrowSquareOut } from '@phosphor-icons/react'

// 引用芯片：资产ID · vN。点击只读该版本的资产详情（ADR 0007：引用必须带版本号）
export default function CitationChip({
  assetId,
  v,
}: {
  assetId: string
  v: number
}) {
  return (
    <Link
      to={`/platform/assets/${assetId}?v=${v}`}
      className="cite-chip"
      title={`查看引用证据：${assetId} · v${v}`}
    >
      {assetId} · v{v}
      <ArrowSquareOut size={11} weight="bold" />
    </Link>
  )
}
