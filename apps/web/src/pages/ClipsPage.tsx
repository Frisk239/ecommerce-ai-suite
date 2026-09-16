// 直播切片（第 18 刀/ADR 0014/0015/0039；第 46 刀真链路；第 93 刀云转写）：候选卡片
// 网格 → 勾选拣选 → 批量登记。候选不是资产（0014）：勾选只在前端，点「拣选登记（N 条）」
// 才把字节登记为种类=视频/来源=切片拣选的资产（已接入→待人洗）。
// 第 46 刀：上传 .mp4 源录像（≤200MB，上传即绑「尚无源录像」的待拣候选），
// 有源录像的候选拣选时真切出 mp4 片段、转写预置为版本字段；无源录像的仍写
// 时间码转写文本（旧路径）。录像是切片模块自有的输入源，不是中台资产。
// 第 93 刀：对选中的源录像点「自动转写」——云 ASR 按停顿聚合出候选落 pending
// （人工拣选闸门保留）；无 key 时按钮禁用并说明（后端也 409，前端只是提前告知）。
// 单向状态机（0039）：已登记卡不再出勾选框，只给「已登记 A-xxxx」链跳治理台
// 详情——不可撤销不可重切。交互状态对照原型 Clips.tsx 冻结口径（勾选态/批量
// 按钮/登记后卡换徽章/单向），视觉照 UX-NOTES §二点七（实色、无 hover 位移）。

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUDownLeft,
  CheckSquare,
  FilmSlate,
  Square,
  UploadSimple,
  Warning,
  Waveform,
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

