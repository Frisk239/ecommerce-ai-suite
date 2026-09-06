import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Brain, DownloadSimple, ArrowsLeftRight } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Empty from '../components/Empty'
import Confirm from '../components/Confirm'
import { dispatch, useStore } from '../store/store'

// 模型微调：只从已发布导出（ADR 0004）；导出记录带资产 ID 列表（数据集血缘）；
// 训练本身不在原型里做，客服页的「基座 / 微调」开关消费导出结果。

export default function Finetune() {
  const allAssets = useStore((s) => s.assets)
  const exports = useStore((s) => s.exports)
  const trainingTasks = useStore((s) => s.trainingTasks)
  const pub = allAssets.filter((a) => a.publishedV != null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState(false)

  const toggle = (id: string) => {
    const next = new Set(picked)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setPicked(next)
  }

  return (
    <div className="p-4 lg:p-6 max-w-[900px]">
      <PageHeader
        title="模型微调"
        desc="当检索解决不了口吻与拒答边界时，从中台导出清洗后的数据做微调，再把适配后的底座送回客服。导出只含已发布资产。"
        actions={
          <button className="btn-primary" disabled={picked.size === 0} onClick={() => setConfirming(true)}>
            <DownloadSimple size={14} />
            导出微调集（{picked.size}）
          </button>
        }
      />

      <div className="grid lg:grid-cols-[1fr_300px] gap-4 items-start">
        <div className="panel min-w-0">
          <div className="px-4 py-2.5 border-b border-line-1 flex items-center gap-2">
            <div className="text-sm font-semibold text-ink">导出向导 · 选择资产</div>
            <span className="flex-1" />
            <span className="text-xs text-caption">只列出已发布（{pub.length} 条）</span>
          </div>
          {pub.length === 0 ? (
            <Empty
              title="没有可导出的已发布资产"
              hint="微调集只能由已发布资产构成。先到治理台完成发布。"
            />
          ) : (
            <table className="table-base">
              <thead>
                <tr>
                  <th className="w-10"></th>
                  <th className="w-24">资产 ID</th>
                  <th>标题</th>
                  <th className="w-20">种类</th>
                  <th className="w-20">版本</th>
                </tr>
              </thead>
              <tbody>
                {pub.map((a) => (
                  <tr key={a.id} className="cursor-pointer" onClick={() => toggle(a.id)}>
                    <td>
                      <input
                        type="checkbox"
                        className="cursor-pointer"
                        checked={picked.has(a.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggle(a.id)}
                      />
                    </td>
                    <td className="font-mono text-xs text-ink-3 tabular-nums">{a.id}</td>
                    <td className="text-ink">{a.title}</td>
                    <td className="text-xs text-ink-3">{a.kind}</td>
                    <td className="font-mono text-xs text-ink-3 tabular-nums">v{a.publishedV}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="px-4 py-2.5 border-t border-line-1 text-xs text-caption leading-5">
            待人洗与已接入资产不出现在这里：未发布内容不进训练集，避免把未经确认的口径教给模型。
          </div>
        </div>

        <div className="space-y-4">
          <div className="panel p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Brain size={15} className="text-accent-strong" />
              训练任务
            </div>
            {trainingTasks.length === 0 ? (
              <div className="mt-2.5 text-xs text-ink-3 leading-5">
                当前没有训练任务（原型不实现真实训练）。每次导出会生成一条 mock 训练任务，
                产物可注册为微调底座，再到客服切换对比口吻。
              </div>
            ) : (
              <div className="mt-2.5 space-y-2">
                {trainingTasks.map((t) => (
                  <div
                    key={t.id}
                    className="flex flex-wrap items-center gap-2 rounded-[6px] border border-line-2 px-3 py-2"
                  >
                    <span className="font-mono text-xs text-ink-3 tabular-nums">{t.id}</span>
                    <span className="text-sm text-ink">{t.modelName}</span>
                    <span className="badge-published">已完成</span>
                    <span className="text-xs text-caption">
                      来自导出 {t.exportId} · {t.assetCount} 条资产
                    </span>
                    <span className="flex-1" />
                    {t.registeredModelId ? (
                      <Link to="/models" className="badge-accent">
                        已注册 {t.registeredModelId}
                      </Link>
                    ) : (
                      <button
                        className="btn-ghost btn-sm"
                        onClick={() => dispatch({ type: 'REGISTER_TRAINED_MODEL', taskId: t.id })}
                      >
                        注册为微调底座
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="panel">
            <div className="px-4 py-2.5 border-b border-line-1 text-sm font-semibold text-ink">
              导出记录
            </div>
            {exports.length === 0 ? (
              <div className="px-4 py-3 text-xs text-caption">暂无导出记录。</div>
            ) : (
              exports.map((e) => (
                <div key={e.id} className="px-4 py-2.5 border-b border-line-1 last:border-b-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-3 tabular-nums">{e.id}</span>
                    <span className="text-xs text-caption tabular-nums">{e.createdAt}</span>
                    <span className="flex-1" />
                    <span className="tabular-nums text-xs text-ink-3">{e.size}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {e.refs.map((r) => (
                      // 芯片带导出时冻结的版本号：之后指针前移，记录仍指向当时的版本（ADR 0007）
                      <Link
                        key={r.assetId}
                        to={`/platform/assets/${r.assetId}?v=${r.v}`}
                        className="cite-chip"
                        title="版本号在导出时冻结"
                      >
                        {r.assetId} · v{r.v}
                      </Link>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <Confirm
        open={confirming}
        title={`导出 ${picked.size} 条资产的微调集？`}
        body={
          <>
            导出包只包含所选已发布资产的当前版本，记录会保留完整资产 ID 列表作为数据集血缘。
            导出不改变资产状态。
          </>
        }
        confirmLabel="确认导出"
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          dispatch({ type: 'CREATE_EXPORT', assetIds: [...picked] })
          setPicked(new Set())
          setConfirming(false)
        }}
      />

      <div className="mt-4 flex items-center gap-1.5 text-xs text-caption">
        <ArrowsLeftRight size={13} />
        闭环：检索不够好 → 这里导出 → 训练 → 客服切「微调」→ 效果对比。
      </div>
    </div>
  )
}
