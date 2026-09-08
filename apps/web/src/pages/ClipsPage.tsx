// 直播切片（第 18 刀/ADR 0014/0015/0039）：候选卡片网格 → 勾选拣选 → 批量登记。
// 候选不是资产（0014）：勾选只在前端，点「拣选登记（N 条）」才把转写字节登记为
// 种类=视频/来源=切片拣选的中台资产（已接入→待人洗）。单向状态机（0039）：
// 已登记卡不再出勾选框，只给「已登记 A-xxxx」链跳治理台详情——不可撤销不可重切。
// 交互状态对照原型 Clips.tsx 冻结口径（勾选态/批量按钮/登记后卡换徽章/单向）。

import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUDownLeft, CheckSquare, FilmSlate, Square, Warning } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { ClipCandidate } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatAssetId, formatClipId } from '../labels'
import { ErrorBanner } from '../components/Banner'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'

const EMPTY_CLIPS = [] as const

function formatTimecodeRange(c: ClipCandidate): string {
  return `${c.timecode_start}-${c.timecode_end}`
}

export default function ClipsPage() {
  const fetcher = useCallback(() => api.listClipCandidates(), [])
  const { state, reload } = useApiData(fetcher)
  const clips = state.phase === 'ok' ? state.data : EMPTY_CLIPS

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  // 单向：勾选只对 pending 有意义；reload 后已登记/被移除的选中项自动出列
  const registrable = clips.filter((c) => c.status === 'pending' && selected.has(c.id))
  const pendingCount = clips.filter((c) => c.status === 'pending').length

  const toggle = (candidate: ClipCandidate) => {
    if (candidate.status !== 'pending' || submitting) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(candidate.id)) next.delete(candidate.id)
      else next.add(candidate.id)
      return next
    })
  }

  const pick = async () => {
    if (submitting || registrable.length === 0) return
    setSubmitting(true)
    setActionError(null)
    try {
      await api.pickClips(registrable.map((c) => c.id))
      setSelected(new Set())
      reload()
    } catch (err) {
      setActionError(detailText(err))
    } finally {
      setSubmitting(false)
    }
  }

  const header = clips[0]?.source_video_label

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
        title="业务能力 · 直播切片"
        desc="候选不是资产，拣选才登记：勾选候选后一次登记 N 条，独立写入对象存储，成为种类=视频、来源=切片拣选的中台资产（已接入→待人洗）。源录像不进中台，登记也不造任务；发布仍在治理台。"
        actions={
          <>
            <span className="text-xs tabular-nums text-caption">
              已选 {registrable.length} · 未登记 {pendingCount}
            </span>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void pick()}
              disabled={registrable.length === 0 || submitting}
            >
              <ArrowUDownLeft aria-hidden size={14} />
              {submitting ? '登记中…' : `拣选登记${registrable.length > 0 ? `（${registrable.length} 条）` : ''}`}
            </button>
          </>
        }
      >
        {header ? (
          <div className="panel px-3.5 py-2.5 text-xs leading-5 text-ink-3">{`直播源：${header}（v1 候选为种子 mock，自动切出/ASR 留部署刀）`}</div>
        ) : null}
      </PageHeader>

      {state.phase === 'loading' ? (
        <div className="panel">
          <SkeletonRows rows={6} />
        </div>
      ) : state.phase === 'error' ? (
        <>
          <ErrorBanner error={state.error} onRetry={reload} />
          <div className="panel">
            <Empty icon={<Warning aria-hidden size={26} />} title="切片候选加载失败" hint="上面的横幅可重试。" />
          </div>
        </>
      ) : clips.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<FilmSlate aria-hidden size={24} />}
            title="没有候选切片"
            hint="直播结束后由机洗切出候选（v1 由启动种子灌入 mock 候选）。"
          />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {clips.map((c) => {
            const isSel = c.status === 'pending' && selected.has(c.id)
            const assetId = c.registered_asset_id
            const registered = c.status === 'registered' && assetId !== null
            return (
              <div
                key={c.id}
                className={`panel flex flex-col gap-2.5 p-3 transition-colors ${
                  isSel ? 'border-accent-border ring-1 ring-accent-border' : ''
                } ${registered ? 'opacity-80' : 'cursor-pointer hover:border-line-4'} ${
                  submitting && !registered ? 'pointer-events-none' : ''
                }`}
                role={registered ? undefined : 'button'}
                aria-pressed={registered ? undefined : isSel}
                onClick={() => toggle(c)}
              >
                <div className="flex items-center gap-2">
                  {!registered &&
                    (isSel ? (
                      <CheckSquare aria-hidden size={17} weight="fill" className="shrink-0 text-accent-strong" />
                    ) : (
                      <Square aria-hidden size={17} className="shrink-0 text-caption" />
                    ))}
                  <span className="font-mono text-xs tabular-nums text-caption">{formatClipId(c.id)}</span>
                  <span className="kind-chip">{c.product_name}</span>
                  <span className="flex-1" />
                  <span className="font-mono text-xs tabular-nums text-caption">{formatTimecodeRange(c)}</span>
                </div>

                {/* 视频占位：灰阶画面 + 时间码，不用假截图（0039：v1 没有视频字节） */}
                <div className="flex h-24 items-center justify-center gap-2 rounded-[4px] border border-line-3 bg-fill/70 text-ink-3">
                  <FilmSlate aria-hidden size={18} />
                  <span className="font-mono text-xs tabular-nums">{formatTimecodeRange(c)}</span>
                </div>

                <p className="line-clamp-2 text-sm leading-6 text-ink-2" title={c.transcript}>
                  {c.transcript}
                </p>

                <div className="mt-auto flex items-center gap-2">
                  <span className="font-mono text-xs tabular-nums text-caption">
                    {`P-${String(c.product_id).padStart(4, '0')}`}
                  </span>
                  <span className="flex-1" />
                  {registered ? (
                    <Link
                      to={`/platform/assets/${c.registered_asset_id}`}
                      className="badge badge-published transition-colors duration-150 hover:brightness-110"
                      onClick={(e) => e.stopPropagation()}
                      title="查看登记出的视频资产（治理台）"
                    >
                      已登记 {formatAssetId(assetId)}
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

      <div className="mt-4 text-xs leading-5 text-caption">
        登记后是种类=视频的中台资产（待人洗）。未发布前客服检索不到；发布后可被引用。素材中心「切片汇入」列出这些视频，不另造任务。
      </div>
    </div>
  )
}
