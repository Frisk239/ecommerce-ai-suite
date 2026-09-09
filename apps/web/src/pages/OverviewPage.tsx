// 总览页（第 23 刀）：三条故事线（接待闭环/内容闭环/连接层，冻结原型文案，链接
// 对齐工程路由）+ 统计带（资产三态/开放缺口/会话数，复用既有只读端点）。
// 计数口径与治理台列表同源：待人洗=纯新待办（无已发布指针）、已发布=指针非空
// （修订中的资产线上版仍在服务）。图标块全中性（DSH：色彩只承担状态语义）。
// 第 27 刀补「其余能力」四行入口（对照原型 Overview.tsx:177 冻结形状：
// 资产/商品/考核/连接层，路由换工程路由）。

import { useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ChatCircleDots,
  CheckCircle,
  Database,
  ImageSquare,
  PlugsConnected,
  Tray,
  Warning,
} from '@phosphor-icons/react'
import type { Icon } from '@phosphor-icons/react'
import { api } from '../api/endpoints'
import type { AssetListItem } from '../api/types'
import { useApiData } from '../hooks/useApiData'
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

  const assets = state.phase === 'ok' ? state.data[0] : EMPTY_ASSETS
  const openGaps = state.phase === 'ok' ? state.data[1].length : null
  const sessions = state.phase === 'ok' ? state.data[2].length : null

  // 与治理台列表 tab 同口径（AssetsListPage counts 同源逻辑，不另发请求）
  const counts = useMemo(
    () => ({
      pending_review: assets.filter(
        (a) => a.status === 'pending_review' && a.current_published_version_no === null,
      ).length,
      ingested: assets.filter((a) => a.status === 'ingested').length,
      published: assets.filter((a) => a.current_published_version_no !== null).length,
    }),
    [assets],
  )

  const stats: {
    label: string
    value: number | null
    hint: string
    to: string
    icon: Icon
  }[] = [
    {
      label: '待人洗队列',
      value: counts.pending_review,
      hint: '纯新待办：机洗值等确认、缺项等补填',
      to: '/platform/assets?status=待人洗',
      icon: Tray,
    },
    {
      label: '已接入',
      value: counts.ingested,
      hint: '刚登记或机洗失败，等待进治理流水线',
      to: '/platform/assets?status=已接入',
      icon: Database,
    },
    {
      label: '已发布 · 可被 Agent 检索',
      value: counts.published,
      hint: '按可检索口径计数：修订中的资产线上版仍在服务',
      to: '/platform/assets?status=已发布',
      icon: CheckCircle,
    },
    {
      label: '开放知识缺口',
      value: openGaps,
      hint: '随拒答产生，解决只随发布发生',
      to: '/platform/assets?status=知识缺口',
      icon: Warning,
    },
    {
      label: '客服会话',
      value: sessions,
      hint: '操作者预览与顾客通道共用同一引擎',
      to: '/service',
      icon: ChatCircleDots,
    },
  ]

  return (
    <div>
      <PageHeader
        title="总览"
        desc="七块能力共用一份被治理过的数据。知识变好靠缺口和发布，不靠训练。治理队列是每天的入口。"
      />

      {state.phase === 'error' ? <ErrorBanner error={state.error} onRetry={reload} /> : null}
      {state.phase === 'loading' ? (
        <div className="panel mb-4">
          <SkeletonRows rows={2} />
        </div>
      ) : (
        <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-5">
          {stats.map((st) => (
            <Link key={st.label} to={st.to} className="stat-card">
              <div className="flex items-start justify-between gap-2">
                <div className="stat-label">{st.label}</div>
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] border border-line-2 bg-fill text-ink-3">
                  <st.icon aria-hidden size={15} />
                </div>
              </div>
              <div className="stat-value">{st.value ?? '—'}</div>
              <div className="stat-hint">{st.hint}</div>
            </Link>
          ))}
        </div>
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
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-y-2">
              {loop.steps.map((st, i) => (
                <div key={`${loop.name}-${st.label}`} className="flex items-center">
                  {i > 0 ? <div className="mx-0.5 h-px w-5 bg-line-3" /> : null}
                  <Link
                    to={st.to}
                    className="group/step flex items-center gap-1.5 rounded-[6px] px-1.5 py-1 hover:bg-fill"
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
