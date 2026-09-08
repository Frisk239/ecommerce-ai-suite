// 消息气泡共享组件（0021：客服预览页与顾客页 /customer 同一视觉语言）。
// 从 ServicePage 抽出，行为不变；citationAsLink 控制引用芯片是否可跳转——
// 操作者预览默认可跳资产详情（0007 版本锚定），顾客侧只读展示（顾客不进控制台）。

import { Link } from 'react-router-dom'
import { ChatCircleDots, HandArrowUp, UserCircle } from '@phosphor-icons/react'
import type { ServiceCitation } from '../api/types'
import CitationChip from './CitationChip'
import { formatGapId, formatTime } from '../labels'

/** 展示用消息 = 服务器消息 + 本地流式追加（streaming/stopped 是前端表达，不进 API 类型）。 */
export interface UiMessage {
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
  /** 中断了订阅：已收文本保留，完整回答在后端留档。 */
  stopped: boolean
  /** thinking 事件带来的检索状态行文案（多个 thinking 事件按到达顺序覆盖，
   * 状态行随最新事件更新：检索 -> 正在生成回答…）。 */
  thinkingText: string | null
  /** 拒答时 complete 事件带回的知识缺口 id（ADR 0030：只在运行时返回，
   * 服务器消息列表不含此列——重载后芯片不重现，属契约口径）。 */
  gapId: number | null
  /** 厂商生成失败降级为证据组装模板（第 7 刀）：只在 complete 事件带回，
   * 重载后徽章不重现（同 gapId 运行时口径）。 */
  fallback: boolean
}

/** 服务器消息 -> 展示用消息（重载会话时用；live 消息由发问方直接构造）。 */
export function toUi(m: {
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
    fallback: false,
  }
}

/** 顾客侧引用芯片：同 cite-chip 视觉，不可跳转（顾客页不进操作者控制台）。 */
function CitationChipPlain({ assetId, version }: { assetId: number; version: number }) {
  const label = `A-${String(assetId).padStart(4, '0')} · v${version}`
  return (
    <span className="cite-chip" title="回答依据的已发布资料版本">
      {label}
    </span>
  )
}

// 连续同角色消息分组：隐藏重复头像，只留首条时间戳（Intercom 式分组）
export default function MessageBubble({
  m,
  prev,
  citationAsLink = true,
}: {
  m: UiMessage
  prev?: UiMessage
  citationAsLink?: boolean
}) {
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
            {m.fallback && (
              <span className="badge badge-ingested" title="厂商模型不可用，本条回答由已发布证据模板组装">
                模板回退
              </span>
            )}
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
            {m.citations.map((c) =>
              citationAsLink ? (
                <CitationChip key={`${c.asset_id}-${c.version_no}`} assetId={c.asset_id} version={c.version_no} />
              ) : (
                <CitationChipPlain key={`${c.asset_id}-${c.version_no}`} assetId={c.asset_id} version={c.version_no} />
              ),
            )}
          </div>
        )}
      </div>
      {!grouped && (
        <span className="mb-1 text-[11px] tabular-nums text-caption">{formatTime(m.created_at)}</span>
      )}
    </div>
  )
}
