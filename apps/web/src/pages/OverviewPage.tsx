// 总览页（第 23 刀）：三条故事线（接待闭环/内容闭环/连接层，冻结原型文案，链接
// 对齐工程路由）+ 统计带（资产三态/开放缺口/会话数，复用既有只读端点）。
// 计数口径与治理台列表同源：待人洗=纯新待办（无已发布指针）、已发布=指针非空
// （修订中的资产线上版仍在服务）。图标块全中性（DSH：色彩只承担状态语义）。
// 第 27 刀补「其余能力」四行入口（对照原型 Overview.tsx:177 冻结形状：
// 资产/商品/考核/连接层，路由换工程路由）。

import { Fragment, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ChatCircleDots,
  ImageSquare,
  PlugsConnected,
} from '@phosphor-icons/react'
import type { Icon } from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import type { AssetListItem, StatsCsat, StatsFeedbackAsset, StatsOverview } from '../api/types'
import { formatAssetId } from '../labels'
import { filterWorkAssets, isIngested, isPendingWash, isPublished } from '../workQueue'
import { useApiData } from '../hooks/useApiData'
import { useStats } from '../stats/StatsContext'
import { ErrorBanner } from '../components/Banner'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'

interface LoopDef {
  name: string
  to: string
  icon: Icon
  line: string
  steps: { label: string; to: string }[]
}

// 对照原型 Overview.tsx 冻结文案；state= → status=（治理台 tab 口径）、/materials → /material
const LOOPS: LoopDef[] = [
  {
    name: '接待闭环',
    to: '/service',
    icon: ChatCircleDots,
    line: '客服只引已发布；无证据拒答；会话可回流再治理。知识变好靠发布，不靠训练。',
    steps: [
      { label: '治理资产', to: '/platform/assets?status=待人洗' },
      { label: '带引用回答', to: '/service' },
      { label: '回流登记', to: '/service' },
      { label: '再治理发布', to: '/platform/assets?status=已接入' },
    ],
  },
  {
    name: '内容闭环',
    to: '/material',
    icon: ImageSquare,
    line: '商品知识生成素材、直播切片汇入素材中心，登记进中台后由运营 Agent 组装投放。',
    steps: [
      { label: '素材生成', to: '/material' },
      { label: '切片汇入', to: '/clips' },
      { label: '登记治理', to: '/platform/assets?status=已接入' },
      { label: '运营投放', to: '/ops' },
    ],
  },
  {
    name: '连接层',
    to: '/connect',
    icon: PlugsConnected,
    line: '外部 Agent 经 MCP 检索、取资产、登记同一份已发布权威。导出是数据包，不是微调集。',
    steps: [
      { label: '检索已发布', to: '/connect' },
      { label: '取资产版本', to: '/connect' },
      { label: '登记进中台', to: '/platform/assets?status=已接入' },
    ],
  },
]

// 其余能力：一行的入口，不是营销磁贴（原型 Overview.tsx:177 冻结形状与文案）
const REMAINING: { to: string; label: string; line: string }[] = [
  {
    to: '/platform/assets',
    label: '中台 · 资产',
    line: '登记、机洗、人洗、发布、修订；发布权只在这里',
  },
  {
    to: '/platform/products',
    label: '中台 · 商品',
    line: '结构化事实；规格来自已发布资产的写回',
  },
  { to: '/coach', label: '销售考核', line: '从已发布对话资产抽场景，按维打分（题面即开场）' },
  {
    to: '/connect',
    label: '连接层（MCP）',
    line: '把中台能力暴露给外部 Agent：检索、取资产、登记、导出，没有发布',
  },
]

const EMPTY_ASSETS: AssetListItem[] = []

/** 柱高：三序列共用同一纵轴刻度（跨序列可比），按窗口内全局最大值归一；
 * 非零最小值 3px，避免小值被 0 高柱淹没。 */
function trendHeight(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0
  return Math.max(3, Math.round((value / max) * 56))
}

