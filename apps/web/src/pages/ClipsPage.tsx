// 直播切片（第 18 刀/ADR 0014/0015/0039；第 46 刀真链路）：候选卡片网格 →
// 勾选拣选 → 批量登记。候选不是资产（0014）：勾选只在前端，点「拣选登记（N 条）」
// 才把字节登记为种类=视频/来源=切片拣选的中台资产（已接入→待人洗）。
// 第 46 刀：上传 .mp4 源录像（≤200MB，上传即绑「尚无源录像」的待拣候选），
// 有源录像的候选拣选时真切出 mp4 片段、转写预置为版本字段；无源录像的仍写
// 时间码转写文本（旧路径）。录像是切片模块自有的输入源，不是中台资产。
// 单向状态机（0039）：已登记卡不再出勾选框，只给「已登记 A-xxxx」链跳治理台
// 详情——不可撤销不可重切。交互状态对照原型 Clips.tsx 冻结口径（勾选态/批量
// 按钮/登记后卡换徽章/单向），视觉照 UX-NOTES §二点七（实色、无 hover 位移）。

import { useCallback, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUDownLeft,
  CheckSquare,
  FilmSlate,
  Square,
  UploadSimple,
  Warning,
} from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { ClipCandidate, ClipRecording } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatAssetId, formatClipId } from '../labels'
import { ErrorBanner, SuccessBanner } from '../components/Banner'
import ActionError from '../components/ActionError'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'

const EMPTY_CLIPS = [] as const

function formatTimecodeRange(c: ClipCandidate): string {
  return `${c.timecode_start}-${c.timecode_end}`
}

/** 字节数展示（源录像大小）：MB/一位小数，够读即可。 */
function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

/** 当前源录像=**待拣候选**里 created_at 最新的那份（裁决 2：上传绑的是待拣候选；
 * 已登记候选的绑定是历史事实，不代表「现在拣选会从哪切」；多源不同绑时显示最新）。 */
function latestRecording(clips: readonly ClipCandidate[]): ClipRecording | null {
  let latest: ClipRecording | null = null
  for (const c of clips) {
    if (c.status !== 'pending' || c.recording === null) continue
    if (latest === null || c.recording.created_at >= latest.created_at) {
      latest = c.recording
    }
  }
  return latest
}

