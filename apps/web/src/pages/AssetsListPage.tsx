// 治理台资产列表：五分段筛选（全部/已接入/待人洗/已发布/知识缺口）+ 高密度表格
// + 登记入口（可从缺口预填）。
// 口径：资产一次拉全量在客户端过滤（数据量小，各 tab 计数顺手同源，不另发请求）；
// 知识缺口不是资产——单独走 /knowledge-gaps，open 与 resolved 各拉一次合并展示。
// 当前 tab 同步进 ?status=（刷新保持；客服拒答芯片跳 ?status=知识缺口）。

import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowsClockwise, CaretRight, Plus, Warning } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { AssetStatus, KnowledgeGap } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import {
  formatDateTime,
  formatAssetId,
  formatGapId,
  isStale,
  sourceKindLabel,
} from '../labels'
import { ErrorBanner } from '../components/Banner'
import ActionError from '../components/ActionError'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { GapStatusBadge, KindChip, StatusBadge } from '../components/StateBadge'
import RegisterAssetDrawer from './RegisterAssetDrawer'

// tab 值即 ?status= 的取值（与拒答芯片的跳转链接 /platform/assets?status=知识缺口 对齐）
const TABS = ['全部', '已接入', '待人洗', '已发布', '知识缺口'] as const
type ListTab = (typeof TABS)[number]

const TAB_TO_STATUS: Record<Exclude<ListTab, '全部' | '知识缺口'>, AssetStatus> = {
  已接入: 'ingested',
  待人洗: 'pending_review',
  已发布: 'published',
}

function isListTab(value: string | null): value is ListTab {
  return value !== null && (TABS as readonly string[]).includes(value)
}

const EMPTY_ASSETS = [] as const

// 空态文案按 tab 区分：全部=中台还没有任何内容；其余=该状态下的引导。
const EMPTY_HINTS: Record<Exclude<ListTab, '知识缺口'>, string> = {
  全部: '还没有任何资产。右上「登记资产」直接录入，或由客服会话回流汇入。',
  已接入:
    '新登记的内容会先进已接入：机洗成功直接进入待人洗，只有机洗失败（或尚未运行）才会留在这里等待重试。',
  待人洗:
    '登记文档并挂上商品后，机洗按商品规格字段抽取；抽到的值待确认、没抽到的待补填，都会在这里等操作者处理。',
  已发布: '待人洗的资产补齐必填字段并确认机洗值后即可发布；发布后写回商品规格，成为线上口径。',
}

