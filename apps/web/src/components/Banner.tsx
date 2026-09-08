// 显性横幅：API 错误（红，带重试）与发布成功（安静绿）。不静默白屏。

import type { ReactNode } from 'react'
import { ArrowClockwise, CheckCircle, Warning } from '@phosphor-icons/react'
import { detailText } from '../api/client'

export function ErrorBanner({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  return (
    <div
      role="alert"
      className="mb-4 flex items-start gap-2.5 rounded-[8px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-3.5 py-2.5 shadow-sm"
    >
      <Warning aria-hidden size={15} className="mt-px shrink-0 text-danger" />
      <div className="min-w-0 flex-1 text-[13px] leading-5 text-danger">{detailText(error)}</div>
      {onRetry ? (
        <button type="button" className="btn btn-ghost btn-sm shrink-0" onClick={onRetry}>
          <ArrowClockwise aria-hidden size={12} />
          重试
        </button>
      ) : null}
    </div>
  )
}

export function SuccessBanner({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      className="mb-4 flex items-center gap-2.5 rounded-[8px] border border-[rgba(30,107,69,0.22)] bg-[rgba(30,107,69,0.05)] px-3.5 py-2.5 text-[13px] leading-5 text-ok shadow-sm"
    >
      <CheckCircle aria-hidden size={15} weight="fill" className="shrink-0" />
      {children}
    </div>
  )
}
