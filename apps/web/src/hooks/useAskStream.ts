// 双通道发问共享状态机（第 25 刀收口，行为零变化）：顾客回显 + agent 占位的本地
// 消息列表、SSE 事件回填（thinking → [tool] → delta* → complete，UX-NOTES §二点八）、
// 停止与换会话清理——ServicePage（操作者预览）与 CustomerPage（顾客通道）消费同一
// hook。两页差异参数化：
// - 传输与凭证：ask 由页面闭包给出（askService 走 cookie；askCustomer 走 Bearer
//   会话令牌）；complete 的 gap_id 裁剪随载荷类型自然成立——顾客版载荷无该字段，
//   回填 `?? null` 与旧实现「不动 gapId（初值 null）」等值；
// - 失败口径：操作者页 finally 统一标 stopped=!completed（覆盖 abort 静默返回与
//   断流）；顾客页在 catch 剪掉未进引擎的空占位（「已留档」对空占位是不实陈述）；
// - 收尾副作用：操作者页 onSettled 刷新会话列表。
// 建议列表等页面差异留在页面内；SSE 协议与事件序不动。

import { useCallback, useRef, useState } from 'react'
import type { ServiceAnswerComplete, ToolCallRecord } from '../api/types'
import { toUiMessage, type UiMessage } from '../components/MessageBubble'

/** complete 载荷：操作者通道带 gap_id（ADR 0030）；顾客通道被服务端白名单裁剪
 * （0021）——类型上记为可选，两通道共用一个回填。 */
export type AskStreamComplete = Omit<ServiceAnswerComplete, 'gap_id'> & { gap_id?: number | null }

/** SSE 事件回调，与 endpoints 的 AskHandlers/CustomerAskHandlers 结构兼容。 */
export interface AskStreamHandlers {
  onThinking: (text: string) => void
  /** 工具调用条数据（{name, arg, result}）：complete 前到达，工具条即时呈现
   * （第 13 刀/ADR 0036，两通道同形状不裁剪）。 */
  onTool: (record: ToolCallRecord) => void
  onDelta: (text: string) => void
  onComplete: (payload: AskStreamComplete) => void
}

export interface AskStreamOptions {
  /** 发问传输：页面闭包包装 api.askService / api.askCustomer。 */
  ask: (question: string, handlers: AskStreamHandlers, signal: AbortSignal) => Promise<void>
  /** 传输抛错（409/429/网络等）：由页面挂错误横幅呈现。 */
  onError: (err: unknown) => void
  /** 顾客通道：失败时占位若没收到任何事件（请求未进引擎、无回答落库）则移除。 */
  pruneEmptyOnFailure?: boolean
  /** 操作者预览：finally 统一标占位 streaming:false、stopped:!completed。 */
  markStoppedOnFinally?: boolean
  /** 流结束（成功/失败/中止）收尾：操作者页刷新会话列表。 */
  onSettled?: () => void
}

export interface AskStream {
  /** 本地追加消息（顾客回显 + agent 占位/流式）；操作者页自行拼在服务器历史后。 */
  messages: UiMessage[]
  /** 发问进行中（send 入口置真、finally 置假）：锁输入、发送钮原位变停止钮。 */
  streaming: boolean
  /** 发送一次提问（question 须已 trim、非空且通过页面守卫）。 */
  send: (question: string) => Promise<void>
  /** 中断在途流：已收文本保留（streamSse 对 abort 静默返回，不走 onError）。 */
  stop: () => void
  /** 换会话/同步服务器口径：中断在途流并清空本地消息。 */
  reset: () => void
}

export function useAskStream(options: AskStreamOptions): AskStream {
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)

  // send 每次渲染重建（与重构前两页内联实现的闭包新鲜度一致），直接读 options
  const send = async (question: string) => {
    const seq = seqRef.current
    seqRef.current += 1
    const agentKey = `local-a-${seq}`
    const now = new Date().toISOString()
    setStreaming(true)
    setMessages((prev) => [
      ...prev,
      toUiMessage({ key: `local-c-${seq}`, role: 'customer', content: question, created_at: now }),
      toUiMessage({ key: agentKey, role: 'agent', content: '', created_at: now, streaming: true }),
    ])
    const patchAgent = (patch: (m: UiMessage) => UiMessage) =>
      setMessages((prev) => prev.map((m) => (m.key === agentKey ? patch(m) : m)))
    const controller = new AbortController()
    abortRef.current = controller
    let completed = false
    try {
      await options.ask(
        question,
        {
          onThinking: (t) => patchAgent((m) => ({ ...m, thinkingText: t })),
          onTool: (record) => patchAgent((m) => ({ ...m, tool: record })),
          onDelta: (piece) => patchAgent((m) => ({ ...m, content: m.content + piece })),
          onComplete: (payload) => {
            completed = true
            patchAgent((m) => ({
              ...m,
              id: payload.message_id,
              citations: payload.citations,
              kind: payload.kind,
              handoff: payload.handoff,
              streaming: false,
              stopped: false,
              gapId: payload.gap_id ?? null,
              fallback: payload.fallback ?? false,
              tool: payload.tool ?? m.tool,
            }))
          },
        },
        controller.signal,
      )
    } catch (err) {
      options.onError(err)
      if (options.pruneEmptyOnFailure) {
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
      }
    } finally {
      abortRef.current = null
      // 中止/断流：已收文本保留，标「已停止展示 · 完整回答已留档」
      if (options.markStoppedOnFinally) {
        patchAgent((m) => ({ ...m, streaming: false, stopped: !completed }))
      }
      setStreaming(false)
      options.onSettled?.()
    }
  }

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setMessages([])
    setStreaming(false)
  }, [])

  return { messages, streaming, send, stop, reset }
}
