// 治理台资产列表：三态分段筛选（已接入/待人洗/已发布）+ 高密度表格 + 登记入口。
// 口径：一次拉全量在客户端过滤（数据量小，三个 tab 的计数顺手同源，不另发请求）。

import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowsClockwise, CaretRight, Plus, Warning } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { AssetStatus } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { ErrorBanner } from '../components/Banner'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { KindChip, StatusBadge } from '../components/StateBadge'
import RegisterAssetDrawer from './RegisterAssetDrawer'

const TABS: { status: AssetStatus; label: string; emptyTitle: string; emptyHint: string }[] = [
  {
    status: 'ingested',
    label: '已接入',
    emptyTitle: '没有已接入的资产',
    emptyHint:
      '新登记的内容会先进已接入：机洗成功直接进入待人洗，只有机洗失败（或尚未运行）才会留在这里等待重试。',
  },
  {
    status: 'pending_review',
    label: '待人洗',
    emptyTitle: '没有待人洗的资产',
    emptyHint:
      '登记文档并挂上商品后，机洗按商品规格字段抽取；抽到的值待确认、没抽到的待补填，都会在这里等操作者处理。',
  },
  {
    status: 'published',
    label: '已发布',
    emptyTitle: '还没有已发布的资产',
    emptyHint:
      '待人洗的资产补齐必填字段并确认机洗值后即可发布；发布后写回商品规格，成为线上口径。',
  },
]

const EMPTY_ASSETS = [] as const

export default function AssetsListPage() {
  const navigate = useNavigate()
  const fetcher = useCallback(() => api.listAssets(), [])
  const { state, reload } = useApiData(fetcher)
  const assets = state.phase === 'ok' ? state.data : EMPTY_ASSETS

  const [activeTab, setActiveTab] = useState<AssetStatus>('pending_review')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const counts = useMemo(() => {
    const by: Record<AssetStatus, number> = { ingested: 0, pending_review: 0, published: 0 }
    for (const a of assets) by[a.status] += 1
    return by
  }, [assets])

  const filtered = useMemo(() => assets.filter((a) => a.status === activeTab), [assets, activeTab])
  const activeTabMeta = TABS.find((t) => t.status === activeTab) ?? TABS[0]

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
      {actionError ? (
        <div
          role="alert"
          className="mb-4 flex items-center gap-2.5 rounded-[8px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-3.5 py-2.5 text-[13px] leading-5 text-danger"
        >
          <Warning aria-hidden size={15} className="shrink-0" />
          {actionError}
        </div>
      ) : null}

      <PageHeader
        title="中台 · 资产"
        desc="被治理后可检索、可引用、可导出的内容。三态：已接入（机洗未完成或失败）→ 待人洗（等人确认或补填）→ 已发布（可被引用与写回）。发布权只在治理台。"
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setDrawerOpen(true)}>
            <Plus aria-hidden size={14} weight="bold" />
            登记资产
          </button>
        }
      >
        <div className="seg" role="tablist" aria-label="资产状态筛选">
          {TABS.map((tab) => (
            <button
              key={tab.status}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.status}
              className={`seg-btn ${activeTab === tab.status ? 'seg-btn-active' : ''}`}
              onClick={() => setActiveTab(tab.status)}
            >
              {tab.label}
              <span className={activeTab === tab.status ? 'text-ink-3' : ''}>{counts[tab.status]}</span>
            </button>
          ))}
        </div>
      </PageHeader>

      {state.phase === 'loading' ? (
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
            title={activeTabMeta.emptyTitle}
            hint={activeTabMeta.emptyHint}
            action={
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setDrawerOpen(true)}>
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
                <th className="w-16">资产 ID</th>
                <th>标题</th>
                <th className="w-16">种类</th>
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
                  <td className="font-mono text-xs text-ink-3">{asset.id}</td>
                  <td className="max-w-[22rem]">
                    <Link
                      to={`/platform/assets/${asset.id}`}
                      className="font-medium text-ink transition-colors duration-150 hover:text-accent-strong"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {asset.title ?? '未命名资产'}
                    </Link>
                  </td>
                  <td>
                    <KindChip kind={asset.kind} />
                  </td>
                  <td>
                    <StatusBadge status={asset.status} failed={asset.status === 'ingested' && asset.last_error !== null} />
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
                      <span className="flex items-center justify-end text-caption">
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

      <RegisterAssetDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </div>
  )
}
