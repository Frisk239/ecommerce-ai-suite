// 素材中心（第 17 刀/ADR 0038）：卖点文案生成任务的列表 + 新建抽屉 + 详情抽屉。
// 任务不是中台对象（0012）：五态徽章走既有灰阶体系（排队/进行中=灰、待抽检=琥珀、
// 已登记=绿、失败=红），registered 行给 A-xxxx 链接直达治理台详情。
// 建任务/重试是请求内同步生成（LLM ≤20s）：按钮置「生成中/重试中…」禁用态，
// 结果回来即整表 reload（不轮询演戏——0012 无任务 worker）。
// 第 18 刀（ADR 0015）：加「切片汇入」页签——直播切片拣选登记出的视频资产列表
// （kind=视频 且 来源=切片拣选）。视图不是二次登记：只展示已登记资产，运营只
// 引用其中已发布的。列表端点无种类/来源过滤参数，客户端全量过滤（与治理台
// 列表同口径，侵入最小）。

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowsClockwise,
  CaretRight,
  FilmSlate,
  Megaphone,
  Plus,
  Warning,
  X,
} from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { AssetListItem, MaterialTask } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatAssetId, formatDateTime, formatTaskId } from '../labels'
import { ErrorBanner } from '../components/Banner'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { StatusBadge, TaskStatusBadge } from '../components/StateBadge'

const EMPTY_TASKS = [] as const
const EMPTY_CLIP_ASSETS: AssetListItem[] = []

// 页签（对照 AssetsListPage 的 seg 模式）：任务列表=本模块自有状态机；
// 切片汇入=已登记视频资产的只读视图（0015）
const TABS = ['任务列表', '切片汇入'] as const
type MaterialTab = (typeof TABS)[number]

// ---------- 新建任务抽屉 ----------

