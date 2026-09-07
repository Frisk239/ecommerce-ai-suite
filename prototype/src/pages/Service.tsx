import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUp,
  ArrowUDownLeft,
  ChatCircleDots,
  GearSix,
  HandArrowUp,
  Plugs,
  Prohibit,
  Stop,
  UserCircle,
} from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import CitationChip from '../components/CitationChip'
import Empty from '../components/Empty'
import { dispatch, useStore } from '../store/store'
import type { ChatMessage } from '../store/types'

// AI 客服：操作者在这里预览顾客对话。
// 规则真实：回答只引用已发布（带版本号）；检索无命中拒答转人工；库存走工具调用不是 RAG；
// 会话结束「回流登记」生成已接入对话资产，不能直接已发布。
// 流式：thinking（typing 三点）→ 逐字输出（尾部光标）→ 完成/中断，可停止，刷新自动补全。

const SUGGESTIONS = [
  '饮用水的保质期是多久？',
  '保温杯的净含量多少？',
  '保温杯还有货吗？',
  '怎么退货？',
  '保温杯保温能坚持几个小时？',
  '这个水喝起来有点发甜',
]

// 连续同角色消息分组：隐藏重复头像，只留首条时间戳（Intercom 式分组）
function MessageBubble({ m, prev }: { m: ChatMessage; prev?: ChatMessage }) {
  const grouped = !!prev && prev.role === m.role && !m.streaming
  if (m.role === 'customer') {
    return (
      <div className={`flex justify-end items-end gap-2 ${grouped ? 'mt-1' : ''}`}>
        {!grouped && <span className="text-[11px] text-caption tabular-nums mb-1">{m.at.slice(11)}</span>}
        <div className="bubble-customer">{m.text}</div>
        {grouped ? (
          <div className="w-7 shrink-0" />
        ) : (
          <div className="w-7 h-7 rounded-full bg-fill-100 border border-line-2 flex items-center justify-center shrink-0">
            <UserCircle size={15} className="text-caption" />
          </div>
        )}
      </div>
    )
  }
  return (
    <div className={`flex justify-start items-end gap-2 ${grouped ? 'mt-1' : ''}`}>
      {grouped ? (
        <div className="w-7 shrink-0" />
      ) : (
        <div className="w-7 h-7 rounded-full bg-accent-soft border border-accent-border/70 flex items-center justify-center shrink-0">
          <ChatCircleDots size={15} className="text-accent-strong" />
        </div>
      )}
      <div className="space-y-2 min-w-0">
        {!grouped && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-accent-strong">AI 客服</span>
            {m.refused && (
              <span className="badge-failed">
                <Prohibit size={11} />
                拒答 · 无已发布证据
              </span>
            )}
            {m.handoff && (
              <span className="badge-review">
                <HandArrowUp size={11} />
                已转人工
              </span>
            )}
            {m.gapId && (
              <Link
                to={`/platform/assets?state=知识缺口`}
                className="badge-accent hover:underline"
                title="已记入治理台知识缺口，不是资产"
              >
                知识缺口 {m.gapId}
              </Link>
            )}
          </div>
        )}
        {/* thinking 态：typing 三点 + 检索状态行（首字前的 TTFT 间隙） */}
        {m.streaming && !m.text ? (
          <div className="bubble-agent" role="status" aria-label="AI 正在检索已发布资产">
            <div className="typing-dots">
              <span />
              <span />
              <span />
            </div>
          </div>
        ) : (
          <>
            {m.toolCall && !m.streaming && (
              // 工具调用条：库存这类事实走查询接口，与 RAG 引用视觉上分开
              <div className="tool-bar">
                <Plugs size={13} className="text-caption" />
                <span className="font-mono">{m.toolCall.name}</span>
                <span className="text-caption">|</span>
                <span>{m.toolCall.args}</span>
                <span className="text-caption">→</span>
                <span className="font-mono tabular-nums text-ink-2">{m.toolCall.result}</span>
              </div>
            )}
            <div className="bubble-agent">
              {m.text}
              {m.streaming && <span className="stream-caret" />}
              {m.interrupted && (
                <span className="ml-2 text-[11px] text-caption">（已中断）</span>
              )}
            </div>
            {m.citations && m.citations.length > 0 && !m.streaming && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-caption">引用：</span>
                {m.citations.map((c, i) => (
                  <CitationChip key={i} assetId={c.assetId} v={c.v} />
                ))}
              </div>
            )}
          </>
        )}
        {m.streaming && !m.text && (
          <div className="text-[11px] text-caption">正在检索已发布资产…</div>
        )}
      </div>
      {!grouped && <span className="text-[11px] text-caption tabular-nums mb-1">{m.at.slice(11)}</span>}
    </div>
  )
}

