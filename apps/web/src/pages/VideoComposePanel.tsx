// 内容成片面板（第 98b 刀/ADR 0056）——素材中心第三页签。AI 排版、人上市：
// 选商品+模板 → plan 同步出时间线候选+预览成片+剪映草稿（人审改）→
// 「确认登记」把文案要点经双闸复用登记 material 资产（可上传剪映导出的成品
// mp4 或直接用预览）。时间线/预览/草稿是任务暂存件不是资产；不做自动 publish。
// 预览常驻「AI 生成」AIGC 角标（服务端 drawtext 钉死，红线①）。

import { useCallback, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { DownloadSimple, FilmSlate, Play, SpinnerGap, UploadSimple } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { ComposeTask, ComposeTemplate, ComposeTimelineItem } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import ActionError from '../components/ActionError'
import Empty from '../components/Empty'
import ProductSelect from '../components/ProductSelect'
import { formatAssetId, formatDateTime } from '../labels'

const TEMPLATE_OPTIONS: { key: ComposeTemplate; label: string; hint: string }[] = [
  { key: 'product_intro', label: '商品介绍', hint: '文案要点打头，图与切片穿插推进' },
  { key: 'highlight', label: '高光集锦', hint: '从切片挑含商品名/卖点词的高光串集锦' },
]

const ITEM_LABEL: Record<ComposeTimelineItem['type'], string> = {
  clip: '切片',
  image: '图片',
  text: '文案',
}

const STATUS_LABEL: Record<ComposeTask['status'], string> = {
  planned: '待人审',
  registered: '已登记',
}

/** 时间线候选条：类型 + 资产锚 + 时窗 + 文案行（AI 排版的可视面）。 */
function TimelineRow({ item }: { item: ComposeTimelineItem }) {
  return (
    <li className="flex items-baseline gap-2 border-b border-line-1 py-1.5 last:border-b-0">
      <span
        className={`inline-block w-12 shrink-0 text-center text-[11px] leading-5 ${
          item.type === 'clip'
            ? 'bg-accent-soft text-accent-strong'
            : item.type === 'image'
              ? 'bg-[rgba(126,88,197,0.1)] text-[#6c4fb5]'
              : 'bg-[rgba(138,97,22,0.12)] text-[#8a6116]'
        }`}
      >
        {ITEM_LABEL[item.type]}
      </span>
      {item.type === 'text' ? (
        <span className="flex-1 text-[13px] leading-5 text-ink">{item.text}</span>
      ) : (
        <Link
          to={`/platform/assets/${item.asset_id}`}
          className="font-mono text-xs text-ink-2 underline-offset-2 hover:text-accent-strong hover:underline"
        >
          {formatAssetId(item.asset_id)}
        </Link>
      )}
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-ink-3">
        {item.start.toFixed(1)}s +{item.dur.toFixed(1)}s
      </span>
    </li>
  )
}

/** 单个任务的审改+登记面：预览播放 / 草稿下载 / 成品上传 / 确认登记。 */
function TaskWorkbench({ task, onDone }: { task: ComposeTask; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<number | null>(task.asset_id)
  const [finalFile, setFinalFile] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const current = result !== null || task.status === 'registered' ? 'registered' : task.status

  const publish = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const resp = await api.publishVideoCompose(task.id, finalFile)
      setResult(resp.asset_id)
      onDone()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel space-y-4 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className="font-semibold text-ink">
          成片 {task.id} · {task.template_name}
        </span>
        <span className="font-mono text-xs text-ink-3">{task.product_name}</span>
        <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] text-ink-2">
          {STATUS_LABEL[current]}
        </span>
        <span className="font-mono text-xs tabular-nums text-ink-3">
          {task.duration_seconds.toFixed(1)}s
        </span>
        {!task.with_tts ? (
          <span className="text-[11px] text-warning">TTS 未配置，预览无声</span>
        ) : null}
      </div>
      {task.note ? <p className="text-xs leading-4 text-ink-3">{task.note}</p> : null}

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <span className="field-label">预览成片（常驻「AI 生成」角标）</span>
          {/* compose/ 暂存件（不是资产）：操作者 cookie 同源直放 */}
          <video
            key={task.id}
            controls
            preload="metadata"
            className="max-h-96 w-full rounded-[8px] border border-line-2 bg-black"
            src={api.videoComposePreviewUrl(task.id)}
          />
        </div>
        <div>
          <span className="field-label">时间线候选（{task.timeline.length} 项）</span>
          <ul className="max-h-80 overflow-y-auto rounded-[8px] border border-line-2 bg-canvas px-3 py-1">
            {task.timeline.map((item, index) => (
              <TimelineRow key={`${item.type}-${item.asset_id}-${index}`} item={item} />
            ))}
          </ul>
          <a
            className="btn btn-secondary btn-sm mt-3"
            href={api.videoComposeDraftUrl(task.id)}
            download
          >
            <DownloadSimple aria-hidden size={14} />
            下载剪映草稿（zip，解压到剪映草稿目录精修）
          </a>
        </div>
      </div>

      {current === 'registered' ? (
        <div className="rounded-[6px] border border-[rgba(30,107,69,0.22)] bg-[rgba(30,107,69,0.05)] px-3 py-2 text-[13px] leading-5">
          已登记为素材资产：
          {result !== null ? (
            <Link
              to={`/platform/assets/${result}`}
              className="ml-1 font-medium text-accent-strong hover:underline"
            >
              {formatAssetId(result)} · 去治理台
            </Link>
          ) : null}
          <span className="ml-1 text-ink-3">（待人洗/发布治理，可被投放引用）</span>
        </div>
      ) : (
        <div className="rounded-[6px] border border-line-2 bg-canvas px-3 py-2.5">
          <div className="text-[12px] leading-4 text-ink-2">
            确认登记：文案要点将过规则+LLM 双闸后登记为 material 资产。可上传剪映
            导出的成品 mp4（留档在任务上）；不传即用预览成片。
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              ref={fileInput}
              type="file"
              accept="video/mp4"
              className="hidden"
              onChange={(e) => setFinalFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
            >
              <UploadSimple aria-hidden size={14} />
              {finalFile ? `已选成品：${finalFile.name}` : '上传成品 mp4（可选）'}
            </button>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void publish()} disabled={busy}>
              {busy ? <SpinnerGap aria-hidden size={14} className="animate-spin" /> : <Play aria-hidden size={14} weight="fill" />}
              {busy ? '登记中…' : '确认登记'}
            </button>
          </div>
        </div>
      )}
      {error ? <ActionError message={error} /> : null}
    </div>
  )
}

