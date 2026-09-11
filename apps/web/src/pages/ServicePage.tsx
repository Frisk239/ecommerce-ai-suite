// 客服预览页 /service：操作者扮演顾客发问，观察引用 / 拒答 / 回流登记。
// 交互状态机照 UX-NOTES §二点八冻结口径搬自原型：thinking（三点 typing + 检索
// 状态行）→ delta 逐片追加（尾部 CSS 光标）→ complete（引用芯片落位）；停止 =
// 中断 SSE 订阅，已收文本保留（后端断连仍完整落库，stopped 只是前端表达）。
// 不搬的：35ms 定时器模拟、mock 大脑、localStorage 补全——传输由真实 SSE 驱动。
// 第 42 刀（ADR 0046）：会话列表待处理工单置顶 + 徽章 + 「全部/待处理工单」分段；
// 详情头部显示工单号/状态/掩码联系方式，并给「结单」动作（ConfirmDialog 确认）。

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowUDownLeft,
  ArrowUp,
  ChatCircleDots,
  CheckCircle,
  Prohibit,
  Stop,
} from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import type { ServiceSessionSummary } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { useAskStream } from '../hooks/useAskStream'
import { useEscapeClose } from '../hooks/useEscapeClose'
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
  // 第 13 刀/ADR 0036：带单号即命中订单工具（种子 mock 单 SO-1001 初始已发货；第 44 刀
// 退货流演示真跑过后状态会停在「退货中」——审计刀 13 C 轴注：别把状态写进口播稿）
  '我的订单 SO-1001 到哪了？',
  // 第 14 刀/ADR 0037：库存关键词即命中库存工具（种子 mock 值：保温杯 42 件有货）
  '钛钢保温杯有货吗？',
]

// 稳定空数组：加载/错误态复用同一引用，排序 useMemo 的依赖才不会每渲染都变。
const EMPTY_SESSIONS: ServiceSessionSummary[] = []

