// 顾客页 /customer（ADR 0021）：不登录、不进操作者壳（无主导航/无登录态），
// 会话级身份=服务端签发的令牌（只保存在页面内存，刷新即新会话——顾客拉历史
// 属本刀 Out）。发问走与操作者预览同一引擎、同事件序（thinking -> delta* ->
// complete），引用芯片只读展示（顾客不进控制台）；complete 不带 gap_id，
// 拒答时只呈现「拒答 · 无已发布证据 / 已转人工」。消息气泡复用共享
// MessageBubble（0021 同一视觉语言），本页只做轻页头 + 会话生命周期。
// 第 42 刀（ADR 0046）：handoff/拒答消息下方挂内联联系方式表单（姓名+留言
// 必填、邮箱/电话可选、可跳过），提交走顾客 Bearer 会话令牌；成功后标「已记录
// 联系方式」（本地 state，工单级）。
// 429（0033 限流）与 409（会话已被操作者回流登记）都以后端 detail 文案呈现，
// 「新会话」随时可重签令牌。

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, ChatCircleDots, Prohibit, Star, Stop, ThumbsDown, ThumbsUp, X } from '@phosphor-icons/react'
import { ApiError, detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { HandoffTicketCreate } from '../api/types'
import MessageBubble, { type UiMessage } from '../components/MessageBubble'
import { ErrorBanner } from '../components/Banner'
import Empty from '../components/Empty'
import { useAskStream } from '../hooks/useAskStream'
import { useEscapeClose } from '../hooks/useEscapeClose'

const SUGGESTIONS = [
  '保温杯的净含量是多少？',
  '怎么退货？',
  // 第 13 刀/ADR 0036：带单号即命中订单工具（与客服页同口径，教顾客带单号提问）
  '我的订单 SO-1001 到哪了？',
  // 第 14 刀/ADR 0037：库存关键词即命中库存工具（种子 mock 值：保温杯 42 件有货）
  '钛钢保温杯有货吗？',
]

interface CustomerSession {
  id: number
  token: string
}

/** 转人工联系方式内联表单（第 42 刀，ADR 0046 §4）：姓名+留言必填、邮箱/电话
 * 可选、整表可跳过（不填也能拿到工单——联系方式只是让工单可回访）。表单只做
 * 必填的本前端校验，格式与 404/409/422 由后端裁决后经 detailText 呈现。 */
function HandoffContactForm({
  onSubmit,
  onSkip,
}: {
  onSubmit: (payload: HandoffTicketCreate) => Promise<void>
  onSkip: () => void
}) {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (busy) return
    if (name.trim() === '' || note.trim() === '') {
      setErr('姓名与留言必填')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      await onSubmit({
        name: name.trim(),
        note: note.trim(),
        ...(email.trim() !== '' ? { email: email.trim() } : {}),
        ...(phone.trim() !== '' ? { phone: phone.trim() } : {}),
      })
    } catch (e) {
      setErr(detailText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="w-full max-w-md space-y-2 rounded-[6px] border border-line-2 bg-white p-2.5">
      <div className="text-[11px] leading-4 text-caption">
        留下联系方式方便我们联系你；姓名与留言必填，邮箱/电话可选，不填也能跳过。
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input
          className="input"
          placeholder="姓名 *"
          aria-label="姓名"
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="input"
          placeholder="电话（可选）"
          aria-label="电话"
          value={phone}
          disabled={busy}
          onChange={(e) => setPhone(e.target.value)}
        />
      </div>
      <input
        className="input"
        placeholder="邮箱（可选）"
        aria-label="邮箱"
        value={email}
        disabled={busy}
        onChange={(e) => setEmail(e.target.value)}
      />
      <textarea
        className="input"
        rows={2}
        placeholder="留言 *（想咨询的问题）"
        aria-label="留言"
        value={note}
        disabled={busy}
        onChange={(e) => setNote(e.target.value)}
      />
      {err !== null && <div className="text-[11px] leading-4 text-danger">{err}</div>}
      <div className="flex items-center gap-2">
        <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => void submit()}>
          {busy ? '提交中…' : '提交联系方式'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={onSkip}>
          暂时不用，跳过
        </button>
      </div>
    </div>
  )
}

export default function CustomerPage({ embed = false }: { embed?: boolean } = {}) {
  const [session, setSession] = useState<CustomerSession | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<unknown>(null)
  // 令牌过期/失效（第 45a 刀 TTL；审计刀 8 P1）：服务端对过期与无效同 401 同文案，
  // 前台必须给一条出路——否则顾客只会反复撞「会话不存在或令牌无效」，输入框还开着。
  const [expired, setExpired] = useState(false)
  const [creating, setCreating] = useState(false)

  // 第 40 刀（ADR 0044 §四）：「没有帮助」反馈——已反馈的消息 id 集合（本地
  // 表达；幂等服务端钉死：同消息二次反馈 409）。换会话即清空。
  const [feedbackSent, setFeedbackSent] = useState<{ id: number; helpful: boolean }[]>([])

  // 第 42 刀（ADR 0046）：工单联系方式已提交/已跳过的工单 id（本地表达；
  // 一会话一单，按工单而非消息计——同会话多条 handoff 消息指向同一工单）。
  // 刷新即新会话（顾客页不保历史），服务端 contact_at 是权威。
  const [contactSentTickets, setContactSentTickets] = useState<number[]>([])
  const [skippedTickets, setSkippedTickets] = useState<number[]>([])

  // 发问状态机与操作者预览共用 useAskStream（第 25 刀）：顾客版 complete 载荷
  // 无 gap_id（服务端白名单裁剪），失败剪掉未进引擎的空占位——口径参数化
  const {
    messages,
    streaming,
    send: sendStream,
    stop,
    reset: resetMessages,
  } = useAskStream({
    ask: (question, handlers, signal) => {
      // 页面守卫保证发问时有会话令牌；此分支只为类型收窄（不可达）
      if (session === null) return Promise.resolve()
      return api.askCustomer(session.id, session.token, question, handlers, signal)
    },
    onError: (err) => {
      setError(err)
      if (err instanceof ApiError && err.status === 401) setExpired(true)
    },
    pruneEmptyOnFailure: true,
  })

  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 自动跟随：消息条数与已输出字符数变化都滚动到尾部
  const totalChars = messages.reduce((acc, m) => acc + m.content.length, 0)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [messages.length, totalChars])

  // 停止：Esc 与发送钮原位替换（与操作者预览同一状态机，UX-NOTES §二点八）
  // 流式中 Esc 停止（审计刀 8：与抽屉/弹层同用 useEscapeClose 单一出处——
  // 原先两页各抄一份同样的 window 监听）
  useEscapeClose(streaming, stop)

  // 嵌入判定（第 45b 刀）：**被框住就算嵌入**，不只看 /widget 路由——否则第三方
  // 直接 iframe /customer 就绕过了来源白名单。宿主来源取自 document.referrer；
  // 被 no-referrer 之类策略剥掉时**不静默退回独立访问**，而是拒绝建会话
  // （fail-closed：宁可拒绝，也不给「剥掉来源就免费嵌」这条路）。
  const framed = window.self !== window.top
  const frameHostOrigin = framed && document.referrer !== '' ? new URL(document.referrer).origin : null
  const frameOriginUnknown = framed && frameHostOrigin === null
  const embedVisitor = framed ? new URLSearchParams(window.location.search).get('visitor') : null

  const startSession = async () => {
    if (creating || streaming) return
    if (frameOriginUnknown) {
      // 被框住但拿不到宿主来源：不建会话（服务端闸无从判断）。正常路径不会到这里，
      // 除非宿主页用 no-referrer 剥掉了来源。
      setError(new Error('无法确认嵌入来源：请在店铺页面内使用本客服（宿主页不要剥离 referrer）'))
      return
    }
    setCreating(true)
    setError(null)
    try {
      const widgetHeaders: Record<string, string> = {}
      if (frameHostOrigin !== null) widgetHeaders['X-Widget-Origin'] = frameHostOrigin
      if (embedVisitor !== null && embedVisitor !== '') widgetHeaders['X-Visitor-Id'] = embedVisitor
      const created = await api.createCustomerSession(widgetHeaders)
      setSession({ id: created.session_id, token: created.token })
      resetMessages()
      setFeedbackSent([])
      setContactSentTickets([])
      setSkippedTickets([])
      // 评分态一并归位（第 48 刀）：它是页面级 state，不重置会让新会话继承上一
      // 会话的「已评分」——评分条永不出现（CSAT 静默丢样本），文案还把旧分安到新会话上
      setRated(null)
      setRatingComment('')
      setRatingSending(false)
      taRef.current?.focus()
    } catch (err) {
      setError(err)
    } finally {
      setCreating(false)
    }
  }

  // 令牌过期后的出路：清掉过期态并重新签发（顾客自己能走完，不用去翻右上角）
  const restartAfterExpiry = async () => {
    setExpired(false)
    setError(null)
    resetMessages()
    setSession(null)
    await startSession()
  }

  // 嵌入形态的「收起」：把关闭意图 postMessage 回宿主（targetOrigin 用宿主来源，
  // 不用 '*'）；独立访问（未被框住）没有宿主，按钮不渲染。
  const closeWidget = () => {
    if (frameHostOrigin !== null) {
      window.parent.postMessage({ type: 'ecom-ai-widget', action: 'close' }, frameHostOrigin)
    }
  }

  // 发送：立即回显顾客消息 + agent 占位，SSE 事件驱动 thinking → delta → complete
  //（状态机回填在 useAskStream；0036 顾客通道 tool 事件照常到达——单号本由提问者给出。
  // 失败口径：409=会话已被操作者回流登记、429=限流、其余=网络，文案来自后端 detail，
  // 重开新会话即可恢复）
  const send = async (text: string) => {
    const question = text.trim()
    if (question === '' || streaming || session === null) return
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    setError(null)
    await sendStream(question)
  }

  // 「没有帮助」（第 40 刀，ADR 0044 §四）：仅 kind=answer 且 citations 非空的
  // 消息出现（拒答/转人工不收反馈）；点击调反馈端点，服务端分诊=逐 citation
  // 资产撤销验证（治理台复审队列承接）。
  const sendFeedback = async (messageId: number, helpful: boolean) => {
    if (session === null) return
    setError(null)
    try {
      await api.leaveFeedback(session.id, session.token, messageId, helpful)
      setFeedbackSent((prev) => [...prev, { id: messageId, helpful }])
    } catch (err) {
      setError(err)
    }
  }

  const feedbackFooter = (m: UiMessage): ReactNode => {
    if (session === null || m.streaming) return undefined
    if (m.role !== 'agent' || m.kind !== 'answer' || m.id === null) return undefined
    if (m.citations === null || m.citations.length === 0) return undefined
    const sent = feedbackSent.find((f) => f.id === m.id)
    if (sent !== undefined) {
      return (
        <span
          className="text-[11px] text-caption"
          title={sent.helpful ? '反馈已记档' : '反馈已记档：引用资料已进入复审'}
        >
          {sent.helpful ? '已反馈 · 感谢' : '已反馈 · 已转入复审'}
        </span>
      )
    }
    const messageId = m.id
    return (
      <span className="inline-flex gap-1.5">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => void sendFeedback(messageId, true)}
          title="有帮助：这条回答解决了问题"
        >
          <ThumbsUp aria-hidden size={12} />
          有帮助
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => void sendFeedback(messageId, false)}
          title="没有帮助：引用的资料可能有错或过期，触发人工复审"
        >
          <ThumbsDown aria-hidden size={12} />
          没有帮助
        </button>
      </span>
    )
  }

  // 会话评分（第 48 刀，CSAT）：页脚一行 1–5 星 + 可选留言，**评过即收**（不弹窗、
  // 不打断了对话；本仓顾客通道没有「结束会话」事件，故不做「结束时弹出」）。
  // 只在有过至少一条 agent 回答后出现——空会话打 1 分是噪声。
  const [hoverScore, setHoverScore] = useState<number | null>(null)
  const [ratingComment, setRatingComment] = useState('')
  const [ratingSending, setRatingSending] = useState(false)
  const [rated, setRated] = useState<number | null>(null)
  // 闸门=「有过一次 AI 回答（任何 kind）」，不是「答出来过」：只被拒答/转人工的
  // 会话恰恰是最想吐槽的那批人，若把他们排除，CSAT 就成了「只统计满意的人」
  // （审计刀 9 P0）。空会话仍然不打分（没有说话就没有服务可评）。
  const hasReply = messages.some((m) => m.role === 'agent' && !m.streaming)

  const submitRating = async (score: number) => {
    if (session === null || ratingSending) return
    setError(null)
    setRatingSending(true)
    try {
      await api.rateSession(session.id, session.token, score, ratingComment.trim() || null)
      setRated(score)
      setRatingComment('')
    } catch (err) {
      setError(err)
    } finally {
      setRatingSending(false)
    }
  }

  const canAsk = session !== null && !streaming && !expired

  // 转人工工单联系方式提交（第 42 刀，ADR 0046 §4）：成功即把该工单标为已记录
  // （同会话多条 handoff 消息共享同一工单，故按工单 id 记）；失败上抛给表单内联呈现。
  const submitHandoff = async (ticketId: number, payload: HandoffTicketCreate) => {
    if (session === null) throw new Error('会话已失效')
    await api.submitHandoffTicket(session.id, ticketId, session.token, payload)
    setContactSentTickets((prev) => [...prev, ticketId])
  }

  const handoffFooter = (m: UiMessage): ReactNode => {
    if (session === null || m.streaming || m.role !== 'agent') return undefined
    if (!m.handoff || m.ticketId === null) return undefined
    const ticketId = m.ticketId
    if (contactSentTickets.includes(ticketId) || m.ticketContactAt !== null) {
      return (
        <span className="text-[11px] text-caption" title="已收到你的联系方式，我们会在工作时间回复">
          已记录联系方式
        </span>
      )
    }
    if (skippedTickets.includes(ticketId)) return undefined
    return (
      <HandoffContactForm
        onSubmit={(payload) => submitHandoff(ticketId, payload)}
        onSkip={() => setSkippedTickets((prev) => [...prev, ticketId])}
      />
    )
  }

  return (
    <div
      className={
        embed || framed
          ? 'flex h-screen flex-col p-3'
          : 'mx-auto flex min-h-screen max-w-3xl flex-col px-4 py-6'
      }
    >
      {/* 轻页头：不搬操作者壳的导航（spec 工程裁决）。嵌入模式收窄：iframe 里
          没有宿主页可退回，取消「新会话」以外的说明文字，右侧加「收起」。 */}
      <header className={embed || framed ? 'mb-3 flex items-center gap-3' : 'mb-4 flex items-center gap-3'}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] border border-line-2 bg-white text-ink-2 shadow-sm">
          <ChatCircleDots aria-hidden size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-[15px] font-semibold leading-6 text-ink">AI 客服</h1>
          {embed || framed ? null : (
            <p className="text-xs leading-4 text-ink-3">回答只依据已发布资料并附引用；没有证据会拒答并转人工处理。</p>
          )}
        </div>
        {session !== null && (
          <button
            type="button"
            className="btn btn-secondary btn-sm shrink-0"
            onClick={() => void startSession()}
            disabled={creating || streaming}
            title="重新签发会话令牌，开始一段全新咨询"
          >
            <ChatCircleDots aria-hidden size={12} />
            {creating ? '创建中…' : '新会话'}
          </button>
        )}
        {frameHostOrigin !== null ? (
          <button
            type="button"
            className="btn btn-ghost btn-sm shrink-0"
            onClick={closeWidget}
            aria-label="收起客服"
            title="收起客服"
          >
            <X aria-hidden size={14} />
          </button>
        ) : null}
      </header>

      {expired ? (
        <div className="mb-3 flex flex-wrap items-center gap-2.5 rounded-[8px] border border-[rgba(154,91,6,0.25)] bg-[rgba(154,91,6,0.05)] px-3.5 py-2.5 text-[13px] leading-5 text-warn">
          <span className="min-w-0 flex-1">
            这段会话的访问令牌已过期（有效期 24 小时），需要重新开始一段咨询。
          </span>
          <button
            type="button"
            className="btn btn-primary btn-sm shrink-0"
            onClick={() => void restartAfterExpiry()}
            disabled={creating}
          >
            {creating ? '创建中…' : '重新开始'}
          </button>
        </div>
      ) : null}
      {error !== null && !expired ? <ErrorBanner error={error} /> : null}

      <section
        className="panel flex min-h-0 flex-1 flex-col overflow-hidden"
        style={{ minHeight: embed || framed ? 0 : 420 }}
      >
        {session === null ? (
          /* 空态在面板内垂直居中：顾客页是产品门面，内容别吊在顶上。
             被框住却拿不到宿主来源（宿主剥离了 referrer）时不给「开始咨询」——
             fail-closed，理由写在面上（否则剥掉 referrer 就成了绕过白名单的免费路）。 */
          <div className="flex flex-1 items-center justify-center">
            {frameOriginUnknown ? (
              <Empty
                icon={<Prohibit aria-hidden size={26} />}
                title="无法确认嵌入来源"
                hint="本客服被嵌在页面里，但宿主页没有提供来源信息（referrer 被剥离）。请让宿主页保留 referrer 后重试，或直接打开客服页。"
              />
            ) : (
              <Empty
                icon={<ChatCircleDots aria-hidden size={26} />}
                title="开始咨询"
                hint="无需注册登录：点击开始，服务端为这段对话签发一次性会话身份。"
                action={
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => void startSession()}
                    disabled={creating}
                  >
                    <ChatCircleDots aria-hidden size={14} />
                    {creating ? '创建中…' : '开始咨询'}
                  </button>
                }
              />
            )}
          </div>
        ) : (
          <>
            {/* 消息流（共享 MessageBubble；引用芯片只读——顾客不进控制台） */}
            <div className="chat-scroll flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {messages.length === 0 && (
                <div className="py-6 text-center text-[13px] text-caption">
                  试着问问：
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
                <MessageBubble
                  key={m.key}
                  m={m}
                  prev={messages[i - 1]}
                  citationAsLink={false}
                  footer={handoffFooter(m) ?? feedbackFooter(m)}
                />
              ))}
              <div ref={endRef} />
            </div>

            {/* 会话评分条（第 48 刀，CSAT）：有过回答才出现；评过变一行致谢 */}
            {hasReply && (
              <div className="border-t border-line-2 bg-canvas px-3 py-2">
                {rated !== null ? (
                  <div className="text-[11px] text-caption" role="status">
                    谢谢反馈：你给这次服务打了 {rated} 星。
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[11px] text-caption">这次服务怎么样？</span>
                    <span className="inline-flex items-center gap-0.5" role="radiogroup" aria-label="服务评分">
                      {[1, 2, 3, 4, 5].map((score) => (
                        <button
                          key={score}
                          type="button"
                          role="radio"
                          aria-checked={false}
                          aria-label={`${score} 星`}
                          className="star-btn"
                          disabled={ratingSending}
                          onMouseEnter={() => setHoverScore(score)}
                          onMouseLeave={() => setHoverScore(null)}
                          onClick={() => void submitRating(score)}
                          title={`${score} 星`}
                        >
                          <Star
                            aria-hidden
                            size={16}
                            weight={(hoverScore ?? 0) >= score ? 'fill' : 'regular'}
                          />
                        </button>
                      ))}
                    </span>
                    <input
                      className="input h-7 min-w-40 flex-1 text-[12px]"
                      placeholder="想补充点什么？（可选）"
                      value={ratingComment}
                      maxLength={500}
                      disabled={ratingSending}
                      onChange={(e) => setRatingComment(e.target.value)}
                    />
                    <span className="text-[11px] text-caption">点星星即提交</span>
                  </div>
                )}
              </div>
            )}

            {/* composer：流式期间锁输入 */}
            <div className="rounded-b-[8px] border-t border-line-2 bg-white p-3">
              <div className="msg-composer">
                <textarea
                  ref={taRef}
                  rows={1}
                  placeholder={
                    streaming ? 'AI 客服正在回答…（Esc 停止）' : '输入你的问题…（Enter 发送，Shift+Enter 换行）'
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
                    disabled={input.trim() === '' || !canAsk}
                    title="发送（Enter）"
                    aria-label="发送"
                  >
                    <ArrowUp aria-hidden size={16} weight="bold" />
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
