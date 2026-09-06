import type { ReactNode } from 'react'

// 页头：标题 + 一句话说明 + 右侧动作区（Polaris 页模式，沿用原型交互）。
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
          <h1 className="text-lg font-semibold leading-7 text-ink">{title}</h1>
          {desc ? <p className="mt-1 max-w-[72ch] text-[13px] leading-5 text-ink-3">{desc}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {children ? <div className="mt-4">{children}</div> : null}
    </div>
  )
}