function SessionStatusBadge({
  status,
  messageCount,
}: {
  status: ServiceSessionSummary['status']
  /** 已知消息数（列表用 message_count，详情头部用已加载 messages 数）：
   *  0 且 active = 还没开口，不是「进行中」。undefined 时不判空会话。 */
  messageCount?: number
}) {
  if (status === 'active') {
    // 空会话是噪音不是进展：中性灰「未开始」，不用进行中的蓝。
    if (messageCount === 0) {
      return <span className="badge badge-ingested">未开始</span>
    }
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
  const sessions = list.state.phase === 'ok' ? list.state.data : EMPTY_SESSIONS

  // 选择模型：null = 尚未选择（跟随最新一条有消息的会话，别落到空会话）；点了哪条就固定在哪条
  const [chosenId, setChosenId] = useState<number | null>(null)
  // URL 参数：?session=N 深链（缺口抽屉「来源会话」）与 ?q= 会话搜索（第 77 刀）
  // 共用一份 searchParams——setSearchParams 函数式更新互不抹除
  const [searchParams, setSearchParams] = useSearchParams()
  const sessionParam = searchParams.get('session')
  const linkedId = sessionParam !== null && /^\d+$/.test(sessionParam) ? Number(sessionParam) : null
  const linkedSession =
    linkedId !== null && list.state.phase === 'ok'
      ? (list.state.data.find((s) => s.id === linkedId) ?? null)
      : null
  const defaultSession =
    list.state.phase === 'ok'
      ? (list.state.data.find((s) => s.message_count > 0) ?? list.state.data[0] ?? null)
      : null
  const selectedId = chosenId ?? linkedSession?.id ?? defaultSession?.id ?? null

  // 列表排序：待处理工单置顶（第 42 刀，ADR 0046 §5）-> 0 消息会话沉底
  // （组内按 id desc，稳定不跳位）
  const [ticketFilter, setTicketFilter] = useState<'all' | 'pending'>('all')
  const orderedSessions = useMemo(
    () =>
      [...sessions].sort((a, b) => {
        const ap = a.pending_ticket_count > 0 ? 0 : 1
        const bp = b.pending_ticket_count > 0 ? 0 : 1
        const az = a.message_count === 0 ? 1 : 0
        const bz = b.message_count === 0 ? 1 : 0
        return ap - bp || az - bz || b.id - a.id
      }),
    [sessions],
  )
  // 「待处理工单」分段筛选（先例：资产页缺口分段）；计数按会话（有待处理工单的会话数）
  // 第 77 刀：会话搜索（?q= 与筛选互不抹除，先例：资产页来源筛选）；命中口径 =
  // 会话号（#123/123）或首问包含（大小写不敏感）。搜索不改排序（工单置顶口径不动）。
  const query = (searchParams.get('q') ?? '').trim()
  const setQuery = (value: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value.trim() === '') next.delete('q')
        else next.set('q', value.trim())
        return next
      },
      { replace: true },
    )
  }
  const visibleSessions = useMemo(() => {
    const filtered =
      ticketFilter === 'pending'
        ? orderedSessions.filter((s) => s.pending_ticket_count > 0)
        : orderedSessions
    if (query === '') return filtered
    // 「#204」与「204」都按会话号精确命中（占位符示例就是 #125——不带 # 反而搜不到）
    const idNeedle = query.replace(/^#/, '')
    const needle = query.toLowerCase()
    return filtered.filter(
      (s) =>
        (/^\d+$/.test(idNeedle) && String(s.id) === idNeedle) ||
        (s.first_question ?? '').toLowerCase().includes(needle),
    )
  }, [orderedSessions, ticketFilter, query])
  const pendingSessionCount = useMemo(
    () => sessions.filter((s) => s.pending_ticket_count > 0).length,
    [sessions],
  )
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

  // 第 40 刀（ADR 0044 §一）：两阶段写确认卡状态——待确认消息 id 集合与
  // 在途确认 id；确认成功后刷新会话即见服务端落的 create_return 工具轨迹。
  const [returnConfirmed, setReturnConfirmed] = useState<number[]>([])
  const [returnConfirming, setReturnConfirming] = useState<number | null>(null)
  // 确认退货是两阶段写的第二段（不可逆：执行后订单状态迁移到退货中、无「反确认」），
  // 与发布/结单同形——先确认再执行，不做一击即写（第 44 刀顺手补齐的口径一致性）
  const [returnPending, setReturnPending] = useState<{ messageId: number; token: string } | null>(
    null,
  )

  // 第 42 刀（ADR 0046 §5）：工单结单确认框与在途态；结单成功后刷新会话列表
  // （pending_ticket_count 与置顶随之更新）与详情（工单状态转 resolved）。
  const [resolveConfirmOpen, setResolveConfirmOpen] = useState(false)
  const [resolving, setResolving] = useState(false)

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
      setReturnConfirmed([])
      setResolveConfirmOpen(false)
      setStreamError(null)
      setChosenId(id)
    },
    [resetLive],
  )

  // Esc 停止（textarea 流式期间被禁用，事件不再冒泡，挂 window 级）
  // 流式中 Esc 停止（审计刀 8：与抽屉/弹层同用 useEscapeClose 单一出处——
  // 原先两页各抄一份同样的 window 监听）
  useEscapeClose(streaming, stop)

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
  // 两阶段写阶段二（第 40 刀，ADR 0044 §一）：资格消息工具条里的
  // 「待确认 token=…」即确认卡——本端点验签+资格重查后写订单事件并落
  // create_return 工具轨迹（detail.reload() 刷新即见，回放完整）。
  const confirmReturn = async (messageId: number, token: string) => {
    if (selectedId === null || returnConfirming !== null) return
    setReturnConfirming(messageId)
    setActionError(null)
    try {
      await api.confirmReturn(selectedId, messageId, token)
      setReturnConfirmed((prev) => [...prev, messageId])
      detail.reload()
    } catch (err) {
      setActionError(err)
    } finally {
      setReturnConfirming(null)
    }
  }

  // 工单结单（第 42 刀，ADR 0046 §5）：pending -> resolved；幂等服务端 409。
  // 结单只影响工单，不触碰知识缺口（两者独立，0046 §3）。
  const resolveHandoffTicket = async () => {
    if (session === null || session.ticket === null || resolving) return
    setResolving(true)
    setActionError(null)
    try {
      await api.resolveHandoffTicket(session.ticket.id)
      setResolveConfirmOpen(false)
      list.reload()
      detail.reload()
    } catch (err) {
      setActionError(err)
      setResolveConfirmOpen(false)
    } finally {
      setResolving(false)
    }
  }

  // 资格消息的确认卡 footer：工具条 result 含「待确认 token=…」才有按钮
  // （不可退货/查无没有令牌，自然不渲染）；已确认就地标注，无复杂 UI。
  const confirmFooter = (m: UiMessage): ReactNode => {
    if (m.tool === null || m.tool.name !== 'check_return_eligibility') return undefined
    if (m.streaming) return undefined
    const confirmed = (m.id !== null && returnConfirmed.includes(m.id)) || m.tool.result.includes('已确认')
    if (confirmed) {
      return (
        <span className="text-[11px] text-caption" title="确认后已写入订单退货事件">
          已确认退货
        </span>
      )
    }
    if (m.id === null) return undefined
    const match = /token=([0-9a-f]+)/.exec(m.tool.result)
    if (match === null) return undefined
    return (
      <button
        type="button"
        className="btn btn-primary btn-sm"
        disabled={returnConfirming !== null}
        onClick={() => setReturnPending({ messageId: m.id as number, token: match[1] })}
        title="两阶段写第二段：确认后执行退货、写入订单事件并把订单状态改为退货中"
      >
        {returnConfirming === m.id ? '确认中…' : '确认退货'}
      </button>
    )
  }

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
        desc="预览顾客对话：只引已发布证据，无证据则拒答转人工。"
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
          {/* 第 42 刀（ADR 0046 §5）：全部 / 待处理工单分段（先例：资产页缺口分段） */}
          <div className="border-b border-line-1 px-3 py-2">
            <div className="seg" role="tablist" aria-label="会话筛选">
              <button
                type="button"
                role="tab"
                aria-selected={ticketFilter === 'all'}
                className={`seg-btn ${ticketFilter === 'all' ? 'seg-btn-active' : ''}`}
                onClick={() => setTicketFilter('all')}
              >
                全部
                <span className={ticketFilter === 'all' ? 'text-ink-3' : ''}>
                  {list.state.phase === 'ok' ? sessions.length : '—'}
                </span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={ticketFilter === 'pending'}
                className={`seg-btn ${ticketFilter === 'pending' ? 'seg-btn-active' : ''}`}
                onClick={() => setTicketFilter('pending')}
                title="有待处理转人工工单的会话（置顶）"
              >
                待处理工单
                <span className={ticketFilter === 'pending' ? 'text-ink-3' : ''}>
                  {list.state.phase === 'ok' ? pendingSessionCount : '—'}
                </span>
              </button>
            </div>
          </div>
          {/* 第 77 刀：会话搜索（先例：资产页搜索框）——找演示会话不用翻 119 条 */}
          <div className="border-b border-line-1 px-3 py-2">
            <div className="relative">
              <input
                className="input h-7 w-full text-[12px]"
                placeholder="搜索会话：首问或 #号（如 保温杯、#125）"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="搜索会话"
              />
              {query !== '' ? (
                <button
                  type="button"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-caption hover:text-ink"
                  onClick={() => setQuery('')}
                  aria-label="清空搜索"
                >
                  ✕
                </button>
              ) : null}
            </div>
            {query !== '' ? (
              <div className="mt-1 text-[11px] text-ink-3">
                {visibleSessions.length} 条匹配 · 按首问/会话号，清空恢复
              </div>
            ) : null}
          </div>
          {list.state.phase === 'loading' ? (
            <div className="py-8 text-center text-xs text-ink-3">加载中…</div>
          ) : list.state.phase === 'error' ? (
            <div className="px-3 py-6 text-center text-xs text-ink-3">
              会话列表加载失败，上方横幅可重试
            </div>
          ) : visibleSessions.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs leading-5 text-ink-3">
              {ticketFilter === 'pending' ? '没有待处理工单的会话。' : '还没有历史会话。'}
              {ticketFilter === 'all' && (
                <>
                  <br />
                  新开一条会话，扮演顾客提问。
                </>
              )}
            </div>
          ) : (
            <ul className="max-h-[calc(100dvh-320px)] overflow-y-auto">
              {visibleSessions.map((s) => {
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
                        <SessionStatusBadge status={s.status} messageCount={s.message_count} />
                        {s.pending_ticket_count > 0 && (
                          <span className="badge badge-review" title="有顾客要求转人工，待处理">
                            工单 {s.pending_ticket_count}
                          </span>
                        )}
                        {s.origin === 'customer' && (
                          <span className="kind-chip" title="来自顾客通道 /customer 的会话">
                            顾客
                          </span>
                        )}
                        {s.rating != null && (
                          <span
                            className="kind-chip"
                            title={`顾客对这次会话的评分：${s.rating} 星（第 48 刀 CSAT）`}
                          >
                            ★ {s.rating}
                          </span>
                        )}
                        {s.host_origin !== null && s.host_origin !== '' ? (
                          <span
                            className="kind-chip"
                            title={`嵌入小组件的宿主站点（第 54 刀：过闸来源落库）：${s.host_origin}`}
                          >
                            站点 {s.host_origin.replace(/^https?:\/\//, '').slice(0, 18)}
                          </span>
                        ) : null}
                        {s.visitor_id !== null && s.visitor_id !== '' ? (
                          <span
                            className="kind-chip"
                            title={`嵌入小组件的宿主访客 id（商家自己那边的标识）：${s.visitor_id}`}
                          >
                            访客 {s.visitor_id.slice(0, 8)}
                          </span>
                        ) : null}
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
              {/* 详情契约无 message_count：用已加载消息数分叉，空会话与列表同为「未开始」 */}
              <SessionStatusBadge status={session.status} messageCount={messages.length} />
              <span>· 开始于 {formatDateTime(session.created_at)}</span>
              {/* 第 57 刀：站点/访客/评分与列表行同口径——深链进详情也能看到同一上下文 */}
              {session.host_origin !== null && session.host_origin !== '' && (
                <span
                  className="kind-chip"
                  title={`嵌入小组件的宿主站点：${session.host_origin}`}
                >
                  站点 {session.host_origin.replace(/^https?:\/\//, '').slice(0, 18)}
                </span>
              )}
              {session.visitor_id !== null && session.visitor_id !== '' && (
                <span className="kind-chip" title={`宿主访客 id：${session.visitor_id}`}>
                  访客 {session.visitor_id.slice(0, 8)}
                </span>
              )}
              {session.rating != null && (
                <span
                  className="kind-chip"
                  title={
                    session.rating_updated_at != null
                      ? `顾客对这次会话的评分：${session.rating} 星（改过，第 71 刀评分可改）`
                      : `顾客对这次会话的评分：${session.rating} 星`
                  }
                >
                  ★ {session.rating}
                  {session.rating_updated_at != null ? ' · 改过' : ''}
                </span>
              )}
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
              {/* 第 42 刀（ADR 0046 §5）：本会话工单——号 + 状态；联系方式与
                  留言单独一行展示（掩码后），见下方 */}
              {session.ticket !== null && (
                <span className="flex flex-wrap items-center gap-1.5">
                  · 工单 <span className="font-mono text-ink-2">{session.ticket.ticket_no}</span>
                  <span className={session.ticket.status === 'pending' ? 'badge badge-review' : 'badge badge-ingested'}>
                    {session.ticket.status === 'pending' ? '待处理' : '已结单'}
                  </span>
                </span>
              )}
              <span className="flex-1" />
              {session.ticket !== null && session.ticket.status === 'pending' && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setResolveConfirmOpen(true)}
                  title="顾客问题已有人回复处理，结单"
                >
                  <CheckCircle aria-hidden size={12} />
                  结单
                </button>
              )}
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

            {/* 工单联系方式面板（第 42 刀，ADR 0046 §5/§6）：顾客留的联系方式与
                留言——电话/邮箱/留言均掩码，姓名不掩；contact_at 为 null 时无面板 */}
            {session.ticket !== null && session.ticket.contact_at !== null && (
              <div className="border-b border-line-2 bg-surface px-4 py-2 text-xs leading-5 text-ink-2">
                <div className="flex flex-wrap items-center gap-x-3">
                  <span className="text-caption" title="联系方式与留言中的电话/邮箱已掩码，姓名不掩">
                    工单联系方式
                  </span>
                  <span className="font-medium text-ink">{session.ticket.name ?? '—'}</span>
                  {session.ticket.phone !== null && <span className="tabular-nums">{session.ticket.phone}</span>}
                  {session.ticket.email !== null && <span className="font-mono">{session.ticket.email}</span>}
                  <span className="text-caption">
                    留于 {formatDateTime(session.ticket.contact_at)}
                  </span>
                </div>
                {session.ticket.note !== null && (
                  <div className="mt-0.5">
                    <span className="text-caption">留言：</span>
                    {session.ticket.note}
                  </div>
                )}
              </div>
            )}

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
                <MessageBubble key={m.key} m={m} prev={messages[i - 1]} footer={confirmFooter(m)} />
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

      {/* 第 42 刀（ADR 0046 §5）：工单结单确认（Esc 关闭由 ConfirmDialog 接入） */}
      <ConfirmDialog
        open={resolveConfirmOpen}
        title="结单"
        confirmLabel="确认结单"
        busy={resolving}
        onCancel={() => setResolveConfirmOpen(false)}
        onConfirm={() => void resolveHandoffTicket()}
        body={
          <div className="space-y-1.5">
            <p>确认顾客问题已有人回复处理？结单后该工单不再计入待处理。</p>
            <p>工单与知识缺口相互独立：结单不会关闭、也不会新建任何知识缺口。</p>
          </div>
        }
      />
      {/* 第 44 刀：确认退货（两阶段写第二段）——不可逆，与发布/结单同形先确认 */}
      <ConfirmDialog
        open={returnPending !== null}
        title="确认退货"
        confirmLabel="确认退货"
        busy={returnConfirming !== null}
        onCancel={() => setReturnPending(null)}
        onConfirm={() => {
          const pending = returnPending
          if (pending === null) return
          void (async () => {
            // 等写动作落地再关框：busy 期间框留着（与发布/结单同形），
            // 失败时框也留着由 actionError 表达，不给「关了但没成功」的错觉
            await confirmReturn(pending.messageId, pending.token)
            setReturnPending(null)
          })()
        }}
        body={
          <div className="space-y-1.5">
            <p>确认后执行退货：订单追加退货事件，状态迁移为「退货中」。</p>
            <p>该动作不可撤销（没有「反确认」）；重复确认会被服务端拒绝（409）。</p>
          </div>
        }
      />
    </div>
  )
}