/** 转写来源标注（第 93 刀，只读）：来源是既成事实，UI 只呈现不提供修改。 */
const TRANSCRIPT_SOURCE_LABELS: Record<string, string> = {
  cloud: '云转写',
  local: '本地转写',
  manual: '人工/导入',
}

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
  const [bindNote, setBindNote] = useState<string | null>(null)
  // 第 49 刀：源录像列表 + 选择器（「改绑到哪一份」得先看得见有哪些份）
  const [recordings, setRecordings] = useState<ClipRecording[]>([])
  const [chosenRecordingId, setChosenRecordingId] = useState<number | null>(null)
  const [binding, setBinding] = useState(false)
  // 第 93 刀：自动转写（选中的源录像 → 云 ASR 候选）与 ASR 配置状态
  const [transcribing, setTranscribing] = useState(false)
  const [transcribeNote, setTranscribeNote] = useState<string | null>(null)
  const [asrConfigured, setAsrConfigured] = useState<boolean | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 单向：勾选只对 pending 有意义；reload 后已登记/被移除的选中项自动出列
  const registrable = clips.filter((c) => c.status === 'pending' && selected.has(c.id))
  const pendingCount = clips.filter((c) => c.status === 'pending').length
  const currentRecording = latestRecording(clips)

  // ASR 配置状态（第 93 刀）：拉不到就按「未知」处理——按钮仍可点，由后端 409
  // 给准确文案（前端不做假的禁用判断）
  useEffect(() => {
    let alive = true
    void api
      .getClipAsrStatus()
      .then((status) => {
        if (alive) setAsrConfigured(status.configured)
      })
      .catch(() => {
        if (alive) setAsrConfigured(null)
      })
    return () => {
      alive = false
    }
  }, [])

  // 录像列表随页加载（一次性）：选择器默认选最新一份；上传后也会重拉（见下）
  useEffect(() => {
    let alive = true
    void api
      .listClipRecordings()
      .then((rows) => {
        if (!alive) return
        // 按 id 合并而不是整体替换：这次拉取可能在上传之后才回来（竞态），
        // 整体替换会把刚上传的那份从选择器里抹掉
        setRecordings((prev) => {
          const byId = new Map(prev.map((r) => [r.id, r]))
          for (const row of rows) if (!byId.has(row.id)) byId.set(row.id, row)
          return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
        })
        setChosenRecordingId((prev) => prev ?? rows[0]?.id ?? null)
      })
      .catch(() => {
        // 列表拉不到不挡主流程（页头仍有「当前源录像」一行）；改绑按钮会提示重试
      })
    return () => {
      alive = false
    }
  }, [])

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
    setBindNote(null)
    setUploading(true)
    try {
      const recording = await api.uploadClipRecording(file)
      // 绑定条数用**后端回执的真值**（bound_count），不再拿前端上传前的候选数猜
      const bound = recording.bound_count ?? 0
      setUploadNote(
        bound > 0
          ? `已上传源录像《${recording.label}》（${formatBytes(recording.size_bytes)}），已绑定 ${bound} 条待拣候选；勾选后拣选即从源录像切出真 mp4 片段。`
          : `已上传源录像《${recording.label}》（${formatBytes(recording.size_bytes)}）；没有「尚无源录像」的待拣候选可顺手绑定。要用它切片段，在下面把待拣候选改绑到这一份。`,
      )
      setRecordings((prev) => [recording, ...prev.filter((r) => r.id !== recording.id)])
      setChosenRecordingId(recording.id)
      reload()
    } catch (err) {
      setActionError(detailText(err))
    } finally {
      setUploading(false)
      // 清空 input 值：同一文件再次选择也能触发 change
      if (fileInputRef.current !== null) fileInputRef.current.value = ''
    }
  }

  // 改绑（第 49 刀）：勾了候选就只改勾选的，没勾就改全部待拣；已登记候选不动
  // （后端 409，文案由 detailText 呈现）。
  const rebind = async () => {
    if (binding || chosenRecordingId === null) return
    const ids = registrable.length > 0 ? registrable.map((c) => c.id) : null
    setBinding(true)
    setActionError(null)
    setBindNote(null)
    setUploadNote(null)
    try {
      const result = await api.bindClipRecording(chosenRecordingId, ids)
      setBindNote(
        result.bound_count > 0
          ? `已把 ${result.bound_count} 条待拣候选改绑到《${result.label}》；现在拣选就从这一份切出真 mp4 片段。`
          : `《${result.label}》没有可改绑的待拣候选（当前都是已登记候选）。`,
      )
      reload()
    } catch (err) {
      setActionError(detailText(err))
    } finally {
      setBinding(false)
    }
  }

  // 自动转写（第 93 刀）：对选中的源录像跑云 ASR → 停顿聚合候选落 pending。
  // 回执用后端真值（candidates_created/segments/note），错误文案由 detailText 呈现
  // （无 key 409/无音轨 422/已有未拣选候选 409 都自带可行动说明）。
  const transcribe = async () => {
    if (transcribing || chosenRecordingId === null) return
    setTranscribing(true)
    setActionError(null)
    setTranscribeNote(null)
    setUploadNote(null)
    setBindNote(null)
    setPickNote(null)
    try {
      const result = await api.transcribeClipRecording(chosenRecordingId)
      const label = recordings.find((r) => r.id === chosenRecordingId)?.label ?? '所选录像'
      const note = result.note === null ? '' : ` ${result.note}`
      if (result.candidates_created > 0) {
        setTranscribeNote(
          `已从《${label}》自动转写出 ${result.candidates_created} 条候选（云 ASR 句级时间戳按停顿聚合，${result.segments} 句；待人拣选）。${note}`,
        )
      } else {
        setTranscribeNote(`《${label}》：${result.note ?? '没有识别到语音'}。`)
      }
      reload()
    } catch (err) {
      setActionError(detailText(err))
    } finally {
      setTranscribing(false)
    }
  }

  const pick = async () => {
    if (submitting || registrable.length === 0) return
    setSubmitting(true)
    setActionError(null)
    setPickNote(null)
    setUploadNote(null)
    setBindNote(null)
    // 回执口径按「勾选候选是否绑了源录像」分：真切片段 vs 时间码文本旧路径；
    // 真切的那几段还要**指名切自哪一份**（审计刀 9：回执不指名来源，用户没法核对）
    const realOnes = registrable.filter((c) => c.recording !== null)
    const realCount = realOnes.length
    const textCount = registrable.length - realCount
    const sourceLabels = [
      ...new Set(realOnes.map((c) => c.recording?.label ?? '').filter((label) => label !== '')),
    ]
    const sourceText =
      sourceLabels.length === 1 ? `《${sourceLabels[0]}》` : `${sourceLabels.length} 份源录像`
    try {
      await api.pickClips(registrable.map((c) => c.id))
      setSelected(new Set())
      if (realCount > 0 && textCount > 0) {
        setPickNote(
          `已登记 ${registrable.length} 条：${realCount} 条已从${sourceText}切出真 mp4 片段、${textCount} 条无源录像走时间码转写文本（待人洗）。`,
        )
      } else if (realCount > 0) {
        setPickNote(
          `已登记 ${registrable.length} 条：均已从${sourceText}切出真 mp4 片段，转写已预置为可检索字段（待人洗）。`,
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
      {transcribeNote ? <SuccessBanner>{transcribeNote}</SuccessBanner> : null}
      {pickNote ? <SuccessBanner>{pickNote}</SuccessBanner> : null}
      {bindNote ? <SuccessBanner>{bindNote}</SuccessBanner> : null}

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
            {pendingCount > 0 ? ` · ${pendingCount} 条待拣候选` : ''}
          </div>
          {/* 第 49 刀：改绑——源录像不再是「绑错就锁死」。勾了候选改勾选的，
              没勾改全部待拣；已登记候选一律不动（后端 409）。
              第 93 刀：同一行给「自动转写」——对选中的这一份录像跑云 ASR。 */}
          {recordings.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-line-1 px-3.5 py-2">
              <span className="text-xs text-ink-3">改绑待拣候选到</span>
              <select
                className="input h-7 max-w-64 text-[12px]"
                value={chosenRecordingId ?? ''}
                disabled={binding || submitting || transcribing}
                onChange={(e) => setChosenRecordingId(Number(e.target.value))}
              >
                {recordings.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.label}（{formatBytes(r.size_bytes)}）
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => void rebind()}
                disabled={binding || submitting || transcribing || chosenRecordingId === null}
                title="把待拣候选的源录像改成这一份；已登记的候选不动"
              >
                <ArrowUDownLeft aria-hidden size={13} />
                {binding
                  ? '改绑中…'
                  : registrable.length > 0
                    ? `改绑勾选的 ${registrable.length} 条`
                    : '改绑全部待拣'}
              </button>
              <span className="flex-1" />
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => void transcribe()}
                disabled={
                  transcribing ||
                  binding ||
                  submitting ||
                  asrConfigured === false ||
                  chosenRecordingId === null
                }
                title={
                  asrConfigured === false
                    ? '未配置 ASR_API_KEY：自动转写不可用（可人工填写转写，或跑 scripts/transcribe_local.py 本地兜底）'
                    : '对选中的源录像跑云 ASR，按停顿切句生成待拣候选'
                }
              >
                <Waveform aria-hidden size={13} />
                {transcribing ? '转写中…' : '自动转写'}
              </button>
              {asrConfigured === false ? (
                <span className="text-xs text-caption">
                  未配置 ASR_API_KEY：自动转写禁用（后端也 409）
                </span>
              ) : null}
            </div>
          ) : null}
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
                    {c.product_id === null ? '未归属商品' : `P-${String(c.product_id).padStart(4, '0')}`}
                  </span>
                  {/* 转写来源（第 93 刀）：只读标注，来源是既成事实 */}
                  <span
                    className="badge badge-ingested"
                    title={`转写来源：${TRANSCRIPT_SOURCE_LABELS[c.transcript_source] ?? c.transcript_source}（只读，来源是既成事实）`}
                  >
                    {TRANSCRIPT_SOURCE_LABELS[c.transcript_source] ?? c.transcript_source}
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
                    <span
                      className="max-w-[55%] truncate text-xs text-caption"
                      title={`源录像：${c.source_video_label}${c.recording !== null ? `（已上传：${c.recording.label}）` : '（尚未上传录像，拣选走时间码文本）'}`}
                    >
                      {c.source_video_label}
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
