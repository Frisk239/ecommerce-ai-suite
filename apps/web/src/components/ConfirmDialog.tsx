import { useEffect, type ReactNode } from 'react'
import { X } from '@phosphor-icons/react'

// 确认框：发布等破坏性/生效动作前用；Esc / 背景点击取消，busy 时锁按钮。
export default function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = '确认',
  cancelLabel = '取消',
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean
  title: ReactNode
  body?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  busy?: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="modal-backdrop absolute inset-0" onClick={onCancel} aria-hidden />
      <div className="modal-card relative w-full max-w-md">
        <div className="flex items-start gap-3 border-b border-line-2 px-4 py-3">
          <div className="min-w-0 flex-1 text-[14px] font-semibold leading-6 text-ink">{title}</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} aria-label="关闭" disabled={busy}>
            <X aria-hidden size={14} />
          </button>
        </div>
        {body ? <div className="px-4 py-3.5 text-[13px] leading-6 text-ink-2">{body}</div> : null}
        <div className="flex justify-end gap-2 border-t border-line-1 px-4 py-3">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={onConfirm} disabled={busy}>
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
