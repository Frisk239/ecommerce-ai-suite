// 工程口径（UX-NOTES §四）：未匹配路由给 404，不回总览。

import { Link } from 'react-router-dom'
import { Compass } from '@phosphor-icons/react'

export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface text-ink-3">
        <Compass aria-hidden size={24} />
      </div>
      <div className="mt-4 font-mono text-sm text-ink-3 tabular-nums">404</div>
      <div className="mt-1.5 text-[15px] font-semibold text-ink">页面不存在</div>
      <p className="mt-1.5 max-w-[48ch] text-[13px] leading-5 text-ink-3">
        这个地址没有对应的页面。用左侧导航回总览或资产列表，也可以直接从下面返回。
      </p>
      <Link to="/platform/assets" className="btn btn-secondary mt-5">
        返回资产列表
      </Link>
    </div>
  )
}
