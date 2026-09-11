// 治理台资产列表：五分段筛选（全部/已接入/待人洗/已发布/知识缺口）+ 高密度表格
// + 登记入口（可从缺口预填）。
// 口径：资产一次拉全量在客户端过滤（数据量小，各 tab 计数顺手同源，不另发请求）；
// 知识缺口不是资产——单独走 /knowledge-gaps，open 与 resolved 各拉一次合并展示。
// 当前 tab 同步进 ?status=（刷新保持；客服拒答芯片跳 ?status=知识缺口）；默认落在「待人洗」，
// 无参数时不再回落「全部」。「工作队列 / 全部」同步进 ?view=（默认工作队列，滤掉 CI 探针行）。
// 两处 URL 更新都走函数式 updater：整体替换会互相抹掉 status/view。

import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowsClockwise,
  CaretDown,
  CaretRight,
  Funnel,
  MagnifyingGlass,
  Plus,
  Warning,
  X,
} from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { AssetListItem, AssetStatus, KnowledgeGap } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import {
  formatDateTime,
  formatAssetId,
  formatGapId,
  isStale,
  sourceKindLabel,
} from '../labels'
import {
  filterWorkAssets,
  isFoldedImport,
  isIngested,
  isPendingWash,
  isPublished,
  parseAssetView,
  type AssetView,
} from '../workQueue'
import { ErrorBanner } from '../components/Banner'
import ActionError from '../components/ActionError'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { GapStatusBadge, KindChip, StatusBadge } from '../components/StateBadge'
import ConfirmDialog from '../components/ConfirmDialog'
import RegisterAssetDrawer from './RegisterAssetDrawer'

// tab 值即 ?status= 的取值（与拒答芯片的跳转链接 /platform/assets?status=知识缺口 对齐）
const TABS = ['全部', '已接入', '待人洗', '已发布', '知识缺口'] as const
type ListTab = (typeof TABS)[number]

const TAB_TO_STATUS: Record<Exclude<ListTab, '全部' | '知识缺口'>, AssetStatus> = {
  已接入: 'ingested',
  待人洗: 'pending_review',
  已发布: 'published',
}

/** 「当前 tab 下的行」单一来源（第 51 刀评审 P2：filtered 与来源计数各抄一份谓词
 * 会漂）。三态口径与总览页同源（workQueue.isPendingWash/isPublished）；
 * 「全部」「知识缺口」= 不筛状态。 */
/** 是否是已知来源词：labels 的映射里没有的，`sourceKindLabel` 原样返回自己
 * （第 50 刀词表是权威集合的镜像；未知值不进筛选态）。 */
function isKnownSourceKind(value: string): boolean {
  return sourceKindLabel(value) !== value
}

function rowsInTab(rows: readonly AssetListItem[], tab: ListTab): AssetListItem[] {
  if (tab === '全部' || tab === '知识缺口') return [...rows]
  if (tab === '已发布') return rows.filter(isPublished)
  if (tab === '待人洗') return rows.filter(isPendingWash)
  return rows.filter((a) => a.status === TAB_TO_STATUS[tab])
}