/** 7 日趋势条（第 43 刀）：一条横排、每日一组 CSS 柱，不引图表库，不是磁贴网格。 */
function TrendPanel({ data }: { data: StatsOverview }) {
  const max = Math.max(
    0,
    ...data.daily.flatMap((d) => [d.sessions, d.refusals, d.thumbs_down]),
  )
  const rate = data.citation_rate_last_7d
  const legend: { label: string; color: string }[] = [
    { label: '会话', color: 'var(--color-ink-3)' },
    { label: '拒答', color: 'var(--color-warn)' },
    { label: '被踩', color: 'var(--color-danger)' },
  ]
  return (
    <div className="panel mb-4">
      <div className="panel-title flex-wrap">
        <span>近 {data.window_days} 日</span>
        <span className="text-xs font-normal text-ink-3">
          会话 {data.sessions_last_7d} · 拒答 {data.refusals_last_7d} · 转人工{' '}
          {data.handoffs_last_7d} · 被踩 {data.thumbs_down_last_7d}
        </span>
        <span className="flex-1" />
        <span
          className="text-xs font-normal text-ink-3"
          title="有引用的回答 / 全部回答（模板与工具回答也是回答）"
        >
          引用覆盖率 {rate === null ? '—' : `${Math.round(rate * 100)}%`}（
          {data.answers_with_citations_last_7d}/{data.answers_last_7d}）
        </span>
      </div>
      <div className="flex items-end gap-2 px-4 pb-3 pt-4">
        {data.daily.map((day) => (
          <div key={day.date} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
            <div className="flex h-14 w-full items-end justify-center gap-[3px]">
              <span
                className="trend-bar trend-bar-sessions"
                style={{ height: trendHeight(day.sessions, max) }}
                title={`${day.date} 会话 ${day.sessions}`}
              />
              <span
                className="trend-bar trend-bar-refusals"
                style={{ height: trendHeight(day.refusals, max) }}
                title={`${day.date} 拒答 ${day.refusals}`}
              />
              <span
                className="trend-bar trend-bar-thumbs"
                style={{ height: trendHeight(day.thumbs_down, max) }}
                title={`${day.date} 被踩 ${day.thumbs_down}`}
              />
            </div>
            <span className="text-[11px] tabular-nums text-caption">{day.date.slice(5)}</span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-1 px-4 py-2 text-[11px] leading-5 text-ink-3">
        {legend.map((l) => (
          <span key={l.label} className="flex items-center gap-1.5">
            <span className="trend-legend-dot" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
        <span className="flex-1" />
        {/* 口径硬标注：分母是「回答」，不是 RAG 准确率（Owner 裁决 2） */}
        <span>引用覆盖率分母是「回答」（含模板/工具回答与 citations 为空的回答），不是 RAG 准确率；拒答按定义无引用，不混进分母。</span>
      </div>
    </div>
  )
}

/** CSAT 行（第 48 刀）：近 7 日会话评分。均分与分布同屏——4.3 可能是
 * 5+5+3，只看均值会把「有人很不满意」读成「总体还行」；无样本时均分是「—」
 * （后端给 null，不返回 0 冒充）；留言已在后端掩码 + 截断。 */
function CsatPanel({ csat }: { csat: StatsCsat }) {
  const total = csat.ratings_last_7d
  const max = Math.max(1, ...Object.values(csat.distribution))
  return (
    <div className="panel mt-4">
      <div className="panel-title flex-wrap">
        <span>顾客满意度 · 近 7 日</span>
        <span className="text-xs font-normal text-ink-3">
          {total === 0
            ? '还没有评分'
            : `均分 ${csat.average_last_7d === null ? '—' : csat.average_last_7d} / 5 · ${total} 条评分（会话级，与「没有帮助」正交）`}
        </span>
      </div>
      {total === 0 ? (
        <div className="px-4 py-3.5 text-[13px] leading-6 text-ink-3">
          顾客在客服页打完分后会汇总在这里；低于 3 分不自动触发任何治理动作（那要走「没有帮助」）。
        </div>
      ) : (
        <>
          <div className="space-y-1 px-4 py-3">
            {[5, 4, 3, 2, 1].map((score) => {
              const count = csat.distribution[String(score)] ?? 0
              return (
                <div key={score} className="flex items-center gap-2.5">
                  <span className="w-6 shrink-0 text-right text-xs tabular-nums text-ink-3">
                    {score} 星
                  </span>
                  <span className="h-2.5 flex-1 overflow-hidden rounded-[2px] bg-fill">
                    <span
                      className="block h-full rounded-[2px]"
                      style={{
                        width: `${Math.round((count / max) * 100)}%`,
                        background: score >= 4 ? 'var(--color-ink-2)' : 'var(--color-danger)',
                      }}
                    />
                  </span>
                  <span className="w-8 shrink-0 text-xs tabular-nums text-ink-2">{count}</span>
                </div>
              )
            })}
          </div>
          {csat.recent_comments.length > 0 && (
            <div className="border-t border-line-1 px-4 py-2.5">
              <div className="text-[11px] text-caption">最新留言（已掩码）</div>
              <ul className="mt-1 space-y-0.5">
                {csat.recent_comments.map((text, index) => (
                  <li key={`${index}-${text.slice(0, 8)}`} className="text-[12.5px] text-ink-2">
                    「{text}」
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** 反馈汇总行（第 43 刀）：被踩最多的资产 top 3，每条带「去重新验证」深链。
 * 行/条形态（与摘要条同一语言），不是磁贴网格。 */
function FeedbackPanel({ assets }: { assets: StatsFeedbackAsset[] }) {
  return (
    <div className="panel mt-4">
      <div className="panel-title flex-wrap">
        {/* 口径显式「全时段」：与同屏「近 7 日」趋势条区分（Owner 裁决） */}
        <span>被踩最多的资产 · 全时段</span>
        <span className="text-xs font-normal text-ink-3">
          顾客「没有帮助」反馈逐引用资产分诊；点「去重新验证」到详情页定位，不自动执行
        </span>
      </div>
      {assets.length === 0 ? (
        <div className="px-4 py-3.5 text-[13px] leading-6 text-ink-3">
          还没有被「没有帮助」反馈过的资产。反馈发生后，被引用的资产会在这里汇总。
        </div>
      ) : (
        assets.map((a) => (
          <div
            key={a.asset_id}
            className="flex items-center gap-3 border-b border-line-1 px-4 py-2.5 last:border-b-0"
          >
            <span className="shrink-0 font-mono text-xs text-ink-3">
              {formatAssetId(a.asset_id)}
            </span>
            <span
              className="min-w-0 flex-1 truncate text-[13px] text-ink"
              title={a.title ?? undefined}
            >
              {a.title ?? '（资产已不存在）'}
            </span>
            <span className="shrink-0 text-xs tabular-nums text-ink-2">被踩 {a.count} 次</span>
            <Link
              to={`/platform/assets/${a.asset_id}?verify=1`}
              className="shrink-0 text-xs text-accent transition-colors duration-150 hover:text-accent-strong hover:underline"
            >
              去重新验证
            </Link>
          </div>
        ))
      )}
    </div>
  )
}

export default function OverviewPage() {
  const fetcher = useCallback(
    () =>
      Promise.all([
        api.listAssets(),
        api.listKnowledgeGaps('open'),
        api.listServiceSessions(),
      ]),
    [],
  )
  const { state, reload } = useApiData(fetcher)

  // 第 43 刀：仪表由 AppShell 取一次并经 context 下发（角标与总览同源，避免同一
  // 路由每次导航发两次 /stats/overview）；刷新语义仍在 AppShell（pathname 作 key）
  const { state: statsState, reload: reloadStats } = useStats()

  const assets = state.phase === 'ok' ? state.data[0] : EMPTY_ASSETS
  const openGaps = state.phase === 'ok' ? state.data[1].length : null
  const sessions = state.phase === 'ok' ? state.data[2].length : null

  // 与治理台列表同口径：先按工作队列谓词（workQueue 单一来源）收窄，再分状态计数
  // ——列表默认 view=work，总览必须同一套数字（§二点十 冻结原则）。
  const workAssets = useMemo(() => filterWorkAssets(assets), [assets])
  const counts = useMemo(
    () => ({
      pending_review: workAssets.filter(isPendingWash).length,
      ingested: workAssets.filter(isIngested).length,
      published: workAssets.filter(isPublished).length,
    }),
    [workAssets],
  )

  // 一条摘要条：数字 + 标签，每个数字仍可点进对应列表（hint 只作 title 兜底，不上界面）。
  const stats: { label: string; value: number | null; hint: string; to: string }[] = [
    {
      label: '待人洗',
      value: counts.pending_review,
      hint: '纯新待办：机洗值等确认、缺项等补填',
      to: '/platform/assets?status=待人洗',
    },
    {
      label: '已接入',
      value: counts.ingested,
      hint: '刚登记或机洗失败，等待进治理流水线',
      to: '/platform/assets?status=已接入',
    },
    {
      label: '已发布',
      value: counts.published,
      hint: '可被 Agent 检索；修订中的资产线上版仍在服务',
      to: '/platform/assets?status=已发布',
    },
    {
      label: '知识缺口',
      value: openGaps,
      hint: '随拒答产生，解决只随发布发生',
      to: '/platform/assets?status=知识缺口',
    },
    {
      label: '客服会话',
      value: sessions,
      hint: '操作者预览与顾客通道共用同一引擎',
      to: '/service',
    },
  ]

  return (
    <div>
      <PageHeader
        title="总览"
        desc="七块能力共用一份被治理过的数据；知识变好靠发布，不靠训练。"
      />

      {state.phase === 'error' ? <ErrorBanner error={state.error} onRetry={reload} /> : null}
      {state.phase === 'loading' ? (
        <div className="panel mb-4">
          <SkeletonRows rows={2} />
        </div>
      ) : (
        /* 一条可点摘要条：数字 + 标签横排，不是磁贴网格（§三 列表页=分段+密度表） */
        <div
          className="panel mb-4 flex flex-wrap items-center gap-x-3.5 gap-y-2 px-4 py-3"
          aria-label="关键数字"
        >
          {stats.map((st, i) => (
            <Fragment key={st.label}>
              {i > 0 ? <span className="h-4 w-px shrink-0 bg-line-2" aria-hidden /> : null}
              <Link
                to={st.to}
                className="group flex items-baseline gap-1.5"
                title={st.hint}
              >
                <span className="text-lg font-semibold tabular-nums leading-6 text-ink group-hover:text-accent-strong">
                  {st.value ?? '—'}
                </span>
                <span className="text-[13px] text-ink-3 group-hover:text-ink">{st.label}</span>
              </Link>
            </Fragment>
          ))}
        </div>
      )}

      {/* 第 43 刀：7 日趋势条 + 反馈汇总行（摘要条之后、能力闭环之前；行/条形态）。
          仪表取数失败不静默：给一行错误提示 + 重试（复用 ErrorBanner，不新造组件）。 */}
      {statsState.phase === 'loading' ? (
        <div className="panel mb-4">
          <SkeletonRows rows={1} />
        </div>
      ) : statsState.phase === 'error' ? (
        <ErrorBanner error={statsState.error} onRetry={reloadStats} />
      ) : (
        <>
          <TrendPanel data={statsState.data} />
          <FeedbackPanel assets={statsState.data.feedback_assets} />
          <CsatPanel csat={statsState.data.csat} />
        </>
      )}

      {/* 三条故事线：步进器（每个节点是真路由），不是营销磁贴；主区域点击跳首步页 */}
      <div className="panel divide-y divide-line-1">
        {LOOPS.map((loop) => (
          <div key={loop.name} className="flex flex-col gap-3 px-4 py-4 md:flex-row md:items-center md:gap-5">
            <Link
              to={loop.to}
              className="group flex shrink-0 items-center gap-2.5 md:w-44"
              title={`进入${loop.name}`}
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[6px] border border-line-2 bg-fill text-ink-2 group-hover:text-ink">
                <loop.icon aria-hidden size={17} />
              </div>
              <span className="text-[15px] font-semibold tracking-tight text-ink group-hover:text-accent-strong">
                {loop.name}
              </span>
            </Link>
            <p className="hidden shrink-0 text-[13px] leading-6 text-ink-3 md:block md:w-72">
              {loop.line}
            </p>
            <p className="text-xs leading-5 text-ink-3 md:hidden">{loop.line}</p>
            {/* 单行不折：步骤再多也横向滚动，不把第 4 步掉到第二行 */}
            <div className="flex min-w-0 flex-1 flex-nowrap items-center overflow-x-auto">
              {loop.steps.map((st, i) => (
                <div key={`${loop.name}-${st.label}`} className="flex shrink-0 items-center">
                  {i > 0 ? <div className="mx-0.5 h-px w-5 shrink-0 bg-line-3" /> : null}
                  <Link
                    to={st.to}
                    className="group/step flex shrink-0 items-center gap-1.5 rounded-[6px] px-1.5 py-1 hover:bg-fill"
                  >
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-fill font-mono text-[11px] tabular-nums text-ink-3 group-hover/step:bg-accent group-hover/step:text-white">
                      {i + 1}
                    </span>
                    <span className="text-[13px] whitespace-nowrap text-ink-2 group-hover/step:text-ink">
                      {st.label}
                    </span>
                  </Link>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* 其余能力：故事线之外的一行入口（第 27 刀，对照原型冻结四行） */}
      <div className="panel mt-4 divide-y divide-line-1">
        {REMAINING.map((row) => (
          <Link
            key={row.to}
            to={row.to}
            className="group flex items-center gap-3 px-4 py-3 hover:bg-fill"
          >
            <span className="text-sm font-medium text-ink group-hover:text-accent-strong">
              {row.label}
            </span>
            <span className="truncate text-xs text-ink-3">{row.line}</span>
            <ArrowRight
              aria-hidden
              size={14}
              className="ml-auto shrink-0 text-caption hover:text-accent-strong"
            />
          </Link>
        ))}
      </div>
    </div>
  )
}
