import type { ReactNode } from 'react'

// 空态：说明「为什么是空的」与「怎么让它不空」，不是装饰
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
    <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
      {icon && (
        <div className="mb-3.5 w-12 h-12 rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-fill-60 flex items-center justify-center text-caption">
          {icon}
        </div>
      )}
      <div className="text-sm font-medium text-ink-2">{title}</div>
      {hint && (
        <div className="mt-1.5 text-xs text-caption max-w-[48ch] leading-5">{hint}</div>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
