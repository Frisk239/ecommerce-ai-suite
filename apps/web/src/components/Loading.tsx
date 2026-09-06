// 加载态：列表用骨架行，区块用浅字提示。

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2.5 p-4" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton h-6" style={{ maxWidth: `${96 - i * 5}%` }} />
      ))}
    </div>
  )
}

export function LoadingHint({ text = '加载中…' }: { text?: string }) {
  return <div className="py-12 text-center text-[13px] text-ink-3">{text}</div>
}
