import type { ReactNode } from 'react'

// 空态：说明「为什么空、怎么不空」，虚线引导框（UX-NOTES v3 细节纪律）。
export default function Empty({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon ? (
        <div className="mb-3.5 flex h-12 w-12 items-center justify-center rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface text-ink-3">
          {icon}
        </div>
      ) : null}
      <div className="text-[13px] font-medium text-ink-2">{title}</div>
      {hint ? <div className="mt-1.5 max-w-[52ch] text-xs leading-5 text-ink-3">{hint}</div> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}
