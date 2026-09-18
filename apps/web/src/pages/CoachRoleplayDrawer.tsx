// 对练抽屉（第 121 刀 A）：AI 客户多轮对话 + 整段评分（四维+证据锚+整改建议）。
// 顾客人设随机（急躁/温和/挑剔/果断），态度随销售表现变化；单点回话失败
// 占位一句对练继续（不杀会话——「单点失败不杀整批」同族纪律）。

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { X } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { CoachQuestion, CoachRoleplay } from '../api/types'
import { useEscapeClose } from '../hooks/useEscapeClose'
import ActionError from '../components/ActionError'
import { DIMS } from './CoachPage'

export default function RoleplayDrawer({
  question,
  onClose,
}: {
  question: CoachQuestion
  onClose: () => void
}) {
  const [session, setSession] = useState<CoachRoleplay | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState<null | 'start' | 'turn' | 'finish'>(null)
  const [error, setError] = useState<string | null>(null)

  useEscapeClose(session === null || session.status === 'finished', onClose, busy === null)

  const start = async () => {
    if (busy) return
    setBusy('start')
    setError(null)
    try {
      setSession(
        await api.startCoachRoleplay(question.key as unknown as Record<string, unknown>),
      )
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(null)
    }
  }

  const send = async () => {
    if (busy || !session || session.status !== 'active') return
    const text = input.trim()
    if (text === '') return
    setBusy('turn')
    setError(null)
    setInput('')
    try {
      setSession(await api.coachRoleplayTurn(session.id, text))
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(null)
    }
  }

  const finish = async () => {
    if (busy || !session) return
    setBusy('finish')
    setError(null)
    try {
      setSession(await api.coachRoleplayFinish(session.id))
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(null)
    }
  }

  if (session === null) {
    return (
      <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="AI 客户对练">
        <div
          className="modal-backdrop absolute inset-0"
          onClick={busy === null ? onClose : undefined}
          aria-hidden
        />
        <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-lg flex-col">
          <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
            <div className="flex-1 text-[14px] font-semibold text-ink">AI 客户对练</div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="关闭对练抽屉">
              <X aria-hidden size={14} />
            </button>
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            <p className="text-xs leading-5 text-ink-3">
              AI 将扮演一位顾客（随机人设：急躁/温和/挑剔/果断），以「
              {question.question.slice(0, 40)}
              {question.question.length > 40 ? '…' : ''}」为开场诉求与你多轮对话。
              结束后整段按四维评分（口径/异议/证据/语气）+ 证据锚（正确口径的出处）+ 整改建议。
            </p>
            <button
              type="button"
              className="btn btn-primary w-full"
              disabled={busy !== null}
              onClick={() => void start()}
            >
              {busy === 'start' ? '顾客入场中（约 10-20 秒）…' : '开始对练'}
            </button>
            {error ? <ActionError message={error} /> : null}
          </div>
        </aside>
      </div>
    )
  }

  const activeSession = session.status === 'active'
  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="AI 客户对练">
      <div className="modal-backdrop absolute inset-0" aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-lg flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <span className="kind-chip">{session.persona?.style ?? '顾客'}型顾客</span>
          <div className="min-w-0 flex-1 truncate text-[13px] text-ink-3" title={session.persona?.trait}>
            {session.persona?.trait ?? ''}
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="关闭对练抽屉">
            <X aria-hidden size={14} />
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {session.turns.map((turn, i) => (
            <div
              key={i}
              className={
                turn.role === 'trainee'
                  ? 'ml-12 rounded-[10px] bg-accent-soft px-3.5 py-2.5 text-[13px] leading-5 text-ink'
                  : turn.role === 'customer'
                    ? 'mr-12 rounded-[10px] border border-line-2 bg-canvas px-3.5 py-2.5 text-[13px] leading-5 text-ink-2'
                    : 'rounded-[6px] border border-[rgba(154,91,6,0.25)] bg-[rgba(154,91,6,0.05)] px-3 py-2 text-xs leading-5 text-warn'
              }
            >
              {turn.text}
            </div>
          ))}
          {busy === 'turn' ? (
            <div className="mr-12 rounded-[10px] border border-line-2 bg-canvas px-3.5 py-2.5 text-[13px] text-ink-3">
              顾客正在输入…
            </div>
          ) : null}

          {!activeSession && session.score ? (
            <div className="mt-2 space-y-3 border-t border-line-1 pt-3">
              <div className="grid grid-cols-4 gap-2">
                {DIMS.map((dim) => (
                  <div key={dim.key} className="rounded-[8px] border border-line-2 bg-canvas px-2.5 py-2">
                    <div className="text-[11px] text-ink-3">{dim.label}</div>
                    <div className="mt-0.5 font-mono text-[16px] font-semibold tabular-nums text-ink">
                      {session.score?.[dim.key] ?? '—'}
                      <span className="text-[10px] font-normal text-ink-3">/{dim.max}</span>
                    </div>
                  </div>
                ))}
              </div>
              <p className="rounded-[6px] border border-line-2 bg-canvas px-3 py-2 text-[13px] leading-5 text-ink-2">
                {session.score.comment}
                {session.score.remediation ? (
                  <span className="mt-1 block text-xs text-warn">建议：{session.score.remediation}</span>
                ) : null}
              </p>
              {session.score.anchors && session.score.anchors.length > 0 ? (
                <div>
                  <span className="field-label">证据锚（正确口径出处）</span>
                  {session.score.anchors.map((a) => (
                    <Link
                      key={`${a.asset_id}-${a.version_no}`}
                      to={`/platform/assets/${a.asset_id}?v=${a.version_no}`}
                      className="flex items-center gap-2 rounded-[6px] border border-line-2 bg-canvas px-3 py-1.5 text-xs transition-colors duration-150 hover:border-accent-border"
                    >
                      <span className="shrink-0 font-mono text-accent-strong">
                        A-{String(a.asset_id).padStart(4, '0')} · v{a.version_no}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-ink-3">{a.chunk}</span>
                    </Link>
                  ))}
                </div>
              ) : null}
              {session.model_name ? (
                <span className="badge badge-published">评分底座：{session.model_name}</span>
              ) : null}
            </div>
          ) : null}
          {!activeSession && !session.score ? (
            <div className="rounded-[6px] border border-[rgba(154,91,6,0.25)] bg-[rgba(154,91,6,0.05)] px-3 py-2 text-xs leading-5 text-warn">
              评分未完成（LLM 未配置或输出不可解析）——对话记录已保存，可稍后重开一场。
            </div>
          ) : null}
        </div>

        {activeSession ? (
          <div className="space-y-2 border-t border-line-2 px-4 py-3">
            <div className="flex gap-2">
              <input
                className="input flex-1 text-[13px]"
                placeholder="以销售口吻回复这位顾客…"
                value={input}
                disabled={busy !== null}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && input.trim() !== '') void send()
                }}
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={busy !== null || input.trim() === ''}
                onClick={() => void send()}
              >
                {busy === 'turn' ? '…' : '发送'}
              </button>
            </div>
            <div className="flex justify-end">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={busy !== null}
                onClick={() => void finish()}
              >
                {busy === 'finish' ? '评分中（约 15 秒）…' : '结束并评分'}
              </button>
            </div>
          </div>
        ) : (
          <div className="border-t border-line-2 px-4 py-3 text-right">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
              关闭
            </button>
          </div>
        )}
        {error ? (
          <div className="px-4 pb-3">
            <ActionError message={error} />
          </div>
        ) : null}
      </aside>
    </div>
  )
}
