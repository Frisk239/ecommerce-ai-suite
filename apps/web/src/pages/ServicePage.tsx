// 客服预览页 /service：操作者扮演顾客发问，观察引用 / 拒答 / 回流登记。
// 交互状态机照 UX-NOTES §二点八冻结口径搬自原型：thinking（三点 typing + 检索
// 状态行）→ delta 逐片追加（尾部 CSS 光标）→ complete（引用芯片落位）；停止 =
// 中断 SSE 订阅，已收文本保留（后端断连仍完整落库，stopped 只是前端表达）。
// 不搬的：35ms 定时器模拟、mock 大脑、localStorage 补全——传输由真实 SSE 驱动。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUDownLeft,
  ArrowUp,
  ChatCircleDots,
  HandArrowUp,
  Prohibit,
  Stop,
  UserCircle,
} from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import type { ServiceCitation, ServiceSessionSummary } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import {
  SERVICE_SESSION_STATUS_LABEL,
  ASSET_STATUS_LABEL,
  formatAssetId,
  formatDate,
  formatDateTime,
  formatGapId,
  formatTime,
} from '../labels'
import { ErrorBanner, SuccessBanner } from '../components/Banner'
import CitationChip from '../components/CitationChip'
import ConfirmDialog from '../components/ConfirmDialog'
import Empty from '../components/Empty'
import { LoadingHint } from '../components/Loading'
import PageHeader from '../components/PageHeader'

const SUGGESTIONS = [
  '保温杯的净含量是多少？',
  '饮用水的保质期是多久？',
  '怎么退货？',
  '保温杯的材质是什么？',
]

/** 展示用消息 = 服务器消息 + 本地流式追加（streaming/stopped 是前端表达，不进 API 类型）。 */
interface UiMessage {
  key: string
  id: number | null
  role: 'customer' | 'agent'
  content: string
  citations: ServiceCitation[] | null
  kind: 'answer' | 'refusal' | null
  handoff: boolean
  created_at: string
  /** thinking 或 delta 进行中（锁输入、显示光标/typing）。 */
  streaming: boolean
  /** 操作者中断了订阅：已收文本保留，完整回答在后端留档。 */
  stopped: boolean
  /** thinking 事件带来的检索状态行文案。 */
  thinkingText: string | null
  /** 拒答时 complete 事件带回的知识缺口 id（ADR 0030：只在运行时返回，
   * 服务器消息列表不含此列——重载后芯片不重现，属契约口径）。 */
  gapId: number | null
}

function toUi(m: {
  id: number
  role: 'customer' | 'agent'
  content: string
  citations: ServiceCitation[] | null
  kind: 'answer' | 'refusal' | null
  handoff: boolean | null
  created_at: string
}): UiMessage {
  return {
    key: `s-${m.id}`,
    id: m.id,
    role: m.role,
    content: m.content,
    citations: m.citations,
    kind: m.kind,
    handoff: m.handoff ?? false,
    created_at: m.created_at,
    streaming: false,
    stopped: false,
    thinkingText: null,
    gapId: null,
  }
}