export default function ClipsPage() {
  const fetcher = useCallback(() => api.listClipCandidates(), [])
  const { state, reload } = useApiData(fetcher)
  const clips = state.phase === 'ok' ? state.data : EMPTY_CLIPS

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [uploadNote, setUploadNote] = useState<string | null>(null)
  const [pickNote, setPickNote] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 单向：勾选只对 pending 有意义；reload 后已登记/被移除的选中项自动出列
  const registrable = clips.filter((c) => c.status === 'pending' && selected.has(c.id))
  const pendingCount = clips.filter((c) => c.status === 'pending').length
  const bindableCount = clips.filter((c) => c.status === 'pending' && c.recording === null).length
  const currentRecording = latestRecording(clips)

  const toggle = (candidate: ClipCandidate) => {
    if (candidate.status !== 'pending' || submitting) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(candidate.id)) next.delete(candidate.id)
      else next.add(candidate.id)
      return next
    })
  }

  const pickRecordingFile = () => {
    if (uploading) return
    fileInputRef.current?.click()
  }

  const onRecordingPicked = async (files: FileList | null) => {
    const file = files?.[0] ?? null
    if (file === null || uploading) return
    setActionError(null)
    setUploadNote(null)
    setPickNote(null)
    // 绑定数=上传前「pending 且尚无源录像」的候选数（上传即绑这些；已登记不追改）
    const bound = bindableCount
    setUploading(true)
    try {
      const recording = await api.uploadClipRecording(file)
      setUploadNote(
        bound > 0
          ? `已上传源录像《${recording.label}》（${formatBytes(recording.size_bytes)}），已绑定 ${bound} 条待拣候选；勾选后拣选即从源录像切出真 mp4 片段。`
          : `已上传源录像《${recording.label}》（${formatBytes(recording.size_bytes)}）；当前没有「尚无源录像」的待拣候选可绑定（已登记/已绑定的候选不追改）。`,
      )
      reload()
    } catch (err) {
      setActionError(detailText(err))
    } finally {
      setUploading(false)
      // 清空 input 值：同一文件再次选择也能触发 change
      if (fileInputRef.current !== null) fileInputRef.current.value = ''
    }
  }

  const pick = async () => {
    if (submitting || registrable.length === 0) return
    setSubmitting(true)
    setActionError(null)
    setPickNote(null)
    setUploadNote(null)
    // 回执口径按「勾选候选是否绑了源录像」分：真切片段 vs 时间码文本旧路径
    const realCount = registrable.filter((c) => c.recording !== null).length
    const textCount = registrable.length - realCount
    try {
      await api.pickClips(registrable.map((c) => c.id))
      setSelected(new Set())
      if (realCount > 0 && textCount > 0) {
        setPickNote(
          `已登记 ${registrable.length} 条：${realCount} 条已从源录像切出真 mp4 片段、${textCount} 条无源录像走时间码转写文本（待人洗）。`,
        )
      } else if (realCount > 0) {
        setPickNote(
          `已登记 ${registrable.length} 条：均已从源录像切出真 mp4 片段，转写已预置为可检索字段（待人洗）。`,
        )
      } else {
        setPickNote(`已登记 ${registrable.length} 条：本次无源录像，仍写时间码转写文本（待人洗）。`)
      }
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
      {actionError ? <ActionError message={actionError} variant="prominent" className="mb-4" /> : null}
      {uploadNote ? <SuccessBanner>{uploadNote}</SuccessBanner> : null}
      {pickNote ? <SuccessBanner>{pickNote}</SuccessBanner> : null}

      <PageHeader
        title="直播切片"
        desc="上传源录像后拣选即切出真 mp4 片段；发布仍在治理台。"
        actions={
          <>
            <span className="text-xs tabular-nums text-caption">
              已选 {registrable.length} · 未登记 {pendingCount}
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".mp4,video/mp4"
              className="hidden"
              onChange={(e) => void onRecordingPicked(e.target.files)}
            />
            <button
              type="button"
              className="btn btn-secondary"
              onClick={pickRecordingFile}
              disabled={uploading}
            >
              <UploadSimple aria-hidden size={14} />
              {uploading ? '上传中…' : '上传源录像'}
            </button>
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
        <div className="space-y-1.5 text-xs leading-5 text-ink-3">
          {header ? (
            <div className="panel px-3.5 py-2.5">{`直播源：${header}`}</div>
          ) : null}
          <div className="panel px-3.5 py-2.5">
            {currentRecording !== null
              ? `当前源录像：《${currentRecording.label}》（${formatBytes(currentRecording.size_bytes)}）— 拣选将从中切出真 mp4 片段`
              : '尚无源录像：拣选仍登记时间码转写文本。上传 .mp4 源录像可切出真片段。'}
            {currentRecording !== null && bindableCount > 0
              ? ` · 另有 ${bindableCount} 条待拣候选尚无源录像`
              : ''}
          </div>
        </div>
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
            hint="直播结束后切出候选（自动切出/ASR 留部署刀；v1 由启动种子灌入 mock 候选）。"
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

                {/* 视频占位：灰阶画面 + 时间码，不用假截图（0039：候选不是视频字节） */}
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
                    <span className="text-xs text-caption">
                      {c.recording !== null ? '有源录像·可真切' : '待拣选'}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="mt-4 text-xs leading-5 text-caption">
        登记后是种类=视频的中台资产（待人洗）。有源录像的候选登记的是切出的真 mp4 片段，转写作为版本字段供检索；无源录像仍是时间码文本。未发布前客服检索不到；发布后可被引用。素材中心「切片汇入」列出这些视频，不另造任务。
      </div>
    </div>
  )
}