export default function AssetsListPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const fetcher = useCallback(() => api.listAssets(), [])
  const { state, reload } = useApiData(fetcher)
  const assets = state.phase === 'ok' ? state.data : EMPTY_ASSETS

  // 缺口：open 给 tab 计数与待补列表，resolved 给「已由 A-xxxx 解决」回链，合并倒序展示
  const gapsFetcher = useCallback(
    () => Promise.all([api.listKnowledgeGaps('open'), api.listKnowledgeGaps('resolved')]),
    [],
  )
  const gapsQ = useApiData(gapsFetcher)
  const gaps = useMemo(() => {
    if (gapsQ.state.phase !== 'ok') return []
    // 第 39 刀：后端已按 hit_count DESC 返回，这里只做合并后的同热度稳定序
    // （新者先）——热度列与「最热待补置顶」都由后端口径保证。
    return [...gapsQ.state.data[0], ...gapsQ.state.data[1]].sort(
      (a, b) => b.hit_count - a.hit_count || b.id - a.id,
    )
  }, [gapsQ.state])
  const openGapCount = gapsQ.state.phase === 'ok' ? gapsQ.state.data[0].length : null

  // tab 由 ?status= 驱动（无参数/非法值回落「全部」），切换即写回 URL
  const statusParam = searchParams.get('status')
  const activeTab: ListTab = isListTab(statusParam) ? statusParam : '全部'
  const setTab = (tab: ListTab) => setSearchParams(tab === '全部' ? {} : { status: tab })

  // 登记抽屉：普通入口与「去补文档」共用，gap 存在时预填（key 切换保证预填干净落地）
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerGap, setDrawerGap] = useState<KnowledgeGap | null>(null)
  const openDrawer = (gap: KnowledgeGap | null) => {
    setDrawerGap(gap)
    setDrawerOpen(true)
  }
  const closeDrawer = () => {
    setDrawerOpen(false)
    setDrawerGap(null)
  }

  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const counts = useMemo(() => {
    return {
      ingested: assets.filter((a) => a.status === 'ingested').length,
      pending_review: assets.filter(
        (a) => a.status === 'pending_review' && a.current_published_version_no === null,
      ).length,
      published: assets.filter((a) => a.current_published_version_no !== null).length,
    }
  }, [assets])

  // 「全部」= 不筛状态；已发布=指针非空（含修订中）；待人洗=纯新待办（无指针）
  const filtered = useMemo(() => {
    if (activeTab === '全部' || activeTab === '知识缺口') return assets
    if (activeTab === '已发布') return assets.filter((a) => a.current_published_version_no !== null)
    if (activeTab === '待人洗') {
      return assets.filter((a) => a.status === 'pending_review' && a.current_published_version_no === null)
    }
    return assets.filter((a) => a.status === TAB_TO_STATUS[activeTab])
  }, [assets, activeTab])

  const fillGap = (gap: KnowledgeGap) => {
    if (gap.product !== null) {
      const spec = assets.find(
        (a) =>
          a.kind === 'document' &&
          a.product?.id === gap.product?.id &&
          a.current_published_version_no !== null,
      )
      if (spec !== undefined) {
        void (async () => {
          setActionError(null)
          try {
            const updated = await api.openRevision(spec.id, gap.id)
            navigate(`/platform/assets/${updated.id}`)
          } catch (err) {
            setActionError(`开修订失败：${detailText(err)}`)
          }
        })()
        return
      }
    }
    openDrawer(gap)
  }

  const retryWash = async (assetId: number) => {
    if (retryingId !== null) return
    setRetryingId(assetId)
    setActionError(null)
    try {
      await api.retryMachineWash(assetId)
      reload()
    } catch (err) {
      setActionError(`重试机洗失败：${detailText(err)}`)
    } finally {
      setRetryingId(null)
    }
  }

  return (
    <div>
      {actionError ? <ActionError message={actionError} variant="prominent" className="mb-4" /> : null}

      <PageHeader
        title="中台 · 资产"
        desc="被治理后可检索、可引用、可导出的内容。三态：已接入（机洗未完成或失败）→ 待人洗（等人确认或补填）→ 已发布（可被引用与写回）。知识缺口不是资产：拒答后在这里排队，人补文档再发布。"
        actions={
          <button type="button" className="btn btn-primary" onClick={() => openDrawer(null)}>
            <Plus aria-hidden size={14} weight="bold" />
            登记资产
          </button>
        }
      >
        <div className="seg" role="tablist" aria-label="资产状态筛选">
          {TABS.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              className={`seg-btn ${activeTab === tab ? 'seg-btn-active' : ''}`}
              onClick={() => setTab(tab)}
            >
              {tab}
              <span className={activeTab === tab ? 'text-ink-3' : ''}>
                {tab === '全部'
                  ? assets.length
                  : tab === '知识缺口'
                    ? (openGapCount ?? '—')
                    : counts[TAB_TO_STATUS[tab]]}
              </span>
            </button>
          ))}
        </div>
      </PageHeader>

      {state.phase === 'ok' ? (
        <div className="mb-4 grid gap-3 sm:grid-cols-3" aria-label="治理队列摘要">
          <button
            type="button"
            className={`stat-card ${activeTab === '待人洗' ? 'stat-card-active' : ''}`}
            onClick={() => setTab('待人洗')}
            title="纯新待办：修订中的待办跟着它的线上资产走，不在这里计数"
          >
            <div className="stat-label">待人洗 · 待办</div>
            <div className="stat-value">{counts.pending_review}</div>
            <div className="stat-hint">纯新待办 · 修订跟线上走</div>
          </button>
          <button
            type="button"
            className={`stat-card ${activeTab === '已发布' ? 'stat-card-active' : ''}`}
            onClick={() => setTab('已发布')}
            title="线上在服务：含修订中的资产，客服与连接层只读这一口径"
          >
            <div className="stat-label">已发布 · 线上口径</div>
            <div className="stat-value">{counts.published}</div>
            <div className="stat-hint">检索与引用只读这一口径</div>
          </button>
          <button
            type="button"
            className={`stat-card ${activeTab === '知识缺口' ? 'stat-card-active' : ''}`}
            onClick={() => setTab('知识缺口')}
            title="拒答后排队：人补文档并发布后关闭，不是资产"
          >
            <div className="stat-label">知识缺口 · 待补</div>
            <div className="stat-value">{openGapCount ?? '—'}</div>
            <div className="stat-hint">拒答排队 · 发布后关闭</div>
          </button>
        </div>
      ) : null}

      {activeTab === '知识缺口' ? (
        gapsQ.state.phase === 'loading' ? (
          <div className="panel">
            <SkeletonRows rows={5} />
          </div>
        ) : gapsQ.state.phase === 'error' ? (
          <>
            <ErrorBanner error={gapsQ.state.error} onRetry={gapsQ.reload} />
            <div className="panel">
              <Empty
                icon={<Warning aria-hidden size={26} />}
                title="知识缺口加载失败"
                hint="API 暂时不可用或网络中断，上面的横幅可重试。"
              />
            </div>
          </>
        ) : gaps.length === 0 ? (
          <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
            <Empty
              title="没有知识缺口"
              hint="客服无证据拒答时会记一条待办。不是工单，也不是资产。"
            />
          </div>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="table-gov">
              <thead>
                <tr>
                  <th className="w-20">缺口 ID</th>
                  <th>顾客原问</th>
                  <th className="w-24">热度</th>
                  <th className="w-36">挂商品</th>
                  <th className="w-20">状态</th>
                  <th className="w-44">提出时间</th>
                  <th className="w-36"></th>
                </tr>
              </thead>
              <tbody>
                {gaps.map((gap) => (
                  <tr key={gap.id}>
                    <td className="font-mono text-xs text-ink-3">{formatGapId(gap.id)}</td>
                    <td className="max-w-[28rem] text-[13px] text-ink">{gap.question}</td>
                    <td>
                      {/* 第 39 刀热度徽章：N>1 高亮（最常被问=最该先补） */}
                      <span
                        className={gap.hit_count > 1 ? 'badge badge-review' : 'text-caption'}
                        title={
                          gap.hit_count > 1
                            ? `重复问法累计被问 ${gap.hit_count} 次`
                            : '首次被问'
                        }
                      >
                        被问 {gap.hit_count} 次
                      </span>
                    </td>
                    <td className="text-[13px]">
                      {gap.product !== null ? (
                        <span className="text-ink-2" title={`P-${gap.product.id}`}>
                          {gap.product.name}
                        </span>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </td>
                    <td>
                      <GapStatusBadge status={gap.status} />
                    </td>
                    <td className="text-xs text-ink-3 tabular-nums">
                      {formatDateTime(gap.created_at)}
                    </td>
                    <td>
                      {gap.status === 'open' ? (
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => fillGap(gap)}
                          title="有已发布规格则开该资产修订；否则登记一份新文档。发布后缺口关闭"
                        >
                          去补文档
                        </button>
                      ) : gap.resolved_by_asset_id !== null ? (
                        <Link
                          to={`/platform/assets/${gap.resolved_by_asset_id}`}
                          className="badge badge-published transition-colors duration-150 hover:brightness-110"
                          title={
                            gap.resolved_at !== null
                              ? `解决于 ${formatDateTime(gap.resolved_at)}`
                              : undefined
                          }
                        >
                          已由 {formatAssetId(gap.resolved_by_asset_id)} 解决
                        </Link>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : state.phase === 'loading' ? (
        <div className="panel">
          <SkeletonRows rows={7} />
        </div>
      ) : state.phase === 'error' ? (
        <>
          <ErrorBanner error={state.error} onRetry={reload} />
          <div className="panel">
            <Empty
              icon={<Warning aria-hidden size={26} />}
              title="资产列表加载失败"
              hint="API 暂时不可用或网络中断，上面的横幅可重试。"
            />
          </div>
        </>
      ) : filtered.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<Plus aria-hidden size={24} />}
            title={activeTab === '全部' ? '还没有资产' : `没有${activeTab}的资产`}
            hint={EMPTY_HINTS[activeTab]}
            action={
              <button type="button" className="btn btn-primary btn-sm" onClick={() => openDrawer(null)}>
                <Plus aria-hidden size={13} weight="bold" />
                登记资产
              </button>
            }
          />
        </div>
      ) : (
        <div className="panel overflow-x-auto">
          <table className="table-gov">
            <thead>
              <tr>
                <th className="w-20">资产 ID</th>
                <th>标题</th>
                <th className="w-16">种类</th>
                <th className="w-20">来源</th>
                <th className="w-36">状态</th>
                <th className="w-36">所挂商品</th>
                <th className="w-20">线上版本</th>
                <th className="w-56">失败原因</th>
                <th className="w-24"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((asset) => (
                <tr
                  key={asset.id}
                  className="row-click"
                  onClick={() => navigate(`/platform/assets/${asset.id}`)}
                >
                  <td className="font-mono text-xs text-ink-3">{formatAssetId(asset.id)}</td>
                  <td className="max-w-[22rem]">
                    <Link
                      to={`/platform/assets/${asset.id}`}
                      className="font-medium text-ink transition-colors duration-150 hover:text-accent-strong"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {asset.title ?? '未命名资产'}
                    </Link>
                    {isStale(asset.last_verified_at) ? (
                      <span
                        className="badge badge-review ml-2 align-middle"
                        title="距上次验证超过 90 天：该资产的检索证据已降权，请到详情页「重新验证」"
                      >
                        未验证 &gt;90 天
                      </span>
                    ) : null}
                  </td>
                  <td>
                    <KindChip kind={asset.kind} />
                  </td>
                  <td className="text-xs text-ink-3" title={asset.source_kind}>
                    {sourceKindLabel(asset.source_kind)}
                  </td>
                  <td>
                    <StatusBadge
                      status={asset.status}
                      failed={asset.status === 'ingested' && asset.last_error !== null}
                      revising={asset.revising}
                      publishedVersionNo={asset.current_published_version_no}
                    />
                  </td>
                  <td className="text-ink-2">
                    {asset.product ? (
                      `${asset.product.name}`
                    ) : (
                      <span className="text-ink-3">未挂商品</span>
                    )}
                  </td>
                  <td className="font-mono text-xs text-ink-2">
                    {asset.current_published_version_no !== null ? `v${asset.current_published_version_no}` : '—'}
                  </td>
                  <td className="max-w-[14rem] truncate text-xs text-ink-3" title={asset.last_error ?? undefined}>
                    {asset.status === 'ingested' && asset.last_error ? (
                      <span className="text-danger">{asset.last_error}</span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    {asset.status === 'ingested' ? (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          void retryWash(asset.id)
                        }}
                        disabled={retryingId !== null}
                        title="只有已接入（机洗未完成或失败）的资产可以重试"
                      >
                        <ArrowsClockwise aria-hidden size={12} />
                        {retryingId === asset.id ? '重试中…' : '重试机洗'}
                      </button>
                    ) : (
                      /* 行尾箭头 hover 才现（StaffDesk 手法：次级导航线索不与数据抢注意） */
                      <span className="row-caret flex items-center justify-end text-caption">
                        <CaretRight aria-hidden size={13} />
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <RegisterAssetDrawer
        key={drawerGap !== null ? `gap-${drawerGap.id}` : 'normal'}
        open={drawerOpen}
        gap={drawerGap ?? undefined}
        onClose={closeDrawer}
      />
    </div>
  )
}