export default function Service() {
  const sessions = useStore((s) => s.sessions)
  const currentId = useStore((s) => s.currentSessionId)
  const activeModel = useStore((s) => s.models.find((m) => m.id === s.activeModelId) ?? s.models[0])
  const session = sessions.find((s) => s.id === currentId)
  // 无进行中会话时，展示最近一次回流登记的结果
  const lastRegistered = sessions.find((s) => s.registeredAssetId)?.registeredAssetId
  const [input, setInput] = useState('')
  const [viewingId, setViewingId] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 回看的历史会话（只读）；进行中会话直接显示
  const shown = viewingId ? sessions.find((s) => s.id === viewingId) : session

  // 流式期间是否正在输出（thinking 或逐字都算），锁发送、显示停止
  const streaming = !!session?.messages.some((m) => m.streaming)

  // 自动跟随：消息条数与已输出字符数变化都滚动到尾部
  const totalChars = shown?.messages.reduce((a, m) => a + m.text.length, 0) ?? 0
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [shown?.messages.length, totalChars])

  // 发送由 store 流式引擎接管：立即回显顾客消息，AI 先 thinking 再逐字输出
  const send = (text: string) => {
    if (!text.trim() || !session || streaming) return
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    dispatch({ type: 'SEND_MESSAGE', text })
  }

  const endedCurrent = !!session?.registeredAssetId

  return (
    <div className="p-4 lg:p-6">
      <PageHeader
        title="AI 客服"
        desc="操作者预览顾客对话。只引已发布；无证据则拒答转人工并记下知识缺口；库存走工具；结束可回流登记。"
        actions={
          <>
            <button
              className="btn-primary"
              disabled={!!session}
              onClick={() => dispatch({ type: 'NEW_SESSION' })}
            >
              <ChatCircleDots size={14} />
              新会话
            </button>
          </>
        }
      />

      {/* 会话历史：结束后仍可回看（含转人工标记），不是一关就没 */}
      {sessions.length > 0 && (
        <div className="panel mb-3 px-3 py-2 flex items-center gap-2 overflow-x-auto">
          <span className="text-xs text-caption shrink-0">历史会话</span>
          {sessions.map((s) => {
            const handoffs = s.messages.filter((m) => m.handoff).length
            const isCurrent = s.id === currentId
            const isViewing = s.id === viewingId
            return (
              <button
                key={s.id}
                className={`inline-flex items-center gap-1.5 h-7 px-2.5 text-xs rounded-full border whitespace-nowrap transition-colors ${
                  isCurrent
                    ? 'border-accent-border bg-accent-soft text-accent-strong'
                    : isViewing
                      ? 'border-line-4 bg-fill-100 text-ink'
                      : 'border-line-2 bg-base text-ink-3 hover:border-line-3'
                }`}
                onClick={() => setViewingId(isCurrent || isViewing ? null : s.id)}
              >
                <span className="font-mono tabular-nums">{s.id}</span>
                {isCurrent && <span>· 进行中</span>}
                {s.registeredAssetId && <span>· 已回流</span>}
                {handoffs > 0 && <span className="text-amber-700">· 转人工×{handoffs}</span>}
              </button>
            )
          })}
        </div>
      )}

      {!shown ? (
        <div className="panel">
          {lastRegistered ? (
            <div className="px-4 py-3 bg-emerald-50 border-b border-emerald-100 text-sm text-emerald-800 flex items-center gap-2">
              <ArrowUDownLeft size={15} />
              上一会话已回流登记为
              <Link
                to={`/platform/assets/${lastRegistered}`}
                className="font-mono underline"
              >
                {lastRegistered}
              </Link>
              （已接入，未发布，检索仍搜不到）
            </div>
          ) : null}
          <Empty
            icon={<ChatCircleDots size={28} />}
            title="没有进行中的会话"
            hint="新开一条会话，扮演顾客提问，观察引用、拒答与工具调用的差别。"
            action={
              <button className="btn-primary" onClick={() => dispatch({ type: 'NEW_SESSION' })}>
                <ChatCircleDots size={14} />
                新会话
              </button>
            }
          />
        </div>
      ) : (
        <div className="panel flex flex-col" style={{ height: 'calc(100dvh - 260px)', minHeight: 400 }}>
          <div className="px-4 py-2.5 border-b border-line-1 flex flex-wrap items-center gap-2 text-xs text-caption">
            <span className="font-mono">{shown.id}</span>
            <span>· 开始于 {shown.startedAt}</span>
            {viewingId && shown.id !== currentId && (
              <span className="badge-review">历史会话 · 只读</span>
            )}
            {shown.registeredAssetId && (
              <span>
                · 已回流
                <Link
                  to={`/platform/assets/${shown.registeredAssetId}`}
                  className="font-mono underline ml-1"
                >
                  {shown.registeredAssetId}
                </Link>
              </span>
            )}
            <span className="flex-1" />
            {viewingId ? (
              <button className="btn-ghost btn-sm" onClick={() => setViewingId(null)}>
                返回当前
              </button>
            ) : (
              <>
                <span className="inline-flex items-center gap-1" title={activeModel.provider}>
                  <GearSix size={13} />
                  {activeModel.provider} · {activeModel.name}
                </span>
                {session!.messages.length > 0 && !endedCurrent && !streaming && (
                  <button
                    className="btn-ghost btn-sm"
                    onClick={() => dispatch({ type: 'END_SESSION' })}
                    title="会话登记为已接入对话资产，进入治理队列"
                  >
                    结束并回流登记
                  </button>
                )}
              </>
            )}
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            {shown.messages.length === 0 && (
              <div className="text-sm text-caption text-center py-6">
                扮演顾客提问试试：
                <div className="mt-3 flex flex-wrap justify-center gap-1.5 max-w-lg mx-auto">
                  {SUGGESTIONS.map((q) => (
                    <button
                      key={q}
                      className="btn-ghost btn-sm"
                      onClick={() => send(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {shown.messages.map((m, i) => (
              <MessageBubble key={m.id} m={m} prev={shown.messages[i - 1]} />
            ))}
            <div ref={endRef} />
          </div>

          {!viewingId && (
            <div className="border-t border-line-1 p-3">
              {/* 输入卡：自增高 textarea + 圆形发送/停止（流式期间原位换成停止） */}
              <div className="msg-composer">
                <textarea
                  ref={taRef}
                  rows={1}
                  placeholder="输入顾客的问题…（Enter 发送，Shift+Enter 换行）"
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value)
                    e.target.style.height = 'auto'
                    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      send(input)
                    } else if (e.key === 'Escape' && streaming) {
                      dispatch({ type: 'STREAM_STOP' })
                    }
                  }}
                  disabled={endedCurrent}
                />
                {streaming ? (
                  <button
                    className="send-btn"
                    onClick={() => dispatch({ type: 'STREAM_STOP' })}
                    title="停止输出（Esc）"
                    aria-label="停止输出"
                  >
                    <Stop size={14} weight="fill" />
                  </button>
                ) : (
                  <button
                    className="send-btn"
                    onClick={() => send(input)}
                    disabled={!input.trim()}
                    title="发送（Enter）"
                    aria-label="发送"
                  >
                    <ArrowUp size={16} weight="bold" />
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