export default function VideoComposePanel() {
  const [productId, setProductId] = useState('')
  const [template, setTemplate] = useState<ComposeTemplate>('product_intro')
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const productsFetcher = useCallback(() => api.listProducts(), [])
  const productsQ = useApiData(productsFetcher)
  const products = productsQ.state.phase === 'ok' ? productsQ.state.data : []
  const ttsFetcher = useCallback(() => api.getVideoComposeTtsStatus(), [])
  const ttsQ = useApiData(ttsFetcher)
  const ttsConfigured = ttsQ.state.phase === 'ok' ? ttsQ.state.data.configured : false

  const tasksFetcher = useCallback(() => api.listVideoComposeTasks(), [])
  const tasksQ = useApiData(tasksFetcher)
  const tasks = tasksQ.state.phase === 'ok' ? tasksQ.state.data : []
  // 选中项派生：未选时回落到最新一行（不写 effect——setState-in-effect 连环渲染）
  const selected = tasks.find((t) => t.id === selectedId) ?? tasks[0] ?? null

  const submit = async () => {
    if (planning) return
    if (productId === '') {
      setError('请选择商品')
      return
    }
    setError(null)
    setPlanning(true)
    try {
      const task = await api.planVideoCompose(Number(productId), template)
      setSelectedId(task.id)
      tasksQ.reload()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setPlanning(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="panel space-y-3 px-4 py-4">
        <p className="text-xs leading-5 text-ink-3">
          选商品与模板后一键合成：系统自动选材（该商品已发布的切片/图片/文案要点）
          排出 15-60s 时间线，产出预览成片（常驻「AI 生成」角标）+ 剪映草稿；
          人审改后「确认登记」，文案过双闸质检登记为 material 资产。
          {!ttsConfigured ? ' 未配置 TTS_API_KEY：预览无声（口播可后配）。' : ''}
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="field-label" htmlFor="compose-product">
              商品
            </label>
            <ProductSelect
              products={products}
              value={productId}
              onChange={setProductId}
              disabled={planning}
              inputId="compose-product"
              ariaLabel="选择要成片的商品"
              emptyLabel="选择商品…"
              loading={productsQ.state.phase === 'loading'}
            />
          </div>
          <div>
            <span className="field-label">成片模板</span>
            <div className="mt-1 space-y-1.5" role="radiogroup" aria-label="成片模板">
              {TEMPLATE_OPTIONS.map((option) => (
                <label
                  key={option.key}
                  className={`flex cursor-pointer items-start gap-2 rounded-[8px] border px-3 py-2 transition-colors duration-150 ${
                    template === option.key ? 'border-accent-strong bg-accent-strong/5' : 'border-line-2 bg-canvas'
                  }`}
                >
                  <input
                    type="radio"
                    name="compose-template"
                    className="mt-0.5"
                    checked={template === option.key}
                    disabled={planning}
                    onChange={() => setTemplate(option.key)}
                  />
                  <span className="flex-1">
                    <span className="block text-[13px] font-medium leading-5 text-ink">{option.label}</span>
                    <span className="block text-xs leading-4 text-ink-3">{option.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>
        {error ? <ActionError message={error} /> : null}
        <div className="flex justify-end">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={planning || productId === ''}
          >
            {planning ? <SpinnerGap aria-hidden size={14} className="animate-spin" /> : <FilmSlate aria-hidden size={14} weight="fill" />}
            {planning ? '合成中（选材+预览+草稿，最长约 3 分钟）…' : '生成成片'}
          </button>
        </div>
      </div>

      {tasksQ.state.phase === 'loading' ? null : tasks.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<FilmSlate aria-hidden size={24} />}
            title="还没有成片任务"
            hint="选商品与模板点「生成成片」：时间线候选+预览+剪映草稿一次产出，审改后登记。"
          />
        </div>
      ) : (
        <>
          <div className="panel overflow-x-auto">
            <table className="table-gov">
              <thead>
                <tr>
                  <th className="w-24">任务 ID</th>
                  <th className="w-40">商品</th>
                  <th className="w-28">模板</th>
                  <th className="w-20">时长</th>
                  <th className="w-24">状态</th>
                  <th className="w-24">口播</th>
                  <th className="w-20">产物</th>
                  <th className="w-44">创建时间</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr
                    key={task.id}
                    className={`row-click ${selected?.id === task.id ? 'bg-accent-strong/5' : ''}`}
                    onClick={() => setSelectedId(task.id)}
                  >
                    <td className="font-mono text-xs text-ink-3">C-{task.id}</td>
                    <td className="text-[13px] text-ink-2" title={`P-${task.product_id}`}>
                      {task.product_name}
                    </td>
                    <td className="text-xs text-ink-2">{task.template_name}</td>
                    <td className="font-mono text-xs tabular-nums text-ink-2">
                      {task.duration_seconds.toFixed(1)}s
                    </td>
                    <td className="text-xs text-ink-2">{STATUS_LABEL[task.status]}</td>
                    <td className="text-xs">
                      {task.with_tts ? (
                        <span className="text-ink-2">有声</span>
                      ) : (
                        <span className="text-ink-3" title={task.note ?? undefined}>
                          无声
                        </span>
                      )}
                    </td>
                    <td>
                      {task.asset_id !== null ? (
                        <Link
                          to={`/platform/assets/${task.asset_id}`}
                          className="font-mono text-xs text-ink-2 underline-offset-2 hover:text-accent-strong hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {formatAssetId(task.asset_id)}
                        </Link>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </td>
                    <td className="text-xs text-ink-3 tabular-nums">{formatDateTime(task.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected ? (
            <TaskWorkbench key={selected.id} task={selected} onDone={tasksQ.reload} />
          ) : null}
        </>
      )}
    </div>
  )
}
