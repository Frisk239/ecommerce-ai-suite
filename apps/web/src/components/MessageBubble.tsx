// 消息气泡共享组件（0021：客服预览页与顾客页 /customer 同一视觉语言）。
// 从 ServicePage 抽出，行为不变；citationAsLink 控制引用芯片是否可跳转——
// 操作者预览默认可跳资产详情（0007 版本锚定），顾客侧只读展示（顾客不进控制台）。

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ChatCircleDots,
  FileText,
  HandArrowUp,
  Prohibit,
  Sparkle,
  UserCircle,
  Wrench,
} from '@phosphor-icons/react'
import type { MediaCitation, ServiceAnswerPath, ServiceCitation, ToolCallRecord } from '../api/types'
import { mediaUrl } from '../api/client'
import CitationChip from './CitationChip'
import { formatAssetId, formatGapId, formatTime } from '../labels'

/** 展示用消息 = 服务器消息 + 本地流式追加（streaming/stopped 是前端表达，不进 API 类型）。 */
export interface UiMessage {
  key: string
  id: number | null
  role: 'customer' | 'agent'
  content: string
  citations: ServiceCitation[] | null
  kind: 'answer' | 'refusal' | 'handoff' | null
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
  /** 回答路径标识（第 108B 刀 W2）：complete 带回的 tool/template/llm/refusal
   * ——气泡角落的来源标签，让「什么时候没调大模型」一眼可见。同 fallback/
   * gapId 运行时口径：消息表不加列，重载后标签不重现（重载的存量消息为 null）。 */
  path: ServiceAnswerPath | null
  /** 订单工具调用记录（第 13 刀/ADR 0036）：与 fallback 相反——随消息落库，
   * 重载后灰底 mono 工具条照样还原（回放完整性）。 */
  tool: ToolCallRecord | null
  /** 第 94b 刀（ADR 0052）：媒体附件 [{asset_id, version_no, mime}]——随消息
   * 落库（重载照样出图/出播放器）；空数组/ null = 本条无媒体。 */
  mediaCitations: MediaCitation[] | null
  /** 第 42 刀（ADR 0046）：本条 handoff/拒答消息的工单 id（complete 带回的
   * 运行时可选键）。重载后服务器消息不带（工单详情在会话详情 ticket 上）；
   * 顾客页据此渲染联系方式表单。H 号只在回执正文里，不单独携带。 */
  ticketId: number | null
  /** 联系方式提交时间（NULL=还没留）：重载/恢复后由后端 contact_at 决定表单态。 */
  ticketContactAt: string | null
}

/** UiMessage 构造单点（第 25 刀收口）：全部字段默认值集中在这里（计数不再写死
 * ——每刀加字段是常态），调用方只给差异字段——服务器消息回填（toUi）与本地
 * 流式占位（useAskStream）共用。 */
export function toUiMessage(
  init: Pick<UiMessage, 'key' | 'role' | 'content' | 'created_at'> &
    Partial<Omit<UiMessage, 'key' | 'role' | 'content' | 'created_at'>>,
): UiMessage {
  return {
    id: null,
    citations: null,
    kind: null,
    handoff: false,
    streaming: false,
    stopped: false,
    thinkingText: null,
    gapId: null,
    fallback: false,
    path: null,
    tool: null,
    mediaCitations: null,
    ticketId: null,
    ticketContactAt: null,
    ...init,
  }
}

/** 服务器消息 -> 展示用消息（重载会话时用；live 消息由 useAskStream 构造）。 */
export function toUi(m: {
  id: number
  role: 'customer' | 'agent'
  content: string
  citations: ServiceCitation[] | null
  kind: 'answer' | 'refusal' | 'handoff' | null
  handoff: boolean | null
  created_at: string
  tool?: ToolCallRecord | null
  /** 第 94b 刀：随消息落库的媒体附件（旧消息/存量行可能没有该键）。 */
  media_citations?: MediaCitation[] | null
}): UiMessage {
  return toUiMessage({
    key: `s-${m.id}`,
    id: m.id,
    role: m.role,
    content: m.content,
    citations: m.citations,
    kind: m.kind,
    handoff: m.handoff ?? false,
    created_at: m.created_at,
    tool: m.tool ?? null,
    mediaCitations: m.media_citations ?? null,
  })
}

/** 顾客侧引用芯片：中性只读变体（UX-B），不可跳转也不像链接——顾客页不进
 * 操作者控制台；保留 A-xxxx · vN 文本。操作者侧 CitationChip 仍是可点蓝链。 */
function CitationChipPlain({ assetId, version }: { assetId: number; version: number }) {
  return (
    <span className="cite-chip-readonly" title="回答依据的已发布资料版本（只读）">
      {formatAssetId(assetId)} · v{version}
    </span>
  )
}

/** 工具条（第 13 刀/ADR 0036，UX-NOTES §6 冻结口径）：灰底 mono「参数→结果」，
 * 与青底引用芯片视觉分离——引用=证据指向，工具条=已发生的动作留档。 */
function ToolStrip({ tool }: { tool: ToolCallRecord }) {
  return (
    <div className="tool-strip" title="引擎调用工具的真实动作记录（只读工具，不是证据引用）">
      <span className="tool-call">
        {tool.name}({tool.arg})
      </span>
      <span className="tool-arrow" aria-hidden>
        →
      </span>
      <span className="tool-result">{tool.result}</span>
    </div>
  )
}

