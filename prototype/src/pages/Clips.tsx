import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUDownLeft, CheckSquare, FilmSlate, Square } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Empty from '../components/Empty'
import { dispatch, useStore } from '../store/store'

// 直播切片：候选 → 人工拣选 → 登记进中台（已接入）。
// 拣选是人的动作；登记只是进入治理队列，发布仍是治理台的事。

export default function Clips() {
  const clips = useStore((s) => s.clips)
  const products = useStore((s) => s.products)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const toggle = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const registrable = [...selected].filter((id) => {
    const c = clips.find((x) => x.id === id)
    return c && !c.registeredAssetId
  })

  return (
    <div className="p-4 lg:p-6">
      <PageHeader
        title="直播切片"
        desc="候选不是资产。拣选时切开独立片段，登记为视频、已接入。源录像不进中台。素材中心用视图展示这些视频，不造生成任务。"
        actions={
          <>
            <span className="text-xs text-caption tabular-nums">
              已选 {selected.size} · 未登记 {clips.filter((c) => !c.registeredAssetId).length}
            </span>
            <button
              className="btn-primary"
              disabled={registrable.length === 0}
              onClick={() => {
                dispatch({ type: 'REGISTER_CLIPS', clipIds: registrable })
                setSelected(new Set())
              }}
            >
              <ArrowUDownLeft size={14} />
              拣选登记{registrable.length > 0 ? `（${registrable.length} 条）` : ''}
            </button>
          </>
        }
      >
        <div className="panel px-3.5 py-2.5 text-xs text-ink-3 leading-5">
          直播源：2026-09-04 「钛钢保温杯 × 饮用水」专场（录像 24 分钟，机洗切出 8 条候选）
        </div>
      </PageHeader>

      {clips.length === 0 ? (
        <div className="panel">
          <Empty icon={<FilmSlate size={28} />} title="没有候选切片" hint="直播结束后，机洗会自动切出候选片段。" />
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {clips.map((c) => {
            const product = products.find((p) => p.id === c.productId)
            const isSel = selected.has(c.id)
            return (
              <div
                key={c.id}
                className={`panel p-3 flex flex-col gap-2.5 transition-colors ${
                  isSel ? 'border-accent-border ring-1 ring-accent-border' : ''
                } ${c.registeredAssetId ? 'opacity-80' : 'cursor-pointer hover:border-line-3'}`}
                onClick={() => !c.registeredAssetId && toggle(c.id)}
              >
                <div className="flex items-center gap-2">
                  {!c.registeredAssetId && (
                    <span className={isSel ? 'text-accent-strong' : 'text-caption'}>
                      {isSel ? <CheckSquare size={17} weight="fill" /> : <Square size={17} />}
                    </span>
                  )}
                  <span className="font-mono text-xs text-caption tabular-nums">{c.id}</span>
                  <span className="badge-neutral">{c.topic}</span>
                  <span className="flex-1" />
                  <span className="font-mono text-xs text-caption tabular-nums">{c.duration}</span>
                </div>

                {/* 视频占位：灰阶画面 + 时间码，不用假截图 */}
                <div className="h-24 rounded-[4px] bg-fill-150/70 border border-line-3 flex items-center justify-center gap-2 text-ink-3">
                  <FilmSlate size={18} />
                  <span className="font-mono text-xs tabular-nums">{c.timecode}</span>
                </div>

                <p className="text-sm text-ink-2 leading-6 line-clamp-2">{c.transcript}</p>

                <div className="flex items-center gap-2 mt-auto">
                  <span className="text-xs text-caption font-mono tabular-nums">
                    {product?.id} {product?.name.split(' ')[0]}
                  </span>
                  <span className="flex-1" />
                  {c.registeredAssetId ? (
                    <Link
                      to={`/platform/assets/${c.registeredAssetId}`}
                      className="badge-ingested hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      已登记 {c.registeredAssetId}
                    </Link>
                  ) : (
                    <span className="text-xs text-caption">待拣选</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="mt-4 text-xs text-caption">
        登记后是种类=视频的中台资产（已接入）。未发布前客服检索不到。素材中心「切片汇入」列出这些视频，不另造任务。
      </div>
    </div>
  )
}
