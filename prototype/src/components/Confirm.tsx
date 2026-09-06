import type { ReactNode } from 'react'
import { Warning } from '@phosphor-icons/react'

// 二次确认对话框（投放发布、发布资产等不可轻点的动作）
export default function Confirm({
  open,
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
  danger,
}: {
  open: boolean
  title: string
  body: ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  danger?: boolean
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="absolute inset-0 bg-stone-900/30" onClick={onCancel} />
      <div className="relative panel w-full max-w-md p-5 shadow-lg">
        <div className="flex items-start gap-3">
          <div
            className={`mt-0.5 w-8 h-8 rounded-[6px] flex items-center justify-center shrink-0 ${
              danger ? 'bg-red-50 text-red-600' : 'bg-accent-soft text-accent-strong'
            }`}
          >
            <Warning size={16} />
          </div>
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-ink">{title}</h2>
            <div className="mt-2 text-sm text-ink-2 leading-6">{body}</div>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button className="btn-ghost" onClick={onCancel}>
            取消
          </button>
          <button
            className={danger ? 'btn-danger' : 'btn-primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