/** 回答路径标签（第 108B 刀 W2）：气泡右下角一枚小 chip，四态各有语义色
 * （灰=工具 / 蓝=模板 / 紫=AI / 红=拒答）。图标用 Phosphor（与页面图标语言
 * 一致，不引 emoji）；标签是「来路」不是证据，故不做链接也不可点。 */
const PATH_CHIP_META: Record<
  ServiceAnswerPath,
  { label: string; title: string; className: string; Icon: typeof Wrench }
> = {
  tool: {
    label: '工具',
    title: '确定性路径：引擎直接查库/取事实（订单/库存/目录/会话回声），未调用大模型',
    className: 'path-chip-tool',
    Icon: Wrench,
  },
  template: {
    label: '模板',
    title: '模板组装：固定句式拼装（证据组装/转人工回执），未调用大模型',
    className: 'path-chip-template',
    Icon: FileText,
  },
  llm: {
    label: 'AI',
    title: 'AI 生成：检索证据 + 大模型生成（引用仍由服务端定）',
    className: 'path-chip-llm',
    Icon: Sparkle,
  },
  refusal: {
    label: '拒答',
    title: '拒答：没有查到可靠资料，不编造——已转人工',
    className: 'path-chip-refusal',
    Icon: Prohibit,
  },
}

function PathChip({ path }: { path: ServiceAnswerPath }) {
  const { label, title, className, Icon } = PATH_CHIP_META[path]
  return (
    <span className={`path-chip ${className}`} title={title}>
      <Icon aria-hidden size={11} />
      {label}
    </span>
  )
}

/** 媒体附件区（第 94b 刀，ADR 0052）：图片直出 <img>（限宽圆角）、视频
 * <video controls preload="metadata">（点开才拉字节，首帧不预载整段）。
 *
 * 与引用芯片区**并列**（两个独立区块）：芯片是「依据哪份资料的哪一版」，附件
 * 是「那份资料里的图/视频本身」。字节走 `mediaUrl`——顾客通道带 query 令牌
 * （src 带不了 Authorization 头），操作者页不带（同源 cookie）。
 * 媒体端点只出当前已发布指针版（未发布/已废弃 404），故一条旧消息里的附件在
 * 资产被下架后自然显示为裂图/不可播——这是已发布口径的如实表达。 */
function MediaAttachments({
  items,
  token,
}: {
  items: MediaCitation[]
  token?: string | null
}) {
  return (
    <div className="flex flex-wrap items-start gap-2">
      <span className="mt-1 text-xs text-caption">附件：</span>
      {items.map((c) =>
        c.mime.startsWith('video/') ? (
          <video
            key={`${c.asset_id}-${c.version_no}`}
            controls
            preload="metadata"
            className="max-h-[260px] w-full max-w-[320px] rounded-[6px] border border-line-2 bg-black"
            src={mediaUrl(c.asset_id, token)}
            title={`回答附带的视频（${formatAssetId(c.asset_id)} · v${c.version_no}）`}
          />
        ) : (
          <img
            key={`${c.asset_id}-${c.version_no}`}
            src={mediaUrl(c.asset_id, token)}
            alt={`回答附带的图片（${formatAssetId(c.asset_id)} · v${c.version_no}）`}
            loading="lazy"
            className="max-h-[260px] max-w-[240px] rounded-[6px] border border-line-2 bg-white object-contain"
          />
        ),
      )}
    </div>
  )
}

// 连续同角色消息分组：隐藏重复头像，只留首条时间戳（Intercom 式分组）
export default function MessageBubble({
  m,
  prev,
  citationAsLink = true,
  mediaToken = null,
  footer,
}: {
  m: UiMessage
  prev?: UiMessage
  citationAsLink?: boolean
  /** 第 94b 刀：媒体附件的顾客会话令牌（顾客页传；操作者页不传——同源 cookie
   * 自动带）。仅用于拼媒体 URL 的 `?token=`，不进任何请求头。 */
  mediaToken?: string | null
  /** 消息下沿的可选动作区（第 40 刀）：顾客页「没有帮助」/客服页「确认退货」
   * 由各页面按消息条件构造后传入，共享组件不感知业务规则。 */
  footer?: ReactNode
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
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line-2 bg-white shadow-sm">
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
            {m.kind === 'refusal' && (
              <span
                className="badge badge-failed"
                title="本条按拒答处理——知识无证据/商品资料待补/本店无此商品，详见消息正文"
              >
                拒答 · 已转人工
              </span>
            )}
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
            {/* 第 108B 刀（W2）：回答路径标签——气泡右下角（角落在气泡内，
                不占正文宽度；只读、不进引用芯片区） */}
            {m.path !== null && !m.streaming && (
              <div className="mt-1.5 flex justify-end">
                <PathChip path={m.path} />
              </div>
            )}
          </div>
        )}
        {m.streaming && m.content === '' && (
          <div className="text-[11px] text-caption">{m.thinkingText ?? '正在检索已发布资产…'}</div>
        )}
        {m.tool !== null && !m.streaming && <ToolStrip tool={m.tool} />}
        {m.mediaCitations !== null && m.mediaCitations.length > 0 && !m.streaming && (
          <MediaAttachments items={m.mediaCitations} token={mediaToken} />
        )}
        {footer !== undefined && !m.streaming && (
          <div className="flex flex-wrap items-center gap-2">{footer}</div>
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