// 连续同角色消息分组：隐藏重复头像，只留首条时间戳（Intercom 式分组）
function MessageBubble({ m, prev }: { m: UiMessage; prev?: UiMessage }) {
  const grouped = !!prev && prev.role === m.role && !m.streaming
  if (m.role === 'customer') {
    return (
      <div className={`flex items-end justify-end gap-2 ${grouped ? 'mt-1' : ''}`}>
        {!grouped && (
          <span className="mb-1 text-[11px] tabular-nums text-caption">{formatTime(m.created_at)}</span>
        )}
        <div className="bubble-customer">{m.content}</div>
        {grouped ? (
          <div className="w-7 shrink-0" />
        ) : (
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line-2 bg-surface">
            <UserCircle aria-hidden size={15} className="text-caption" />
          </div>
        )}
      </div>
    )
  }
  return (
    <div className={`flex items-end justify-start gap-2 ${grouped ? 'mt-1' : ''}`}>
      {grouped ? (
        <div className="w-7 shrink-0" />
      ) : (
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[rgba(65,118,230,0.35)] bg-[rgba(65,118,230,0.08)]">
          <ChatCircleDots aria-hidden size={15} className="text-accent-strong" />
        </div>
      )}
      <div className="min-w-0 space-y-1.5">
        {!grouped && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-accent-strong">AI 客服</span>
            {m.kind === 'refusal' && <span className="badge badge-failed">拒答 · 无已发布证据</span>}
            {m.handoff && (
              <span className="badge badge-review">
                <HandArrowUp aria-hidden size={11} />
                已转人工
              </span>
            )}
            {m.kind === 'refusal' && m.gapId !== null && (
              <Link
                to="/platform/assets?status=知识缺口"
                className="badge badge-review font-mono transition-colors duration-150 hover:brightness-110"
                title="已记入治理台知识缺口，不是资产"
              >
                知识缺口 {formatGapId(m.gapId)}
              </Link>
            )}
          </div>
        )}
        {m.streaming && m.content === '' ? (
          // thinking 态：typing 三点 + 检索状态行（首字前的 TTFT 间隙）
          <div className="bubble-agent" role="status" aria-label="AI 客服正在检索已发布资产">
            <div className="typing-dots">
              <span />
              <span />
              <span />
            </div>
          </div>
        ) : (
          <div className="bubble-agent">
            {m.content}
            {m.streaming && <span className="stream-caret" />}
            {m.stopped && (
              <span
                className="ml-2 text-[11px] text-caption"
                title="停止的是页面上的流式展示；回答已完整留档，切回本会话即可看到完整内容"
              >
                已停止展示 · 完整回答已留档
              </span>
            )}
          </div>
        )}
        {m.streaming && m.content === '' && (
          <div className="text-[11px] text-caption">{m.thinkingText ?? '正在检索已发布资产…'}</div>
        )}
        {m.citations !== null && m.citations.length > 0 && !m.streaming && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-caption">引用：</span>
            {m.citations.map((c) => (
              <CitationChip key={`${c.asset_id}-${c.version_no}`} assetId={c.asset_id} version={c.version_no} />
            ))}
          </div>
        )}
      </div>
      {!grouped && (
        <span className="mb-1 text-[11px] tabular-nums text-caption">{formatTime(m.created_at)}</span>
      )}
    </div>
  )
}

function SessionStatusBadge({ status }: { status: ServiceSessionSummary['status'] }) {
  if (status === 'active') {
    return (
      <span className="inline-flex h-[18px] items-center rounded-[4px] bg-[rgba(65,118,230,0.08)] px-1.5 text-[11px] font-medium leading-none text-accent-strong">
        {SERVICE_SESSION_STATUS_LABEL.active}
      </span>
    )
  }
  return <span className="badge badge-review">{SERVICE_SESSION_STATUS_LABEL.registered}</span>
}

interface RegisteredBanner {
  assetId: number
  assetStatus: 'ingested' | 'pending_review' | 'published'
}

