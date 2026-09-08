// 题源锚链（第 25 刀收口）：`A-xxxx · vN` 等宽文本链接 → 治理台资产详情。
// 不与 CitationChip 复用——两者形状语义不同：CitationChip 是青底 cite-chip、
// 链向 ?v=N 版本锚定视图（0007 引用回放）；本组件是考核题源指向（0040），
// 纯文本下划线链、只指资产详情不带版本参数。嵌在 row-click 行内时传
// stopPropagation 阻止芯片点击触发行展开。

import { Link } from 'react-router-dom'
import { formatAssetId } from '../labels'

export default function AssetAnchorChip({
  assetId,
  version,
  title,
  stopPropagation = false,
}: {
  assetId: number
  version: number
  title?: string
  stopPropagation?: boolean
}) {
  return (
    <Link
      to={`/platform/assets/${assetId}`}
      className="font-mono text-xs text-ink-2 underline-offset-2 hover:text-accent-strong hover:underline"
      title={title}
      onClick={stopPropagation ? (e) => e.stopPropagation() : undefined}
    >
      {formatAssetId(assetId)} · v{version}
    </Link>
  )
}
