import { useCallback, useEffect, useState } from 'react'
import { ArrowClockwise, Pulse } from '@phosphor-icons/react'

// dev：vite proxy 把 /api 转发到 API（本地 localhost:8000 / compose 内 api:8000）
// 生产构建：可用 VITE_API_URL 覆盖为 API 直连地址
const API_BASE: string = import.meta.env.VITE_API_URL ?? '/api'
const HEALTH_URL = `${API_BASE}/health`

interface HealthBody {
  status: string
  database: string
}

type Probe =
  | { phase: 'loading' }
  | { phase: 'responded'; httpStatus: number; body: HealthBody }
  | { phase: 'unreachable' }

async function probeHealth(): Promise<Probe> {
  try {
    const resp = await fetch(HEALTH_URL)
    const body = (await resp.json()) as HealthBody
    return { phase: 'responded', httpStatus: resp.status, body }
  } catch {
    // 网络层失败（连不上、非 JSON 响应体）：视为 API 不可达
    return { phase: 'unreachable' }
  }
}

type Tone = 'ok' | 'bad' | 'idle'

function StatusDot({ tone }: { tone: Tone }) {
  const color =
    tone === 'ok' ? 'bg-emerald-500' : tone === 'bad' ? 'bg-red-500' : 'bg-slate-300 animate-pulse'
  return <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${color}`} />
}

function StatusRow({ label, tone, text, hint }: { label: string; tone: Tone; text: string; hint?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3.5 first:pt-0 last:pb-0">
      <span className="text-[13px] text-ink-2">{label}</span>
      <span className="flex min-w-0 items-center gap-2">
        <StatusDot tone={tone} />
        <span className="text-[13px] text-ink">{text}</span>
        {hint ? <span className="truncate font-mono text-xs text-ink-3 tabular-nums">{hint}</span> : null}
      </span>
    </div>
  )
}

export default function App() {
  const [probe, setProbe] = useState<Probe>({ phase: 'loading' })
  const [requestedAt, setRequestedAt] = useState('')
  const [runId, setRunId] = useState(0)

  useEffect(() => {
    let cancelled = false
    const startedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    probeHealth().then((result) => {
      if (cancelled) return
      setRequestedAt(startedAt)
      setProbe(result)
    })
    return () => {
      cancelled = true
    }
  }, [runId])

  const refresh = useCallback(() => {
    setProbe({ phase: 'loading' })
    setRunId((n) => n + 1)
  }, [])

  // API 可达性：有 HTTP 响应即「可达」（503 也是可达，附原始 status 文案）
  const apiRow: { tone: Tone; text: string; hint?: string } =
    probe.phase === 'loading'
      ? { tone: 'idle', text: '检查中…' }
      : probe.phase === 'unreachable'
        ? { tone: 'bad', text: 'API 不可达' }
        : {
            tone: 'ok',
            text: '可达',
            hint: `HTTP ${probe.httpStatus} · ${probe.body.status}`,
          }

  // 数据库连通性：只信 API 报告的 database 原文；API 不可达时未知
  const dbRow: { tone: Tone; text: string; hint?: string } =
    probe.phase === 'loading'
      ? { tone: 'idle', text: '检查中…' }
      : probe.phase === 'unreachable'
        ? { tone: 'idle', text: '未知' }
        : probe.body.database === 'connected'
          ? { tone: 'ok', text: '已连通', hint: probe.body.database }
          : { tone: 'bad', text: '异常', hint: probe.body.database }

  return (
    <div className="min-h-screen bg-white font-sans text-[14px] text-ink">
      <header className="border-b border-line">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-2.5 px-6">
          <Pulse aria-hidden size={20} weight="bold" className="text-accent" />
          <div className="flex items-baseline gap-2.5">
            <h1 className="text-[15px] font-semibold tracking-tight text-ink">电商 AI 套件</h1>
            <p className="text-xs text-ink-3">工程脚手架 · 健康检查</p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <section
          aria-labelledby="health-title"
          className="rounded-lg border border-line p-6 shadow-[0_2px_8px_rgba(15,23,42,0.04)]"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 id="health-title" className="text-base font-semibold text-ink">
                服务健康
              </h2>
              <p className="mt-1 text-xs text-ink-3">
                真实调用 <code className="font-mono">GET {HEALTH_URL}</code>
              </p>
            </div>
            <button
              type="button"
              onClick={refresh}
              className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-accent transition-colors hover:bg-[rgba(65,118,230,0.06)]"
            >
              <ArrowClockwise aria-hidden size={13} />
              刷新
            </button>
          </div>

          <div className="mt-5 divide-y divide-[rgba(15,23,42,0.06)]">
            <StatusRow label="API 服务" {...apiRow} />
            <StatusRow label="数据库" {...dbRow} />
          </div>

          <p className="mt-5 border-t border-line pt-4 font-mono text-xs text-ink-3 tabular-nums">
            {HEALTH_URL}
            {probe.phase === 'responded' ? ` · HTTP ${probe.httpStatus}` : ''}
            {requestedAt ? ` · ${requestedAt}` : ''}
          </p>
        </section>
      </main>
    </div>
  )
}
