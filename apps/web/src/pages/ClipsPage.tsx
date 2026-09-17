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
// 第 114 刀 C（W11/W13/W14）：绑了源录像的候选卡出**真画面帧**（0039 的「不用
// 假截图」只对无字节的 demo 候选仍成立）；状态/源录像/关键词三维筛选；卡片角
// 「详情」抽屉（不抢勾选手势——点击卡片仍是拣选工作台的高频动作）。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUDownLeft,
  CheckSquare,
  FilmSlate,
  Info,
  MagnifyingGlass,
  Square,
  UploadSimple,
  Warning,
  Waveform,
  X,
} from '@phosphor-icons/react'
import { clipFrameUrl, detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { ClipCandidate, ClipRecording } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { useEscapeClose } from '../hooks/useEscapeClose'
import { formatAssetId, formatClipId } from '../labels'
import { matchesAllTerms, splitTerms } from '../search'
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

/** 拣选回执链接条数上限：批量拣选（勾 20 条也常见）不许把横幅撑成链接墙，
 * 超出的按条数收尾——全部资产仍可在下面的已登记卡片上逐条点开。 */
const PICK_RECEIPT_LINK_CAP = 6

/** 拣选回执（第 112 刀 W12）：文案 + 登记出资产的「查看 A-xxxx」内联链接。
 * 后端 pickClips 的回执**就是**登记出的资产列表（含 id），这里直接拿来回链，
 * 不再靠「回执文案里念一句资产号」（此前只有已登记候选卡有链，回执没有）。 */
function PickReceipt({ note, assetIds }: { note: string; assetIds: number[] }) {
  const shown = assetIds.slice(0, PICK_RECEIPT_LINK_CAP)
  const rest = assetIds.length - shown.length
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span>{note}</span>
      {shown.map((id) => (
        <Link
          key={id}
          to={`/platform/assets/${id}`}
          className="shrink-0 font-mono underline"
          title="查看登记出的视频资产（治理台补转写/发布）"
        >
          查看 {formatAssetId(id)}
        </Link>
      ))}
      {rest > 0 ? <span className="shrink-0 font-mono">…另 {rest} 条</span> : null}
    </span>
  )
}

/** 候选画面占位框：无源录像（demo 候选）或帧加载失败时用——0039「不用假截图」
 * 对没有视频字节的候选仍是诚实设计（有帧可抽的走 CandidateFrame 真图）。 */
function FramePlaceholder({ candidate, tall = false }: { candidate: ClipCandidate; tall?: boolean }) {
  return (
    <div
      className={`flex ${tall ? 'h-48' : 'h-24'} items-center justify-center gap-2 rounded-[4px] border border-line-3 bg-fill/70 text-ink-3`}
      title={
        candidate.recording === null
          ? 'demo 候选没有视频字节：占位不造假（0039）'
          : '帧加载失败，可稍后刷新重试'
      }
    >
      <FilmSlate aria-hidden size={tall ? 24 : 18} />
      <span className="font-mono text-xs tabular-nums">{formatTimecodeRange(candidate)}</span>
    </div>
  )
}

/** 候选帧（W11）：绑了源录像的候选向帧端点要 timecode_start 处的真画面；
 * 失败（越界/损坏/抖动）回退占位框——不把破图钉在屏幕上。 */
function CandidateFrame({ candidate, tall = false }: { candidate: ClipCandidate; tall?: boolean }) {
  const [failed, setFailed] = useState(false)
  if (failed || candidate.recording === null) {
    return <FramePlaceholder candidate={candidate} tall={tall} />
  }
  return (
    <img
      src={clipFrameUrl(candidate.id, candidate.recording.id)}
      alt={`${candidate.product_name}候选 ${candidate.timecode_start} 处的画面`}
      loading="lazy"
      onError={() => setFailed(true)}
      className={`${tall ? 'h-48' : 'h-24'} w-full rounded-[4px] border border-line-3 bg-canvas object-cover`}
    />
  )
}

/** 抽屉元信息行（W14）：dt 定宽，墨线分层——与详情页元数据同语言。 */
function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">{label}</dt>
      <dd className="min-w-0 flex-1 text-ink-2">{children}</dd>
    </div>
  )
}

/** 候选详情抽屉（W14）：完整转写/绑定录像/时间码/归属/登记锚——入口在卡片头
 * 的「详情」小钮，**不抢勾选手势**（卡片点击仍是拣选工作台的高频动作）。 */
