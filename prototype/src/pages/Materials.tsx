import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUDownLeft, CheckCircle, ImageSquare, Plus, X } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import CitationChip from '../components/CitationChip'
import Empty from '../components/Empty'
import { dispatch, useStore } from '../store/store'

// 素材中心：生成任务读中台商品与卖点，成品「登记」回中台（已接入）。
// 登记不等于发布：客服还不能用它，要在治理台人洗发布。

function NewTaskForm({ onClose }: { onClose: () => void }) {
  const products = useStore((s) => s.products)
  const [productId, setProductId] = useState(products[0]?.id ?? '')
  const [brief, setBrief] = useState('')

  const submit = () => {
    if (!productId || !brief.trim()) return
    dispatch({ type: 'CREATE_MATERIAL_TASK', productId, brief: brief.trim() })
    onClose()
  }

  return (
    <div className="panel p-4 mb-4 space-y-2.5">
      <div className="flex items-center gap-2">
        <div className="text-sm font-semibold text-ink">新建生成任务</div>
        <span className="text-xs text-caption">成品会引用已发布资产作为生成依据</span>
        <span className="flex-1" />
        <button className="btn-ghost btn-sm" onClick={onClose} aria-label="关闭新建表单">
          <X size={13} />
        </button>
      </div>
      <div className="grid sm:grid-cols-2 gap-2">
        <label className="block">
          <span className="text-xs text-ink-3">商品</span>
          <select
            className="input w-full mt-1"
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
          >
            {products.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id} {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-ink-3">任务说明（brief）</span>
          <input
            className="input w-full mt-1"
            placeholder="如：保温杯 · 种草图文（小红书）"
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
          />
        </label>
      </div>
      <div className="flex justify-end gap-2">
        <button className="btn-ghost btn-sm" onClick={onClose}>
          取消
        </button>
        <button className="btn-primary btn-sm" onClick={submit} disabled={!brief.trim()}>
          创建任务
        </button>
      </div>
    </div>
  )
}

export default function Materials() {
  const tasks = useStore((s) => s.materialTasks)
  const products = useStore((s) => s.products)
  const allAssets = useStore((s) => s.assets)
  const clipAssets = allAssets.filter((a) => a.kind === '视频' && a.sourceKind === '切片拣选')
  const [creating, setCreating] = useState(false)

  return (
    <div className="p-4 lg:p-6">
      {creating && <NewTaskForm onClose={() => setCreating(false)} />}
      <PageHeader
        title="素材中心"
        desc="生成任务须质检过线才登记。切片汇入是已登记视频资产的视图，不是生成任务。"
        actions={
          <button className="btn-primary" onClick={() => setCreating(true)}>
            <Plus size={14} />
            新建生成任务
          </button>
        }
      />

      {tasks.length === 0 ? (
        <div className="panel">
          <Empty
            icon={<ImageSquare size={28} />}
            title="暂无生成任务"
            hint="点右上「新建生成任务」发起，或由运营 Agent 编排时自动创建。"
          />
        </div>
      ) : (
        <div className="space-y-4 max-w-[900px]">
          {tasks.map((t) => {
            const product = products.find((p) => p.id === t.productId)
            // 登记状态跟中台资产实时联动：登记只是入口，治理后的状态回流到这张卡
            const regAsset = t.registeredAssetId
              ? allAssets.find((a) => a.id === t.registeredAssetId)
              : undefined
            return (
              <div key={t.id} className="panel">
                <div className="flex flex-wrap items-center gap-2.5 px-4 py-3 border-b border-line-1">
                  <span className="font-mono text-xs text-caption tabular-nums">{t.id}</span>
                  <span className="text-sm font-semibold text-ink">{t.brief}</span>
                  {t.status === '已完成' ? (
                    <span className="badge-published">
                      <CheckCircle size={11} weight="fill" />
                      质检通过
                    </span>
                  ) : t.status === '待质检' ? (
                    <span className="badge-review">待质检</span>
                  ) : t.status === '已打回' ? (
                    <span className="badge-failed">已打回</span>
                  ) : (
                    <span className="badge-review">生成中</span>
                  )}
                  {t.origin === 'ops' && <span className="badge-neutral">运营发起</span>}
                  <span className="flex-1" />
                  <span className="text-xs text-caption font-mono tabular-nums">
                    {product?.id} {product?.name}
                  </span>
                </div>

                {t.output && (
                  <div className="px-4 py-3.5">
                    <div className="text-base font-medium text-ink">{t.output.title}</div>
                    <p className="mt-1.5 text-sm text-ink-2 leading-7 max-w-[70ch]">
                      {t.output.body}
                    </p>
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                      <span className="text-xs text-caption">生成依据（已发布引用）：</span>
                      {t.output.refs.map((r, i) => (
                        <CitationChip key={i} assetId={r.assetId} v={r.v} />
                      ))}
                    </div>
                  </div>
                )}

                <div className="px-4 py-3 border-t border-line-1 flex flex-wrap items-center gap-3">
                  {t.registeredAssetId ? (
                    <>
                      {regAsset?.publishedV != null ? (
                        <span className="badge-published">
                          已发布 · v{regAsset.publishedV}
                        </span>
                      ) : (
                        <span className="badge-ingested">
                          已登记 · {regAsset?.state ?? '已接入'}
                        </span>
                      )}
                      <Link
                        to={`/platform/assets/${t.registeredAssetId}`}
                        className="text-sm font-mono text-accent-strong hover:underline"
                      >
                        {t.registeredAssetId}
                      </Link>
                      <span className="text-xs text-caption">
                        {regAsset?.publishedV != null
                          ? '客服检索已可用；正文以该版本为准。'
                          : '未发布，客服还不能用；到治理台人洗发布后进入检索。'}
                      </span>
                    </>
                  ) : t.status === '待质检' ? (
                    <>
                      <button
                        className="btn-primary"
                        onClick={() => dispatch({ type: 'QC_PASS', taskId: t.id })}
                      >
                        抽检通过
                      </button>
                      <button
                        className="btn-ghost btn-sm"
                        onClick={() => dispatch({ type: 'QC_REJECT', taskId: t.id })}
                      >
                        打回
                      </button>
                      <span className="text-xs text-caption">过线才登记。打回则不进中台。</span>
                    </>
                  ) : t.status === '已打回' ? (
                    <>
                      <button
                        className="btn-ghost btn-sm"
                        onClick={() => dispatch({ type: 'QC_PASS', taskId: t.id })}
                      >
                        改判通过
                      </button>
                      <span className="text-xs text-caption">已打回，未登记。</span>
                    </>
                  ) : (
                    <>
                      <button
                        className="btn-primary"
                        disabled={t.status !== '已完成'}
                        onClick={() => dispatch({ type: 'REGISTER_MATERIAL', taskId: t.id })}
                      >
                        <ArrowUDownLeft size={14} />
                        登记到中台
                      </button>
                      <span className="text-xs text-caption">
                        登记后进入已接入，等待治理；不直接发布。
                      </span>
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="mt-8">
        <div className="text-sm font-semibold text-ink mb-2">直播切片汇入</div>
        <p className="text-xs text-caption mb-3">
          来源=切片拣选、种类=视频的已登记资产。不是生成任务。运营只引用其中已发布的。
        </p>
        {clipAssets.length === 0 ? (
          <div className="panel px-4 py-3 text-sm text-caption">还没有拣选登记的视频资产。</div>
        ) : (
          <div className="panel overflow-hidden">
            <ul className="divide-y divide-line-1">
              {clipAssets.map((a) => (
                <li key={a.id} className="px-4 py-2.5 flex items-center gap-2 text-sm">
                  <Link to={`/platform/assets/${a.id}`} className="font-mono text-accent-strong hover:underline">
                    {a.id}
                  </Link>
                  <span className="text-ink">{a.title}</span>
                  <span className="flex-1" />
                  <span className="text-xs text-caption">{a.state}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
