// 顾客页 /customer（ADR 0021）：不登录、不进操作者壳（无主导航/无登录态），
// 会话级身份=服务端签发的令牌（只保存在页面内存，刷新即新会话——顾客拉历史
// 属本刀 Out）。发问走与操作者预览同一引擎、同事件序（thinking -> delta* ->
// complete），引用芯片只读展示（顾客不进控制台）；complete 不带 gap_id，
// 拒答时只呈现「拒答 · 无已发布证据 / 已转人工」。消息气泡复用共享
// MessageBubble（0021 同一视觉语言），本页只做轻页头 + 会话生命周期。
// 429（0033 限流）与 409（会话已被操作者回流登记）都以后端 detail 文案呈现，
// 「新会话」随时可重签令牌。

import { useEffect, useRef, useState } from 'react'
import { ArrowUp, ChatCircleDots } from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import MessageBubble, { type UiMessage } from '../components/MessageBubble'
import { ErrorBanner } from '../components/Banner'
import Empty from '../components/Empty'

const SUGGESTIONS = ['保温杯的净含量是多少？', '怎么退货？']

interface CustomerSession {
  id: number
  token: string
}

export default function CustomerPage() {
  const [session, setSession] = useState<CustomerSession | null>(null)
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [creating, setCreating] = useState(false)
  const [streaming, setStreaming] = useState(false)

  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const seqRef = useRef(0)

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
      setMessages([])
      taRef.current?.focus()
    } catch (err) {
      setError(err)
    } finally {
      setCreating(false)
    }
  }

  // 发送：立即回显顾客消息 + agent 占位，SSE 事件驱动 thinking → delta → complete
  const send = async (text: string) => {
    const question = text.trim()
    if (question === '' || streaming || session === null) return
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    const now = new Date().toISOString()
    const seq = seqRef.current
    seqRef.current += 1
    const agentKey = `local-a-${seq}`
    setError(null)
    setStreaming(true)
    setMessages((prev) => [
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
        fallback: false,
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
        fallback: false,
      },
    ])
    try {
      await api.askCustomer(
        session.id,
        session.token,
        question,
        {
          onThinking: (t) =>
            setMessages((prev) =>
              prev.map((m) => (m.key === agentKey ? { ...m, thinkingText: t } : m)),
            ),
          onDelta: (piece) =>
            setMessages((prev) =>
              prev.map((m) => (m.key === agentKey ? { ...m, content: m.content + piece } : m)),
            ),
          onComplete: (payload) => {
            setMessages((prev) =>
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
                      fallback: payload.fallback ?? false,
                    }
                  : m,
              ),
            )
          },
        },
        new AbortController().signal,
      )
    } catch (err) {
      // 409=会话已被操作者回流登记（令牌失去发问资格）、429=限流、其余=网络；
      // 文案来自后端 detail，重开新会话即可恢复
      setError(err)
      setMessages((prev) =>
        prev.flatMap((m) => {
          if (m.key !== agentKey) return [m]
          // 收到过任何事件（引擎先落库再流式）= 后端确有完整回答留档，
          // 保留已收文本+停止标注；空占位=请求未进引擎（409/401/429/建流
          // 失败），没有回答落库，「已留档」对它是不实陈述——移除，原因由 alert 表达
          if (m.content !== '' || m.thinkingText !== null) {
            return [{ ...m, streaming: false, stopped: true }]
          }
          return []
        }),
      )
    } finally {
      setStreaming(false)
    }
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
                <MessageBubble key={m.key} m={m} prev={messages[i - 1]} citationAsLink={false} />
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
