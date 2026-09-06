import type { ReactNode } from 'react'

// 页头：标题 + 一句话说明 + 右侧动作区（Polaris 页模式）
export default function PageHeader({
  title,
  desc,
  actions,
  children,
}: {
  title: ReactNode
  desc?: ReactNode
  actions?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="mb-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold text-ink leading-7">{title}</h1>
          {desc && <p className="mt-1 text-sm text-ink-3 max-w-[70ch]">{desc}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </div>
  )
}