// 删掉 tab 下方的摘要卡后，把原来卡上的口径说明留在 tab 悬停上：
// 数字只在分段出现一次，语义（纯新待办 / 线上口径 / 缺口非资产）不丢。
const TAB_TITLES: Partial<Record<ListTab, string>> = {
  待人洗: '纯新待办：修订中的待办跟线上资产走，不计入这里',
  已发布: '线上在服务：含修订中的资产，客服与连接层只读这一口径',
  知识缺口: '拒答后排队：人补文档并发布后关闭，不是资产',
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
  const resolvedGapCount = gapsQ.state.phase === 'ok' ? gapsQ.state.data[1].length : null
  // 缺口分段（走查实录 A4）：此前 tab 徽标只算 open、表格却把已解决也混着列，
  // 徽标 7 对表格 9 行。分段后列表与计数同口径，tab 徽标 = 默认「待补」段。
  const [gapView, setGapView] = useState<'open' | 'resolved'>('open')
  const visibleGaps = useMemo(
    () => gaps.filter((g) => (gapView === 'open' ? g.status === 'open' : g.status !== 'open')),
    [gaps, gapView],
  )

  // tab 由 ?status= 驱动（无参数/非法值回落「待人洗」——默认工作视角不是全量库）。
  // 「全部」写显式 status=全部：清空 URL 会回落待人洗，点击像没反应。
  const statusParam = searchParams.get('status')
  const activeTab: ListTab = isListTab(statusParam) ? statusParam : '待人洗'
  const view = parseAssetView(searchParams.get('view'))
  const setTab = (tab: ListTab) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('status', tab)
      return next
    })
  const setView = (target: AssetView) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('view', target)
      return next
    })

  // 第 51 刀：来源筛选（?source=，空=全部）。工作队列首屏被导入货淹掉（评论 200
  // 条），运营要能一键只看自己传的或只看某一来源——筛选不改数据、不动默认。
  const sourceParam = searchParams.get('source')
  // 只认已知来源词（未知/拼错的值当作未筛选——否则会出现一个叫「typo」的 chip
  // 和一个说不清为什么空的屏幕；后端对未知值是 422，前端也不该把它当有效筛选）
  const activeSource =
    sourceParam !== null && sourceParam !== '' && isKnownSourceKind(sourceParam)
      ? sourceParam
      : null
  const setSource = (source: string | null) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (source === null) next.delete('source')
      else next.set('source', source)
      return next
    })

  // 搜索只做客户端过滤（数据量小），不进 URL：切 tab / view 保留输入，刷新即清。
  const [query, setQuery] = useState('')

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

  // 工作队列先收窄一次，计数、列表、摘要卡都在 scoped 上分叉——同一谓词单一来源
  // （workQueue.filterWorkAssets），禁止各处复制。总览页计数同源。
  const scoped = useMemo<AssetListItem[]>(
    () => (view === 'work' ? filterWorkAssets(assets) : [...assets]),
    [assets, view],
  )

  const counts = useMemo(() => {
    return {
      ingested: scoped.filter(isIngested).length,
      pending_review: scoped.filter(isPendingWash).length,
      published: scoped.filter(isPublished).length,
    }
  }, [scoped])

  // 过滤顺序 view（scoped 已生效）→ status → 搜索。
  // 「全部」= 不筛状态；已发布=指针非空（含修订中）；待人洗=纯新待办（无指针）——
  // 三态口径与总览页同源（workQueue.isPendingWash/isPublished）。
  // 有查询词时跳过状态 tab：搜索的语义是「找这条资产」，在当前视图内全状态搜——
  // 否则在默认「待人洗」下搜已发布资产得 0 行，像这条资产不存在。
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    let rows: typeof scoped
    if (q !== '' || activeTab === '全部' || activeTab === '知识缺口') rows = scoped
    else rows = rowsInTab(scoped, activeTab)

    if (activeSource !== null) rows = rows.filter((a) => a.source_kind === activeSource)

    if (q === '') return rows
    // NULL 标题用展示兜底「未命名资产」，ID 同时支持裸数字与 A-0000 形态。
    return rows.filter((a) => {
      const title = (a.title ?? '未命名资产').toLowerCase()
      return (
        title.includes(q) ||
        String(a.id).includes(q) ||
        formatAssetId(a.id).toLowerCase().includes(q)
      )
    })
  }, [scoped, activeTab, query, activeSource])

  // 来源筛选 chips 的取数：按「当前 tab + 范围」下的实际行统计（**不含搜索词**——
  // chip 是这一屏有什么的导航，不随输入跳动）。只显示有行的来源。
  const sourceCounts = useMemo(() => {
    const counts = new Map<string, number>()
    if (activeTab === '知识缺口') return counts
    for (const asset of rowsInTab(scoped, activeTab)) {
      counts.set(asset.source_kind, (counts.get(asset.source_kind) ?? 0) + 1)
    }
    return counts
  }, [scoped, activeTab])

  // 第 59 刀：待人洗队列里导入货占绝大多数（180/194），首屏被铺满。把**数据集导入**
  // 行折叠到表尾（默认收起，带计数），人工/系统产生的行先铺开——不删数据、不改默认
  // 视角、总数与原有次序不变（导入组内保持 filtered 的相对次序）。
  // 第 64 刀（审计刀 12 C-P2）：折叠口径改成 isFoldedImport（导入且**未发布**）——
  // 已发布的导入行是线上证据，进主行；折叠组只剩真正的待办，与「待人洗待办」语义对齐。
  const importedRows = useMemo(() => filtered.filter(isFoldedImport), [filtered])
  const primaryRows = useMemo(() => filtered.filter((a) => !isFoldedImport(a)), [filtered])
  const [importsOpenManual, setImportsOpen] = useState(false)
  // 自动展开（审计刀 12 P1）：筛选/搜索只命中导入货时（主行 0 条），收起态会让屏幕
  // 只剩一条折叠头——像「没找到」，而用户搜的正是那条导入资产。手动折叠仍可选。
  const importsOpen = importsOpenManual || (primaryRows.length === 0 && importedRows.length > 0)

  // 「去补文档」（审计刀 8 P1：方案 UX-C.3 明令禁止静默开修订）：
  // 该商品已有已发布规格文档时，先问清「在这份上开修订」还是「另起一份新文档」——
  // 此前直接 openRevision，操作者连自己点了什么都不知道（双击还会 409）。
  const [revisionChoice, setRevisionChoice] = useState<{ gap: KnowledgeGap; specId: number } | null>(
    null,
  )

  const openRevisionForGap = async (gap: KnowledgeGap, specId: number) => {
    setRevisionChoice(null)
    setActionError(null)
    try {
      const updated = await api.openRevision(specId, gap.id)
      navigate(`/platform/assets/${updated.id}`)
    } catch (err) {
      setActionError(`开修订失败：${detailText(err)}`)
    }
  }

  const fillGap = (gap: KnowledgeGap) => {
    if (gap.product !== null) {
      const spec = assets.find(
        (a) =>
          a.kind === 'document' &&
          a.product?.id === gap.product?.id &&
          a.current_published_version_no !== null,
      )
      if (spec !== undefined) {
        setRevisionChoice({ gap, specId: spec.id })
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
        desc="被治理后可检索、可引用、可导出的内容；知识缺口也在这里排队补文档。"
        actions={
          <button type="button" className="btn btn-primary" onClick={() => openDrawer(null)}>
            <Plus aria-hidden size={14} weight="bold" />
            登记资产
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <div className="seg" role="tablist" aria-label="资产状态筛选">
            {TABS.map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={activeTab === tab}
                className={`seg-btn ${activeTab === tab ? 'seg-btn-active' : ''}`}
                onClick={() => setTab(tab)}
                title={TAB_TITLES[tab]}
                aria-label={TAB_TITLES[tab] !== undefined ? `${tab}（${TAB_TITLES[tab]}）` : tab}
              >
                {tab}
                <span className={activeTab === tab ? 'text-ink-3' : ''}>
                  {tab === '全部'
                    ? scoped.length
                    : tab === '知识缺口'
                      ? (openGapCount ?? '—')
                      : counts[TAB_TO_STATUS[tab]]}
                </span>
              </button>
            ))}
          </div>
          {activeTab === '知识缺口' ? null : (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[11px] text-caption">范围</span>
              <div className="seg" role="tablist" aria-label="工作队列范围">
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'work'}
                  className={`seg-btn ${view === 'work' ? 'seg-btn-active' : ''}`}
                  onClick={() => setView('work')}
                  title="排除 CI 冒烟 / 证据探针（连接层登记的机器行）"
                >
                  工作队列
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'all'}
                  className={`seg-btn ${view === 'all' ? 'seg-btn-active' : ''}`}
                  onClick={() => setView('all')}
                  title="含 CI 冒烟 / 证据探针的全部登记"
                >
                  全部登记
                </button>
              </div>
            </div>
          )}
        </div>
      </PageHeader>

      {/* 来源筛选（第 51 刀）：工作队列首屏被导入货淹掉（评论 200 条），
          运营要能一键只看自己传的、或只看某一来源。只列**当前这屏真有的**来源。 */}
      {state.phase === 'ok' &&
      activeTab !== '知识缺口' &&
      (sourceCounts.size > 1 || activeSource !== null) ? (
        <div className="mb-2 flex flex-wrap items-center gap-1.5" role="group" aria-label="来源筛选">
          <span className="text-[11px] text-caption">来源</span>
          <button
            type="button"
            className={`kind-chip ${activeSource === null ? 'kind-chip-active' : ''}`}
            aria-pressed={activeSource === null}
            onClick={() => setSource(null)}
          >
            全部
          </button>
          {[
            ...sourceCounts.entries(),
            // 选中的来源若在本 tab 为 0 条，也要渲染出来（否则没有任何 chip 高亮，
            // 空屏看不出「谁在生效」——审计刀 10 P2）
            ...(activeSource !== null && !sourceCounts.has(activeSource)
              ? ([[activeSource, 0]] as [string, number][])
              : []),
          ]
            .sort((a, b) => b[1] - a[1])
            .map(([kind, count]) => (
              <button
                key={kind}
                type="button"
                className={`kind-chip ${activeSource === kind ? 'kind-chip-active' : ''}`}
                aria-pressed={activeSource === kind}
                onClick={() => setSource(activeSource === kind ? null : kind)}
                title={`只看「${sourceKindLabel(kind)}」来源（${count} 条）`}
              >
                {sourceKindLabel(kind)} {count}
              </button>
            ))}
        </div>
      ) : null}

      {state.phase === 'ok' && activeTab !== '知识缺口' ? (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="relative w-full max-w-xs">
            <MagnifyingGlass
              aria-hidden
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-caption"
            />
            <input
              className="input w-full pl-8 pr-8"
              aria-label="搜索资产"
              placeholder="搜索标题 / ID（如 保温杯、A-0029）…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query !== '' ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm absolute right-1 top-1/2 -translate-y-1/2"
                aria-label="清空搜索"
                onClick={() => setQuery('')}
              >
                <X aria-hidden size={12} />
              </button>
            ) : null}
          </div>
          {query.trim() !== '' ? (
            <span className="text-xs tabular-nums text-ink-3">
              {filtered.length} 条匹配 · 搜索覆盖全部状态
            </span>
          ) : null}
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
            <div className="flex items-center gap-2 border-b border-line-1 px-4 py-2.5">
              <div className="seg" role="tablist" aria-label="缺口状态筛选">
                <button
                  type="button"
                  role="tab"
                  aria-selected={gapView === 'open'}
                  className={`seg-btn ${gapView === 'open' ? 'seg-btn-active' : ''}`}
                  onClick={() => setGapView('open')}
                  title="拒答后排队、还没补口径的缺口"
                >
                  待补
                  <span className={gapView === 'open' ? 'text-ink-3' : ''}>
                    {openGapCount ?? '—'}
                  </span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={gapView === 'resolved'}
                  className={`seg-btn ${gapView === 'resolved' ? 'seg-btn-active' : ''}`}
                  onClick={() => setGapView('resolved')}
                  title="补了口径并发布后自动关闭的缺口（留档可回溯）"
                >
                  已解决
                  <span className={gapView === 'resolved' ? 'text-ink-3' : ''}>
                    {resolvedGapCount ?? '—'}
                  </span>
                </button>
              </div>
            </div>
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
                {visibleGaps.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-6 text-center text-[13px] text-ink-3">
                      {gapView === 'open'
                        ? '没有待补缺口——切到「已解决」看历史。'
                        : '还没有已解决的缺口：补文档并发布后自动关闭，关闭记录留在这里。'}
                    </td>
                  </tr>
                ) : (
                  visibleGaps.map((gap) => (
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
                  ))
                )}
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
        query.trim() !== '' ? (
          <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
            <Empty
              icon={<MagnifyingGlass aria-hidden size={24} />}
              title="没有匹配的资产"
              hint="按标题（不区分大小写）或资产 ID 搜索：A-0029 / 29 都可命中；清空输入恢复当前筛选。"
            />
          </div>
        ) : activeSource !== null ? (
          // 来源筛选挡空且搜索框为空：说清是**筛选**所致（否则「没有已接入的资产」
          // 会与「其实有、只是被来源过滤掉」直接矛盾——审计刀 10 P1）
          <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
            <Empty
              icon={<Funnel aria-hidden size={24} />}
              title={`当前筛选下没有「${sourceKindLabel(activeSource)}」来源的资产`}
              hint={`${activeTab}页签里有资产，只是没有这个来源的。点来源行的「全部」清除筛选（不影响数据）。`}
              action={
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setSource(null)}>
                  清除来源筛选
                </button>
              }
            />
          </div>
        ) : (
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
        )
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
              {primaryRows.map((asset) => (
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
              {importedRows.length > 0 ? (
                <>
                  <tr className="bg-canvas">
                    <td colSpan={9} className="px-3 py-1.5">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        aria-expanded={importsOpen}
                        onClick={() => setImportsOpen((prev) => !prev)}
                        title="数据集批量导入且未发布的待办行（评论语料 / 开放数据集商品与规格）——已发布的导入行是线上证据，在主行正常显示"
                      >
                        {importsOpen ? (
                          <CaretDown aria-hidden size={13} />
                        ) : (
                          <CaretRight aria-hidden size={13} />
                        )}
                        <span className="text-[13px] font-medium text-ink">
                          数据集导入待办 {importedRows.length} 条
                        </span>
                        <span className="text-xs text-ink-3">
                          （评论语料 / 开放数据集，非人工登记；已发布的导入行在上方主行）
                        </span>
                      </button>
                    </td>
                  </tr>
                  {importsOpen
                    ? importedRows.map((asset) => (
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
                            {asset.current_published_version_no !== null
                              ? `v${asset.current_published_version_no}`
                              : '—'}
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
                              <span className="row-caret flex items-center justify-end text-caption">
                                <CaretRight aria-hidden size={13} />
                              </span>
                            )}
                          </td>
                        </tr>
                      ))
                    : null}
                </>
              ) : null}
            </tbody>
          </table>
        </div>
      )}

      {/* 补口径的两条路（审计刀 8 P1）：不再静默开修订，先把后果说清 */}
      <ConfirmDialog
        open={revisionChoice !== null}
        title="这个缺口怎么补？"
        confirmLabel="在既有文档上开修订"
        cancelLabel="另起一份新文档"
        busy={false}
        onCancel={() => {
          const choice = revisionChoice
          setRevisionChoice(null)
          if (choice !== null) openDrawer(choice.gap)
        }}
        onConfirm={() => {
          const choice = revisionChoice
          if (choice !== null) void openRevisionForGap(choice.gap, choice.specId)
        }}
        body={
          <div className="space-y-1.5">
            <p>
              该商品已有已发布规格文档{' '}
              <span className="font-mono">{formatAssetId(revisionChoice?.specId ?? 0)}</span>
              ，可直接在它上面开修订并补上这条口径。
            </p>
            <p>若这份口径属于另一类内容，也可以另起一份新文档登记。</p>
          </div>
        }
      />
      <RegisterAssetDrawer
        key={drawerGap !== null ? `gap-${drawerGap.id}` : 'normal'}
        open={drawerOpen}
        gap={drawerGap ?? undefined}
        onClose={closeDrawer}
      />
    </div>
  )
}