function CandidateDetailDrawer({
  candidate,
  onClose,
}: {
  candidate: ClipCandidate | null
  onClose: () => void
}) {
  useEscapeClose(candidate !== null, onClose, true)
  if (candidate === null) return null
  const assetId = candidate.registered_asset_id
  const registered = candidate.status === 'registered' && assetId !== null
  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="切片候选详情">
      <div className="modal-backdrop absolute inset-0" onClick={onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-lg flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <FilmSlate aria-hidden size={15} className="text-ink-3" />
          <div className="min-w-0 flex-1 truncate text-[14px] font-semibold text-ink">
            候选详情 · {formatClipId(candidate.id)}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            aria-label="关闭候选详情"
          >
            <X aria-hidden size={14} />
          </button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <CandidateFrame candidate={candidate} tall />
          <dl className="space-y-1.5 text-[13px]">
            <DetailRow label="状态">
              {registered ? (
                <span className="badge badge-published">已登记（单向终态，不可重拣）</span>
              ) : (
                <span className="badge badge-ingested">待拣（可勾选）</span>
              )}
            </DetailRow>
            <DetailRow label="商品归属">
              {candidate.product_id === null ? (
                <span className="text-ink-3">未归属（云转写按录像整段生成，归属是人/治理动作，不编造）</span>
              ) : (
                `${candidate.product_name}（P-${String(candidate.product_id).padStart(4, '0')}）`
              )}
            </DetailRow>
            <DetailRow label="时间码">
              <span className="font-mono text-xs tabular-nums">{formatTimecodeRange(candidate)}</span>
            </DetailRow>
            <DetailRow label="转写来源">
              {TRANSCRIPT_SOURCE_LABELS[candidate.transcript_source] ?? candidate.transcript_source}
              <span className="ml-1 text-xs text-ink-3">（只读，来源是既成事实）</span>
            </DetailRow>
            <DetailRow label="源录像">
              {candidate.recording !== null ? (
                <span title={`对象字节 ${formatBytes(candidate.recording.size_bytes)}`}>
                  《{candidate.recording.label}》（{formatBytes(candidate.recording.size_bytes)}，已上传）
                </span>
              ) : (
                <span className="text-ink-3">
                  未绑定——直播源「{candidate.source_video_label}」没有视频字节（demo），拣选走时间码文本
                </span>
              )}
            </DetailRow>
          </dl>
          <div>
            <div className="mb-1.5 text-xs font-medium text-ink-3">转写全文</div>
            <pre className="whitespace-pre-wrap break-words rounded-[6px] border border-line-2 bg-canvas/40 px-3 py-2.5 font-sans text-[13px] leading-6 text-ink-2">
              {candidate.transcript}
            </pre>
          </div>
          {registered && assetId !== null ? (
            <Link
              to={`/platform/assets/${assetId}`}
              className="btn btn-secondary w-full"
              onClick={onClose}
              title="治理台：补转写、发布（发布后客服可引用）"
            >
              查看登记出的资产 {formatAssetId(assetId)}
            </Link>
          ) : (
            <div className="text-xs leading-5 text-ink-3">
              {candidate.recording !== null
                ? `勾选后拣选将从《${candidate.recording.label}》切出 ${formatTimecodeRange(candidate)} 的真 mp4 片段，登记为待人洗视频资产（转写预置为版本字段）。`
                : '该候选无源录像：勾选后拣选仍登记时间码转写文本（旧路径，待人洗）。'}
            </div>
          )}
        </div>
      </aside>
    </div>
  )
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
  // 第 112 刀 W12：本次拣选登记出的资产 id（后端回执同序返回）——回执横幅的
  // 「查看 A-xxxx」链接用它；与 pickNote 同生命周期（清回执处一并清）。
  const [pickedAssetIds, setPickedAssetIds] = useState<number[]>([])
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
  const registeredCount = clips.filter((c) => c.status === 'registered').length
  const currentRecording = latestRecording(clips)

  // 三维筛选（W13）：状态/源录像/关键词各管一维——客户端过滤（数据量小），
  // 不进 URL（口径对齐资产页：切筛选保留输入、刷新即清）。
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'registered'>('all')
  const [recordingFilter, setRecordingFilter] = useState<'all' | 'none' | number>('all')
  const [query, setQuery] = useState('')
  // 候选详情抽屉（W14）：锚定打开时点的候选快照（候选字段不可变，status 除外）
  const [detailCandidate, setDetailCandidate] = useState<ClipCandidate | null>(null)

  const terms = useMemo(() => splitTerms(query), [query])
  const visibleClips = useMemo(() => {
    let rows = clips
    if (statusFilter !== 'all') rows = rows.filter((c) => c.status === statusFilter)
    if (recordingFilter !== 'all') {
      rows = rows.filter((c) =>
        recordingFilter === 'none' ? c.recording === null : c.recording?.id === recordingFilter,
      )
    }
    if (terms.length > 0) {
      rows = rows.filter((c) =>
        matchesAllTerms(
          [
            c.product_name.toLowerCase(),
            c.transcript.toLowerCase(),
            c.source_video_label.toLowerCase(),
            String(c.id),
            formatClipId(c.id).toLowerCase(),
          ],
          terms,
        ),
      )
    }
    return rows
  }, [clips, statusFilter, recordingFilter, terms])

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

  // 回执与它的链接同生死：清回执处一并清 id（避免「旧链接配新文案」）
  const clearPickReceipt = () => {
    setPickNote(null)
    setPickedAssetIds([])
  }

  const onRecordingPicked = async (files: FileList | null) => {
    const file = files?.[0] ?? null
    if (file === null || uploading) return
    setActionError(null)
    setUploadNote(null)
    clearPickReceipt()
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
    clearPickReceipt()
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
    clearPickReceipt()
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
    clearPickReceipt()
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
      // 第 112 刀 W12：回执就是登记出的资产列表（含 id）——用它给回执横幅挂
      // 「查看 A-xxxx」链接（此前回执只有文案，链接只在已登记候选卡上）。
      const registered = await api.pickClips(registrable.map((c) => c.id))
      setPickedAssetIds(registered.map((asset) => asset.id))
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
      {pickNote ? (
        <SuccessBanner>
          <PickReceipt note={pickNote} assetIds={pickedAssetIds} />
        </SuccessBanner>
      ) : null}
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

      {/* 三维筛选（W13）：几十张候选卡平铺靠滚不是办法——demo 源与真实 ASR 候选
          混排，状态/源录像/关键词各管一维；口径对齐资产页（客户端过滤，不进 URL）。 */}
      {state.phase === 'ok' && clips.length > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="seg" role="tablist" aria-label="候选状态筛选">
            <button
              type="button"
              role="tab"
              aria-selected={statusFilter === 'all'}
              className={`seg-btn ${statusFilter === 'all' ? 'seg-btn-active' : ''}`}
              onClick={() => setStatusFilter('all')}
            >
              全部 <span className={statusFilter === 'all' ? 'text-ink-3' : ''}>{clips.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={statusFilter === 'pending'}
              className={`seg-btn ${statusFilter === 'pending' ? 'seg-btn-active' : ''}`}
              onClick={() => setStatusFilter('pending')}
              title="还没登记的候选（可勾选拣选）"
            >
              待拣 <span className={statusFilter === 'pending' ? 'text-ink-3' : ''}>{pendingCount}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={statusFilter === 'registered'}
              className={`seg-btn ${statusFilter === 'registered' ? 'seg-btn-active' : ''}`}
              onClick={() => setStatusFilter('registered')}
              title="已登记为资产（单向终态，卡上有 A-xxxx 链接）"
            >
              已登记 <span className={statusFilter === 'registered' ? 'text-ink-3' : ''}>{registeredCount}</span>
            </button>
          </div>
          <select
            className="input h-7 max-w-56 text-[12px]"
            aria-label="源录像筛选"
            value={String(recordingFilter)}
            onChange={(e) => {
              const value = e.target.value
              setRecordingFilter(value === 'all' || value === 'none' ? value : Number(value))
            }}
          >
            <option value="all">全部源录像</option>
            <option value="none">未绑定（demo 候选）</option>
            {recordings.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}（{formatBytes(r.size_bytes)}）
              </option>
            ))}
          </select>
          <div className="relative ml-auto w-full max-w-xs">
            <MagnifyingGlass
              aria-hidden
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-caption"
            />
            <input
              className="input h-7 w-full pl-8 pr-8 text-[12px]"
              aria-label="搜索候选"
              placeholder="搜索商品 / 转写 / ID（多词空格分隔）…"
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
        </div>
      ) : null}

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
      ) : visibleClips.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<MagnifyingGlass aria-hidden size={24} />}
            title="当前筛选下没有候选"
            hint={`筛选或搜索把 ${clips.length} 条候选收窄到了 0——清空搜索、或把状态/源录像切回「全部」即可恢复。`}
            action={
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setStatusFilter('all')
                  setRecordingFilter('all')
                  setQuery('')
                }}
              >
                清除全部筛选
              </button>
            }
          />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visibleClips.map((c) => {
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
                  {/* 详情入口（W14）：不抢勾选手势（stopPropagation），已登记卡也有——
                      转写全文/绑定录像/登记锚都在抽屉里 */}
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm shrink-0"
                    aria-label={`查看 ${formatClipId(c.id)} 详情`}
                    title="候选详情：完整转写 / 绑定源录像 / 登记锚"
                    onClick={(e) => {
                      e.stopPropagation()
                      setDetailCandidate(c)
                    }}
                  >
                    <Info aria-hidden size={13} />
                  </button>
                </div>

                {/* 画面（W11）：绑了源录像的候选出真帧（timecode_start 处，≤480px，
                    与洗帧同规格）；无源录像（demo）保持占位——0039「不用假截图」
                    只对没有视频字节的候选仍成立 */}
                <CandidateFrame candidate={c} />

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

      {/* 候选详情抽屉（W14）：入口在卡片头「详情」钮 */}
      <CandidateDetailDrawer candidate={detailCandidate} onClose={() => setDetailCandidate(null)} />
    </div>
  )
}