function CreateTaskDrawer({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const productsFetcher = useCallback(() => api.listProducts(), [])
  const productsQ = useApiData(productsFetcher)
  const products = productsQ.state.phase === 'ok' ? productsQ.state.data : []

  const [productId, setProductId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  const submit = async () => {
    if (submitting) return
    if (productId === '') {
      setError('请选择商品')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      // 同步就地执行：请求返回即稳定态（pending_qc 或 failed），无轮询
      await api.createMaterialTask(Number(productId))
      onCreated()
      onClose()
      setProductId('')
    } catch (err) {
      setError(detailText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="生成卖点文案">
      <div className="modal-backdrop absolute inset-0" onClick={submitting ? undefined : onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-md flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <div className="flex-1 text-[14px] font-semibold text-ink">生成卖点文案</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={submitting} aria-label="关闭生成抽屉">
            <X aria-hidden size={14} />
          </button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <p className="text-xs leading-5 text-ink-3">
            选择商品后由厂商模型生成「标题 + 正文」卖点文案，规则质检（非空/总长≤2000/标题非空/正文含商品名）过线即落
            待抽检，等操作者在任务详情里通过或打回。生成失败不降级：任务直接失败，可重试。
          </p>
          <div>
            <label className="field-label" htmlFor="material-product">
              商品
            </label>
            <select
              id="material-product"
              className="input w-full"
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              disabled={submitting}
            >
              <option value="">选择商品…</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.category}
                </option>
              ))}
            </select>
          </div>
          {error ? (
            <div
              role="alert"
              className="rounded-[6px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-3 py-2 text-xs leading-5 text-danger"
            >
              {error}
            </div>
          ) : null}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-line-2 px-4 py-3">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={submitting || productId === ''}>
            <Megaphone aria-hidden size={14} />
            {submitting ? '生成中…（≤20s）' : '开始生成'}
          </button>
        </div>
      </aside>
    </div>
  )
}

// ---------- 任务详情抽屉 ----------

function TaskDetailDrawer({
  task,
  onClose,
  onActioned,
}: {
  task: MaterialTask | null
  onClose: () => void
  onActioned: () => void
}) {
  const [busy, setBusy] = useState<null | 'approve' | 'reject' | 'retry'>(null)
  const [error, setError] = useState<string | null>(null)

  if (task === null) return null

  const run = async (action: 'approve' | 'reject' | 'retry') => {
    if (busy !== null) return
    setBusy(action)
    setError(null)
    try {
      if (action === 'approve') await api.approveMaterialTask(task.id)
      else if (action === 'reject') await api.rejectMaterialTask(task.id)
      else await api.retryMaterialTask(task.id)
      onActioned()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={`素材任务 ${formatTaskId(task.id)}`}>
      <div className="modal-backdrop absolute inset-0" onClick={busy === null ? onClose : undefined} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-lg flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <div className="flex-1 text-[14px] font-semibold text-ink">
            任务 {formatTaskId(task.id)}
            <span className="ml-2 font-mono text-xs text-ink-3">{task.product_name}</span>
          </div>
          <TaskStatusBadge status={task.status} />
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy !== null} aria-label="关闭任务详情">
            <X aria-hidden size={14} />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <div>
            <span className="field-label">质检结果</span>
            {task.status === 'failed' ? (
              <p className="text-[13px] leading-5 text-danger">{task.last_error ?? '失败原因未记录'}</p>
            ) : task.status === 'pending_qc' ? (
              <p className="text-[13px] leading-5 text-ok">规则质检已过线</p>
            ) : (
              <p className="text-[13px] leading-5 text-ink-3">—</p>
            )}
          </div>

          <div>
            <span className="field-label">生成文案</span>
            {task.title !== null || task.content !== null ? (
              <div className="rounded-[8px] border border-line-2 bg-canvas px-3.5 py-3">
                <div className="text-[14px] font-semibold leading-6 text-ink">{task.title ?? '（无标题）'}</div>
                <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs leading-5 text-ink-2">
                  {task.content ?? '（无正文）'}
                </pre>
              </div>
            ) : (
              <p className="text-[13px] leading-5 text-ink-3">还没有生成物（排队/进行中，或本次生成失败）。</p>
            )}
          </div>

          {task.status === 'registered' && task.asset_id !== null ? (
            <div className="rounded-[6px] border border-[rgba(30,107,69,0.22)] bg-[rgba(30,107,69,0.05)] px-3 py-2 text-[13px] leading-5">
              已登记为素材资产：
              <Link to={`/platform/assets/${task.asset_id}`} className="ml-1 font-medium text-accent-strong hover:underline">
                {formatAssetId(task.asset_id)} · 去治理台
              </Link>
              <span className="ml-1 text-ink-3">（机洗弃权已推进待人洗，发布后可被检索引用）</span>
            </div>
          ) : null}

          {error ? (
            <div
              role="alert"
              className="rounded-[6px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-3 py-2 text-xs leading-5 text-danger"
            >
              {error}
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line-2 px-4 py-3">
          {task.status === 'pending_qc' ? (
            <>
              <button type="button" className="btn btn-secondary" onClick={() => void run('reject')} disabled={busy !== null}>
                {busy === 'reject' ? '打回中…' : '打回'}
              </button>
              <button type="button" className="btn btn-primary" onClick={() => void run('approve')} disabled={busy !== null}>
                {busy === 'approve' ? '登记中…' : '抽检通过 · 登记为资产'}
              </button>
            </>
          ) : task.status === 'failed' ? (
            <button type="button" className="btn btn-primary" onClick={() => void run('retry')} disabled={busy !== null}>
              <ArrowsClockwise aria-hidden size={14} />
              {busy === 'retry' ? '重试中…（≤20s）' : '重试生成'}
            </button>
          ) : null}
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy !== null}>
            关闭
          </button>
        </div>
      </aside>
    </div>
  )
}

// ---------- 页面 ----------

export default function MaterialPage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<MaterialTab>('任务列表')

  const fetcher = useCallback(() => api.listMaterialTasks(), [])
  const { state, reload } = useApiData(fetcher)
  const tasks = state.phase === 'ok' ? state.data : EMPTY_TASKS

  // 切片汇入数据源：全量资产客户端过滤 video + clip_pick（0015 视图，不新端点）
  const assetsFetcher = useCallback(() => api.listAssets(), [])
  const assetsQ = useApiData(assetsFetcher)
  const clipAssets = useMemo(
    () =>
      assetsQ.state.phase === 'ok'
        ? assetsQ.state.data.filter((a) => a.kind === 'video' && a.source_kind === 'clip_pick')
        : EMPTY_CLIP_ASSETS,
    [assetsQ.state],
  )

  const [createOpen, setCreateOpen] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  // 详情永远从最新列表取行（动作后 reload，抽屉跟着刷新状态；列表里没了就关抽屉）
  const detailTask = useMemo(
    () => (detailId === null ? null : (tasks.find((t) => t.id === detailId) ?? null)),
    [detailId, tasks],
  )
  useEffect(() => {
    if (detailId !== null && state.phase === 'ok' && detailTask === null) setDetailId(null)
  }, [detailId, detailTask, state.phase])

  return (
    <div>
      <PageHeader
        title="业务能力 · 素材中心"
        desc="内容闭环第一段：选商品生成卖点文案，规则质检 + 操作者抽检双重过线才登记为素材资产（失败不进中台）。登记后走治理台人洗发布，成为可检索、可引用的证据。「切片汇入」页签列出直播切片拣选登记出的视频资产（0015：视图不是二次登记）。"
        actions={
          tab === '任务列表' ? (
            <button type="button" className="btn btn-primary" onClick={() => setCreateOpen(true)}>
              <Plus aria-hidden size={14} weight="bold" />
              生成卖点文案
            </button>
          ) : undefined
        }
      >
        <div className="seg" role="tablist" aria-label="素材中心视图">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              className={`seg-btn ${tab === t ? 'seg-btn-active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t}
              <span className={tab === t ? 'text-ink-3' : ''}>
                {t === '任务列表' ? tasks.length : clipAssets.length}
              </span>
            </button>
          ))}
        </div>
      </PageHeader>

      {tab === '切片汇入' ? (
        assetsQ.state.phase === 'loading' ? (
          <div className="panel">
            <SkeletonRows rows={5} />
          </div>
        ) : assetsQ.state.phase === 'error' ? (
          <>
            <ErrorBanner error={assetsQ.state.error} onRetry={assetsQ.reload} />
            <div className="panel">
              <Empty
                icon={<Warning aria-hidden size={26} />}
                title="切片汇入加载失败"
                hint="API 暂时不可用或网络中断，上面的横幅可重试。"
              />
            </div>
          </>
        ) : clipAssets.length === 0 ? (
          <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
            <Empty
              icon={<FilmSlate aria-hidden size={24} />}
              title="还没有拣选登记的视频资产"
              hint="去侧栏「直播切片」勾选候选、点「拣选登记」；这里列出登记出的视频资产（视图不是二次登记，发布与引用在治理台）。"
            />
          </div>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="table-gov">
              <thead>
                <tr>
                  <th className="w-20">资产 ID</th>
                  <th>标题</th>
                  <th className="w-24">状态</th>
                  <th className="w-36">商品</th>
                </tr>
              </thead>
              <tbody>
                {clipAssets.map((asset) => (
                  <tr
                    key={asset.id}
                    className="row-click"
                    onClick={() => navigate(`/platform/assets/${asset.id}`)}
                  >
                    <td className="font-mono text-xs text-ink-3">{formatAssetId(asset.id)}</td>
                    <td className="max-w-[28rem]">
                      <Link
                        to={`/platform/assets/${asset.id}`}
                        className="font-medium text-ink transition-colors duration-150 hover:text-accent-strong"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {asset.title ?? '未命名资产'}
                      </Link>
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
                      {asset.product ? asset.product.name : <span className="text-ink-3">未挂商品</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : state.phase === 'loading' ? (
        <div className="panel">
          <SkeletonRows rows={6} />
        </div>
      ) : state.phase === 'error' ? (
        <>
          <ErrorBanner error={state.error} onRetry={reload} />
          <div className="panel">
            <Empty icon={<Warning aria-hidden size={26} />} title="素材任务加载失败" hint="上面的横幅可重试。" />
          </div>
        </>
      ) : tasks.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<Megaphone aria-hidden size={24} />}
            title="还没有素材任务"
            hint="右上「生成卖点文案」选一个商品开始；任务请求内同步执行，回来即待抽检或失败。"
            action={
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreateOpen(true)}>
                <Plus aria-hidden size={13} weight="bold" />
                生成卖点文案
              </button>
            }
          />
        </div>
      ) : (
        <div className="panel overflow-x-auto">
          <table className="table-gov">
            <thead>
              <tr>
                <th className="w-24">任务 ID</th>
                <th className="w-36">商品</th>
                <th>标题</th>
                <th className="w-24">状态</th>
                <th className="w-20">产物</th>
                <th className="w-56">失败原因</th>
                <th className="w-44">创建时间</th>
                <th className="w-16"></th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id} className="row-click" onClick={() => setDetailId(task.id)}>
                  <td className="font-mono text-xs text-ink-3">{formatTaskId(task.id)}</td>
                  <td className="text-[13px] text-ink-2" title={`P-${task.product_id}`}>
                    {task.product_name}
                  </td>
                  <td className="max-w-[22rem] truncate text-[13px] text-ink">
                    {task.title ?? <span className="text-ink-3">—</span>}
                  </td>
                  <td>
                    <TaskStatusBadge status={task.status} />
                  </td>
                  <td>
                    {task.asset_id !== null ? (
                      <Link
                        to={`/platform/assets/${task.asset_id}`}
                        className="font-mono text-xs text-ink-2 underline-offset-2 hover:text-accent-strong hover:underline"
                        onClick={(e) => e.stopPropagation()}
                        title="查看登记出的素材资产"
                      >
                        {formatAssetId(task.asset_id)}
                      </Link>
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </td>
                  <td className="max-w-[14rem] truncate text-xs text-ink-3" title={task.last_error ?? undefined}>
                    {task.status === 'failed' ? <span className="text-danger">{task.last_error}</span> : '—'}
                  </td>
                  <td className="text-xs text-ink-3 tabular-nums">{formatDateTime(task.created_at)}</td>
                  <td>
                    {/* 行尾箭头 hover 才现（StaffDesk 手法，与资产列表同款） */}
                    <span className="row-caret flex items-center justify-end text-caption">
                      <CaretRight aria-hidden size={13} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CreateTaskDrawer open={createOpen} onClose={() => setCreateOpen(false)} onCreated={reload} />
      <TaskDetailDrawer task={detailTask} onClose={() => setDetailId(null)} onActioned={reload} />
    </div>
  )
}
