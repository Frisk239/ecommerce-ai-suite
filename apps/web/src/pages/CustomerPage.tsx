// 顾客页 /customer（ADR 0021）：不登录、不进操作者壳（无主导航/无登录态），
// 会话级身份=服务端签发的令牌（只保存在页面内存，刷新即新会话——顾客拉历史
// 属本刀 Out）。发问走与操作者预览同一引擎、同事件序（thinking -> delta* ->
// complete），引用芯片只读展示（顾客不进控制台）；complete 不带 gap_id，
// 拒答时只呈现「拒答 · 无已发布证据 / 已转人工」。消息气泡复用共享
// MessageBubble（0021 同一视觉语言），本页只做轻页头 + 会话生命周期。
// 429（0033 限流）与 409（会话已被操作者回流登记）都以后端 detail 文案呈现，
// 「新会话」随时可重签令牌。

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, ChatCircleDots, ThumbsDown } from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import MessageBubble, { type UiMessage } from '../components/MessageBubble'
import { ErrorBanner } from '../components/Banner'
import Empty from '../components/Empty'
import { useAskStream } from '../hooks/useAskStream'

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

export default function CustomerPage() {
  const [session, setSession] = useState<CustomerSession | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [creating, setCreating] = useState(false)

  // 第 40 刀（ADR 0044 §四）：「没有帮助」反馈——已反馈的消息 id 集合（本地
  // 表达；幂等服务端钉死：同消息二次反馈 409）。换会话即清空。
  const [feedbackSent, setFeedbackSent] = useState<number[]>([])

  // 发问状态机与操作者预览共用 useAskStream（第 25 刀）：顾客版 complete 载荷
  // 无 gap_id（服务端白名单裁剪），失败剪掉未进引擎的空占位——口径参数化
  const {
    messages,
    streaming,
    send: sendStream,
    reset: resetMessages,
  } = useAskStream({
    ask: (question, handlers, signal) => {
      // 页面守卫保证发问时有会话令牌；此分支只为类型收窄（不可达）
      if (session === null) return Promise.resolve()
      return api.askCustomer(session.id, session.token, question, handlers, signal)
    },
    onError: setError,
    pruneEmptyOnFailure: true,
  })

  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 自动跟随：消息条数与已输出字符数变化都滚动到尾部
  const totalChars = messages.reduce((acc, m) => acc + m.content.length, 0)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [messages.length, totalChars])

  const startSession = async () => {
    if (creating || streaming) return
    setCreating(true)
    setError(null)
    try {
      const created = await api.createCustomerSession()
      setSession({ id: created.session_id, token: created.token })
      resetMessages()
      setFeedbackSent([])
      taRef.current?.focus()
    } catch (err) {
      setError(err)
    } finally {
      setCreating(false)
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
  const sendFeedback = async (messageId: number) => {
    if (session === null) return
    setError(null)
    try {
      await api.leaveFeedback(session.id, session.token, messageId)
      setFeedbackSent((prev) => [...prev, messageId])
    } catch (err) {
      setError(err)
    }
  }

  const feedbackFooter = (m: UiMessage): ReactNode => {
    if (session === null || m.streaming) return undefined
    if (m.role !== 'agent' || m.kind !== 'answer' || m.id === null) return undefined
    if (m.citations === null || m.citations.length === 0) return undefined
    if (feedbackSent.includes(m.id)) {
      return (
        <span className="text-[11px] text-caption" title="反馈已记档：引用资料已进入复审">
          已反馈 · 感谢
        </span>
      )
    }
    return (
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={() => void sendFeedback(m.id as number)}
        title="没有帮助：引用的资料可能有错或过期，触发人工复审"
      >
        <ThumbsDown aria-hidden size={12} />
        没有帮助
      </button>
    )
  }

  const canAsk = session !== null && !streaming

  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col px-4 py-6">
      {/* 轻页头：不搬操作者壳的导航（spec 工程裁决） */}
      <header className="mb-4 flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] border border-line-2 bg-white text-ink-2 shadow-sm">
          <ChatCircleDots aria-hidden size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-[15px] font-semibold leading-6 text-ink">AI 客服</h1>
          <p className="text-xs leading-4 text-ink-3">回答只依据已发布资料并附引用；没有证据会拒答并转人工处理。</p>
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
      </header>

      {error !== null && <ErrorBanner error={error} />}

      <section className="panel flex min-h-0 flex-1 flex-col overflow-hidden" style={{ minHeight: 420 }}>
        {session === null ? (
          /* 空态在面板内垂直居中：顾客页是产品门面，内容别吊在顶上 */
          <div className="flex flex-1 items-center justify-center">
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
                <MessageBubble key={m.key} m={m} prev={messages[i - 1]} citationAsLink={false} footer={feedbackFooter(m)} />
              ))}
              <div ref={endRef} />
            </div>

            {/* composer：流式期间锁输入 */}
            <div className="rounded-b-[8px] border-t border-line-2 bg-white p-3">
              <div className="msg-composer">
                <textarea
                  ref={taRef}
                  rows={1}
                  placeholder={streaming ? 'AI 客服正在回答…' : '输入你的问题…（Enter 发送，Shift+Enter 换行）'}
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
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