export default function ServicePage() {
  // 左栏：历史会话列表（倒序）
  const listFetcher = useCallback(() => api.listServiceSessions(), [])
  const list = useApiData(listFetcher)
  const sessions = list.state.phase === 'ok' ? list.state.data : []

  // 选择模型：null = 尚未选择（跟随最近一条会话）；点了哪条就固定在哪条
  const [chosenId, setChosenId] = useState<number | null>(null)
  const selectedId =
    chosenId ?? (list.state.phase === 'ok' && list.state.data.length > 0 ? list.state.data[0].id : null)
  const detailFetcher = useCallback(
    () => (selectedId === null ? Promise.resolve(null) : api.getServiceSession(selectedId)),
    [selectedId],
  )
  const detail = useApiData(detailFetcher)
  const session = detail.state.phase === 'ok' ? detail.state.data : null

  // 本地流式追加的消息（detail 重新加载时清空，以服务器为准）
  const [live, setLive] = useState<UiMessage[]>([])

  const [input, setInput] = useState('')
  const [streamError, setStreamError] = useState<unknown>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [registered, setRegistered] = useState<RegisteredBanner | null>(null)
  const [creating, setCreating] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)

  const messages = useMemo<UiMessage[]>(
    () => [...(session?.messages.map(toUi) ?? []), ...live],
    [session, live],
  )
  const streaming = live.some((m) => m.streaming)

  // 自动跟随：消息条数与已输出字符数变化都滚动到尾部
  const totalChars = messages.reduce((acc, m) => acc + m.content.length, 0)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [messages.length, totalChars])

  // 切换会话：中断在途流（旧会话的已收文本不带入新会话），回到服务器口径
  const selectSession = useCallback((id: number) => {
    abortRef.current?.abort()
    abortRef.current = null
    setLive([])
    setStreamError(null)
    setChosenId(id)
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  // Esc 停止（textarea 流式期间被禁用，事件不再冒泡，挂 window 级）
  useEffect(() => {
    if (!streaming) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') stop()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [streaming, stop])

  const newSession = async () => {
    if (creating || streaming) return
    setCreating(true)
    setActionError(null)
    try {
      const created = await api.createServiceSession()
      setRegistered(null)
      abortRef.current?.abort()
      abortRef.current = null
      setLive([])
      setStreamError(null)
      setChosenId(created.id)
      list.reload()
      taRef.current?.focus()
    } catch (err) {
      setActionError(err)
    } finally {
      setCreating(false)
    }
  }

  // 发送：立即回显顾客消息 + agent 占位，SSE 事件驱动 thinking → delta → complete
  const send = async (text: string) => {
    const question = text.trim()
    if (question === '' || streaming || selectedId === null) return
    if (session === null || session.status !== 'active') return
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    const now = new Date().toISOString()
    const seq = seqRef.current
    seqRef.current += 1
    const agentKey = `local-a-${seq}`
    setStreamError(null)
    setLive((prev) => [
      ...prev,
      {
        key: `local-c-${seq}`,
        id: null,
        role: 'customer',
        content: question,
        citations: null,
        kind: null,
        handoff: false,
        created_at: now,
        streaming: false,
        stopped: false,
        thinkingText: null,
        gapId: null,
      },
      {
        key: agentKey,
        id: null,
        role: 'agent',
        content: '',
        citations: null,
        kind: null,
        handoff: false,
        created_at: now,
        streaming: true,
        stopped: false,
        thinkingText: null,
        gapId: null,
      },
    ])
    const controller = new AbortController()
    abortRef.current = controller
    let completed = false
    try {
      await api.askService(
        selectedId,
        question,
        {
          onThinking: (t) =>
            setLive((prev) =>
              prev.map((m) => (m.key === agentKey ? { ...m, thinkingText: t } : m)),
            ),
          onDelta: (piece) =>
            setLive((prev) =>
              prev.map((m) => (m.key === agentKey ? { ...m, content: m.content + piece } : m)),
            ),
          onComplete: (payload) => {
            completed = true
            setLive((prev) =>
              prev.map((m) =>
                m.key === agentKey
                  ? {
                      ...m,
                      id: payload.message_id,
                      citations: payload.citations,
                      kind: payload.kind,
                      handoff: payload.handoff,
                      streaming: false,
                      stopped: false,
                      gapId: payload.gap_id ?? null,
                    }
                  : m,
              ),
            )
          },
        },
        controller.signal,
      )
    } catch (err) {
      setStreamError(err)
    } finally {
      abortRef.current = null
      // 中止/断流：已收文本保留，标「已停止展示 · 完整回答已留档」
      setLive((prev) =>
        prev.map((m) => (m.key === agentKey ? { ...m, streaming: false, stopped: !completed } : m)),
      )
      list.reload()
    }
  }

  const register = async () => {
    if (selectedId === null || registering) return
    setRegistering(true)
    setActionError(null)
    try {
      const asset = await api.registerServiceSession(selectedId)
      setRegistered({ assetId: asset.id, assetStatus: asset.status })
      setConfirmOpen(false)
      setLive([])
      list.reload()
      detail.reload()
    } catch (err) {
      setActionError(err)
      setConfirmOpen(false)
    } finally {
      setRegistering(false)
    }
  }

  // 发送/回流失败后的「重试」= 同步服务器口径：后端断连仍完整落库，刷新即见真相
  const syncFromServer = useCallback(() => {
    setStreamError(null)
    setActionError(null)
    setLive([])
    list.reload()
    detail.reload()
  }, [list, detail])

  const isActiveSession = session !== null && session.status === 'active'
  const canAsk = isActiveSession && !streaming
  const canRegister =
    isActiveSession && !streaming && messages.length > 0 && detail.state.phase === 'ok'

  return (
    <div>
      {registered !== null && (
        <SuccessBanner>
          <span className="inline-flex flex-wrap items-center gap-1">
            <ArrowUDownLeft aria-hidden size={14} className="shrink-0" />
            已回流登记为
            <Link to={`/platform/assets/${registered.assetId}`} className="font-mono underline">
              {formatAssetId(registered.assetId)}
            </Link>
            （{ASSET_STATUS_LABEL[registered.assetStatus]}）· 治理台可见；客服检索暂不可见，发布后可被引用
          </span>
        </SuccessBanner>
      )}
      {streamError !== null && <ErrorBanner error={streamError} onRetry={syncFromServer} />}
      {actionError !== null && <ErrorBanner error={actionError} onRetry={syncFromServer} />}

      <PageHeader
        title="客服"
        desc="操作者预览顾客对话：回答只引用已发布资产（带版本号），无已发布证据则拒答并转人工；会话结束后回流登记为对话资产。"
        actions={
          <button type="button" className="btn btn-primary" onClick={() => void newSession()} disabled={creating || streaming}>
            <ChatCircleDots aria-hidden size={14} />
            {creating ? '创建中…' : '新会话'}
          </button>
        }
      />

      <div className="grid items-start gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        {/* 左栏：历史会话 */}
        <aside className="panel overflow-hidden">
          <div className="panel-title text-xs">
            <span className="flex-1">历史会话</span>
            <span className="font-mono text-[11px] font-normal text-caption">{sessions.length}</span>
          </div>
          {list.state.phase === 'loading' ? (
            <div className="py-8 text-center text-xs text-ink-3">加载中…</div>
          ) : list.state.phase === 'error' ? (
            <div className="px-3 py-6 text-center text-xs text-ink-3">
              会话列表加载失败，上方横幅可重试
            </div>
          ) : sessions.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs leading-5 text-ink-3">
              还没有历史会话。
              <br />
              新开一条会话，扮演顾客提问。
            </div>
          ) : (
            <ul className="max-h-[calc(100dvh-320px)] overflow-y-auto">
              {sessions.map((s) => {
                const selected = s.id === selectedId
                return (
                  <li key={s.id} className="border-b border-line-1 last:border-b-0">
                    <button
                      type="button"
                      className={`w-full px-3 py-2.5 text-left transition-colors duration-150 ${
                        selected ? 'bg-[rgba(65,118,230,0.06)]' : 'hover:bg-[rgba(38,49,72,0.04)]'
                      }`}
                      onClick={() => selectSession(s.id)}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[11px] tabular-nums text-ink-3">#{s.id}</span>
                        <SessionStatusBadge status={s.status} />
                        <span className="ml-auto text-[11px] tabular-nums text-caption">
                          {formatDate(s.created_at)}
                        </span>
                      </div>
                      <div className="mt-1 truncate text-[12.5px] text-ink">
                        {s.first_question ?? '（还没有提问）'}
                      </div>
                      <div className="mt-0.5 text-[11px] tabular-nums text-caption">
                        {s.message_count} 条消息
                        {s.status === 'registered' && s.registered_asset_id !== null && (
                          <> · 已回流 {formatAssetId(s.registered_asset_id)}</>
                        )}
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </aside>

        {/* 右栏：当前会话 */}
        {selectedId === null ? (
          <div className="panel">
            <Empty
              icon={<ChatCircleDots aria-hidden size={26} />}
              title="没有选中的会话"
              hint="新开一条会话，扮演顾客提问，观察引用芯片、拒答与回流登记的差别。"
              action={
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => void newSession()}
                  disabled={creating}
                >
                  <ChatCircleDots aria-hidden size={13} />
                  新会话
                </button>
              }
            />
          </div>
        ) : detail.state.phase === 'loading' ? (
          <div className="panel">
            <LoadingHint text="正在加载会话…" />
          </div>
        ) : detail.state.phase === 'error' ? (
          <div className="panel">
            <Empty
              icon={<Prohibit aria-hidden size={26} />}
              title={detail.state.error.status === 404 ? '会话不存在' : '会话加载失败'}
              hint={
                detail.state.error.status === 404
                  ? '这条会话可能已被删除。上方横幅可重试，或从左侧选择其他会话。'
                  : 'API 暂时不可用或网络中断，上方横幅可重试。'
              }
            />
          </div>
        ) : session === null ? (
          <div className="panel">
            <Empty icon={<Prohibit aria-hidden size={26} />} title="会话不存在" hint="从左侧选择其他会话。" />
          </div>
        ) : (
          <section
            className="panel flex flex-col overflow-hidden"
            style={{ height: 'calc(100dvh - 180px)', minHeight: 440 }}
          >
            {/* 会话头部 */}
            <div className="flex flex-wrap items-center gap-2 border-b border-line-2 px-4 py-2.5 text-xs text-ink-3">
              <span className="font-mono text-ink">会话 #{session.id}</span>
              <SessionStatusBadge status={session.status} />
              <span>· 开始于 {formatDateTime(session.created_at)}</span>
              {session.status === 'registered' && session.registered_asset_id !== null && (
                <span>
                  · 已回流
                  <Link
                    to={`/platform/assets/${session.registered_asset_id}`}
                    className="ml-1 font-mono underline"
                  >
                    {formatAssetId(session.registered_asset_id)}
                  </Link>
                </span>
              )}
              <span className="flex-1" />
              {canRegister && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setConfirmOpen(true)}
                  title="会话登记为对话资产，进入治理队列"
                >
                  <ArrowUDownLeft aria-hidden size={12} />
                  结束并回流登记
                </button>
              )}
            </div>

            {/* 消息流 */}
            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {messages.length === 0 && (
                <div className="py-6 text-center text-[13px] text-caption">
                  扮演顾客提问试试：
                  <div className="mx-auto mt-3 flex max-w-lg flex-wrap justify-center gap-1.5">
                    {SUGGESTIONS.map((q) => (
                      <button
                        key={q}
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => void send(q)}
                        disabled={!canAsk}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m, i) => (
                <MessageBubble key={m.key} m={m} prev={messages[i - 1]} />
              ))}
              <div ref={endRef} />
            </div>

            {/* composer：仅进行中会话；流式期间锁输入、发送钮原位变停止钮 */}
            {isActiveSession ? (
              <div className="border-t border-line-2 p-3">
                <div className="msg-composer">
                  <textarea
                    ref={taRef}
                    rows={1}
                    placeholder={
                      streaming ? 'AI 客服正在回答…（Esc 停止）' : '输入顾客的问题…（Enter 发送，Shift+Enter 换行）'
                    }
                    value={input}
                    disabled={!canAsk}
                    onChange={(e) => {
                      setInput(e.target.value)
                      e.target.style.height = 'auto'
                      e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        void send(input)
                      }
                    }}
                  />
                  {streaming ? (
                    <button
                      type="button"
                      className="send-btn send-stop"
                      onClick={stop}
                      title="停止输出（Esc）"
                      aria-label="停止输出"
                    >
                      <Stop aria-hidden size={13} weight="fill" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="send-btn"
                      onClick={() => void send(input)}
                      disabled={input.trim() === ''}
                      title="发送（Enter）"
                      aria-label="发送"
                    >
                      <ArrowUp aria-hidden size={16} weight="bold" />
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="border-t border-line-2 px-4 py-2.5 text-xs text-ink-3">
                会话已回流登记，只读。回流出的对话资产在治理台完成人洗与发布后，才能被客服检索引用。
              </div>
            )}
          </section>
        )}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="结束并回流登记"
        confirmLabel="确认回流登记"
        busy={registering}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void register()}
        body={
          <div className="space-y-1.5">
            <p>本会话将转为只读，同时产生：</p>
            <p>· 一条种类为「对话」的新资产（已接入或待人洗，按机洗结果；进入治理队列，治理台可对人洗与发布）</p>
            <p>· 客服检索暂不可见——发布之后才能被引用</p>
          </div>
        }
      />
    </div>
  )
}
