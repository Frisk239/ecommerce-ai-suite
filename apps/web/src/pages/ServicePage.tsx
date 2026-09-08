// 客服预览页 /service：操作者扮演顾客发问，观察引用 / 拒答 / 回流登记。
// 交互状态机照 UX-NOTES §二点八冻结口径搬自原型：thinking（三点 typing + 检索
// 状态行）→ delta 逐片追加（尾部 CSS 光标）→ complete（引用芯片落位）；停止 =
// 中断 SSE 订阅，已收文本保留（后端断连仍完整落库，stopped 只是前端表达）。
// 不搬的：35ms 定时器模拟、mock 大脑、localStorage 补全——传输由真实 SSE 驱动。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUDownLeft, ArrowUp, ChatCircleDots, Prohibit, Stop } from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import type { ServiceSessionSummary } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { useAskStream } from '../hooks/useAskStream'
import {
  SERVICE_SESSION_STATUS_LABEL,
  ASSET_STATUS_LABEL,
  formatAssetId,
  formatDate,
  formatDateTime,
} from '../labels'
import { ErrorBanner, SuccessBanner } from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import Empty from '../components/Empty'
import MessageBubble, { toUi, type UiMessage } from '../components/MessageBubble'
import { LoadingHint } from '../components/Loading'
import PageHeader from '../components/PageHeader'

const SUGGESTIONS = [
  '保温杯的净含量是多少？',
  '饮用水的保质期是多久？',
  '怎么退货？',
  '保温杯的材质是什么？',
  // 第 13 刀/ADR 0036：带单号即命中订单工具（种子 mock 单 SO-1001 已发货）
  '我的订单 SO-1001 到哪了？',
  // 第 14 刀/ADR 0037：库存关键词即命中库存工具（种子 mock 值：保温杯 42 件有货）
  '钛钢保温杯有货吗？',
]

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

  const [input, setInput] = useState('')
  const [streamError, setStreamError] = useState<unknown>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [registered, setRegistered] = useState<RegisteredBanner | null>(null)
  const [creating, setCreating] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  // 本地流式追加的消息（换会话/回流登记/同步时清空，以服务器为准）——占位构造、
  // SSE 事件回填与停止/失败口径都收敛在共享 hook（第 25 刀；顾客通道同钩）
  const {
    messages: live,
    streaming,
    send: sendStream,
    stop,
    reset: resetLive,
  } = useAskStream({
    ask: (question, handlers, signal) => {
      // 页面守卫保证发问时有选中会话；此分支只为类型收窄（不可达）
      if (selectedId === null) return Promise.resolve()
      return api.askService(selectedId, question, handlers, signal)
    },
    onError: setStreamError,
    // 中止/断流：已收文本保留，标「已停止展示 · 完整回答已留档」
    markStoppedOnFinally: true,
    onSettled: () => list.reload(),
  })

  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  const messages = useMemo<UiMessage[]>(
    () => [...(session?.messages.map(toUi) ?? []), ...live],
    [session, live],
  )

  // 自动跟随：消息条数与已输出字符数变化都滚动到尾部
  const totalChars = messages.reduce((acc, m) => acc + m.content.length, 0)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [messages.length, totalChars])

  // 切换会话：中断在途流（旧会话的已收文本不带入新会话），回到服务器口径
  const selectSession = useCallback(
    (id: number) => {
      resetLive()
      setStreamError(null)
      setChosenId(id)
    },
    [resetLive],
  )

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
      resetLive()
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
  //（状态机回填在 useAskStream；0036 工具条随 tool 事件即时落上，重载经 toUi 还原）
  const send = async (text: string) => {
    const question = text.trim()
    if (question === '' || streaming || selectedId === null) return
    if (session === null || session.status !== 'active') return
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    setStreamError(null)
    await sendStream(question)
  }

  const register = async () => {
    if (selectedId === null || registering) return
    setRegistering(true)
    setActionError(null)
    try {
      const asset = await api.registerServiceSession(selectedId)
      setRegistered({ assetId: asset.id, assetStatus: asset.status })
      setConfirmOpen(false)
      resetLive()
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
    resetLive()
    list.reload()
    detail.reload()
  }, [list, detail, resetLive])

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
                        selected
                          ? 'bg-accent-soft shadow-[inset_2px_0_0_var(--color-accent)]'
                          : 'hover:bg-hover'
                      }`}
                      onClick={() => selectSession(s.id)}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[11px] tabular-nums text-ink-3">#{s.id}</span>
                        <SessionStatusBadge status={s.status} />
                        {s.origin === 'customer' && (
                          <span className="kind-chip" title="来自顾客通道 /customer 的会话（ADR 0021）">
                            顾客
                          </span>
                        )}
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
            <div className="chat-scroll flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {messages.length === 0 && (
                <div className="py-6 text-center text-[13px] text-caption">
                  扮演顾客提问试试：
                  <div className="mx-auto mt-3 flex max-w-lg flex-wrap justify-center gap-1.5">
                    {SUGGESTIONS.map((q) => (
                      <button
                        key={q}
                        type="button"
                        className="btn btn-secondary btn-sm"
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
              <div className="rounded-b-[8px] border-t border-line-2 bg-white p-3">
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
