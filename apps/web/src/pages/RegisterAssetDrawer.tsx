// 登记资产抽屉：单份（选商品可不挂 + 可选标题 + 上传 .txt/.md ≤ 2MB）与
// 批量导入（CSV title,content 两列，逐行登记一份文档资产）两个页签。
// 前端只做类型/大小校验，415/413/422 以 API 返回文案为准；单份成功后直达
// 资产详情，批量留在抽屉里看报告（创建 A-XXXX 可点、跳过行号+原因）。
// 批量预览是 FileReader 极简计数（表头识别 + 行计数 + 空值行计数），刻意
// 不做完整 CSV 解析——以后端结果为准，避免两套解析器语义漂移。
// 从知识缺口「去补文档」进入时带 gap：不切页签（批量通道不带缺口关联），
// 标题预填「补口径 · 原问」、商品预选、提交附 knowledgeGapId（登记只关联，
// 缺口关闭发生在发布事务里）——列表页用 key 区分实例，预填只落在初始
// state，普通登记路径不受影响。

import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { FileCsv, FileText, X } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { CsvImportReport, KnowledgeGap, Product } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatAssetId, formatGapId } from '../labels'
import ActionError from '../components/ActionError'

const MAX_UPLOAD_BYTES = 2 * 1024 * 1024

type DrawerMode = 'single' | 'batch'

function validateFile(file: File): string | null {
  const lower = file.name.toLowerCase()
  if (!lower.endsWith('.txt') && !lower.endsWith('.md')) {
    return '仅接受 .txt 或 .md 文本文件'
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return '文件超过 2MB 上限'
  }
  if (file.size === 0) {
    return '空文件不能登记'
  }
  return null
}

function validateCsvFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.csv')) {
    return '仅接受 .csv 文件'
  }
  if (file.size === 0) {
    return '空文件不能导入'
  }
  return null
}

/** 批量预览计数（极简，以后端结果为准）：表头识别 + 数据行计数 + 空值行计数。
 * 刻意用朴素逗号切分，不处理引号包裹——预览漂移由「以后端结果为准」兜底。 */
interface CsvPreview {
  headerOk: boolean
  missing: string[]
  total: number
  create: number
  skip: number
}

function previewCsvText(text: string): CsvPreview {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/)
  while (lines.length > 0 && lines[lines.length - 1].trim() === '') lines.pop()
  if (lines.length === 0) return { headerOk: true, missing: [], total: 0, create: 0, skip: 0 }
  const header = lines[0].split(',').map((cell) => cell.trim())
  const missing = ['title', 'content'].filter((column) => !header.includes(column))
  const titleIdx = header.indexOf('title')
  const contentIdx = header.indexOf('content')
  let create = 0
  let skip = 0
  for (const line of lines.slice(1)) {
    if (line.trim() === '') {
      skip++
      continue
    }
    const cells = line.split(',')
    const title = (titleIdx >= 0 ? cells[titleIdx] : '').trim()
    const content = (contentIdx >= 0 ? cells[contentIdx] : '').trim()
    if (title === '' || content === '') skip++
    else create++
  }
  return { headerOk: missing.length === 0, missing, total: lines.length - 1, create, skip }
}

export default function RegisterAssetDrawer({
  open,
  onClose,
  gap,
}: {
  open: boolean
  onClose: () => void
  gap?: KnowledgeGap
}) {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const csvInputRef = useRef<HTMLInputElement>(null)

  const productsFetcher = useCallback(
    () => (open ? api.listProducts() : Promise.resolve([] as Product[])),
    [open],
  )
  const productsQ = useApiData(productsFetcher)
  const products = productsQ.state.phase === 'ok' ? productsQ.state.data : []

  // gap 流程（补缺口）只有单份通道：不切页签，批量导入不带缺口关联
  const [mode, setMode] = useState<DrawerMode>('single')

  const [productId, setProductId] = useState(gap?.product != null ? String(gap.product.id) : '')
  const [title, setTitle] = useState(gap !== undefined ? `补口径 · ${gap.question}` : '')
  const [file, setFile] = useState<File | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [csvFile, setCsvFile] = useState<File | null>(null)
  const [csvPreview, setCsvPreview] = useState<CsvPreview | null>(null)
  const [csvFieldError, setCsvFieldError] = useState<string | null>(null)
  const [csvSubmitError, setCsvSubmitError] = useState<string | null>(null)
  const [csvSubmitting, setCsvSubmitting] = useState(false)
  const [csvReport, setCsvReport] = useState<CsvImportReport | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const busy = mode === 'single' ? submitting : csvSubmitting

  const pickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null
    setFile(picked)
    setFieldError(picked ? validateFile(picked) : null)
    setSubmitError(null)
  }

  const clearFile = () => {
    setFile(null)
    setFieldError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const pickCsvFile = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null
    setCsvFile(picked)
    setCsvReport(null)
    setCsvSubmitError(null)
    if (picked === null) {
      setCsvFieldError(null)
      setCsvPreview(null)
      return
    }
    const invalid = validateCsvFile(picked)
    setCsvFieldError(invalid)
    if (invalid !== null) {
      setCsvPreview(null)
      return
    }
    // FileReader 极简预解析：只做计数提示，不做校验真源（后端为准）
    const reader = new FileReader()
    reader.onload = () => {
      setCsvPreview(previewCsvText(typeof reader.result === 'string' ? reader.result : ''))
    }
    reader.onerror = () => setCsvPreview(null)
    reader.readAsText(picked, 'utf-8')
  }

  const clearCsvFile = () => {
    setCsvFile(null)
    setCsvPreview(null)
    setCsvFieldError(null)
    setCsvSubmitError(null)
    setCsvReport(null)
    if (csvInputRef.current) csvInputRef.current.value = ''
  }

  const doSubmit = async () => {
    if (submitting) return
    if (!file) {
      setFieldError('请选择要登记的文件')
      return
    }
    const invalid = validateFile(file)
    if (invalid) {
      setFieldError(invalid)
      return
    }
    setFieldError(null)
    setSubmitError(null)
    setSubmitting(true)
    const form = new FormData()
    form.append('file', file)
    if (productId !== '') form.append('productId', productId)
    if (title.trim() !== '') form.append('title', title.trim())
    if (gap !== undefined) form.append('knowledgeGapId', String(gap.id))
    try {
      const detail = await api.registerAsset(form)
      onClose()
      navigate(`/platform/assets/${detail.id}`)
    } catch (err) {
      setSubmitError(detailText(err))
      setSubmitting(false)
    }
  }

  const doImportCsv = async () => {
    if (csvSubmitting) return
    if (!csvFile) {
      setCsvFieldError('请选择要导入的 CSV 文件')
      return
    }
    const invalid = validateCsvFile(csvFile)
    if (invalid) {
      setCsvFieldError(invalid)
      return
    }
    setCsvFieldError(null)
    setCsvSubmitError(null)
    setCsvSubmitting(true)
    const form = new FormData()
    form.append('file', csvFile)
    try {
      const report = await api.importCsv(form)
      setCsvReport(report)
    } catch (err) {
      setCsvSubmitError(detailText(err))
    } finally {
      setCsvSubmitting(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (mode === 'single') void doSubmit()
    else void doImportCsv()
  }

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={gap !== undefined ? `补缺口 ${formatGapId(gap.id)}` : '登记资产'}>
      <div className="modal-backdrop absolute inset-0" onClick={onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-md flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <div className="flex-1 text-[14px] font-semibold text-ink">
            {gap !== undefined ? `补缺口 ${formatGapId(gap.id)}` : '登记资产'}
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="关闭登记表单">
            <X aria-hidden size={14} />
          </button>
        </div>

        <form className="flex-1 space-y-4 overflow-y-auto px-4 py-4" onSubmit={submit} noValidate>
          {gap === undefined ? (
            <div className="seg self-start" role="tablist" aria-label="登记方式">
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'single'}
                className={`seg-btn ${mode === 'single' ? 'seg-btn-active' : ''}`}
                onClick={() => setMode('single')}
                disabled={busy}
              >
                单份登记
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'batch'}
                className={`seg-btn ${mode === 'batch' ? 'seg-btn-active' : ''}`}
                onClick={() => setMode('batch')}
                disabled={busy}
              >
                批量导入
              </button>
            </div>
          ) : null}

          {mode === 'single' ? (
            <>
              {gap !== undefined ? (
                <div className="rounded-[6px] border border-[rgba(154,91,6,0.25)] bg-[rgba(154,91,6,0.05)] px-3 py-2 text-xs leading-5 text-warn">
                  补缺口 {formatGapId(gap.id)}：登记后进入已接入，发布后缺口关闭。
                </div>
              ) : (
                <p className="text-xs leading-5 text-ink-3">
                  登记即把文件字节写入对象存储并进入已接入（来源=上传）；机洗成功直接到待人洗，失败会停在已接入并给出原因。
                </p>
              )}

              {gap !== undefined ? (
                <div className="rounded-[6px] border border-line-2 bg-surface px-3 py-2 text-xs leading-5 text-ink-2">
                  顾客原问：<span className="text-ink">{gap.question}</span>
                  {gap.product !== null ? (
                    <span className="text-ink-3">（挂商品：{gap.product.name}）</span>
                  ) : null}
                </div>
              ) : null}

              <label className="block">
                <span className="field-label">挂载商品</span>
                <select
                  className="input"
                  value={productId}
                  onChange={(e) => setProductId(e.target.value)}
                  disabled={submitting}
                >
                  <option value="">不挂商品</option>
                  {products.map((p) => (
                    <option key={p.id} value={String(p.id)}>
                      {p.name}（{p.category} · {p.id}）
                    </option>
                  ))}
                </select>
                <span className="mt-1.5 block text-[11px] leading-4 text-ink-3">
                  挂了商品的文档，按该商品的规格字段做机洗与发布必填。
                </span>
              </label>

              <label className="block">
                <span className="field-label">标题（可选）</span>
                <input
                  className="input"
                  placeholder="如：钛钢保温杯 · 检测报告"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={submitting}
                />
              </label>

              <div>
                <span className="field-label">文件（.txt / .md，≤ 2MB）</span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,.md,text/plain,text/markdown"
                  className="hidden"
                  onChange={pickFile}
                />
                {file ? (
                  <div className="flex items-center gap-2.5 rounded-[6px] border border-line-3 bg-surface px-3 py-2">
                    <FileText aria-hidden size={15} className="shrink-0 text-ink-3" />
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{file.name}</span>
                    <span className="shrink-0 font-mono text-[11px] text-ink-3 tabular-nums">
                      {(file.size / 1024).toFixed(1)} KB
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={clearFile}
                      aria-label="移除文件"
                      disabled={submitting}
                    >
                      <X aria-hidden size={13} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="flex w-full flex-col items-center justify-center gap-2 rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface px-4 py-7 text-center transition-colors duration-150 hover:bg-[rgba(38,49,72,0.03)]"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={submitting}
                  >
                    <FileText aria-hidden size={22} className="text-ink-3" />
                    <span className="text-[13px] text-ink-2">点击选择文件</span>
                    <span className="text-[11px] leading-4 text-ink-3">
                      仅接受纯文本或 Markdown；超过 2MB 会被拒绝
                    </span>
                  </button>
                )}
                {fieldError ? <p className="mt-2 text-xs leading-5 text-danger">{fieldError}</p> : null}
              </div>

              {submitError ? <ActionError message={submitError} /> : null}
            </>
          ) : (
            <>
              <p className="text-xs leading-5 text-ink-3">
                CSV 首行为表头，须含 title 与 content 两列；每行登记一份文档资产
                （不挂商品，来源=上传）。单次最多 200 行，空行/空值行会被跳过。
              </p>

              <div>
                <span className="field-label">CSV 文件（title,content 两列，≤ 200 行）</span>
                <input
                  ref={csvInputRef}
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={pickCsvFile}
                />
                {csvFile ? (
                  <div className="flex items-center gap-2.5 rounded-[6px] border border-line-3 bg-surface px-3 py-2">
                    <FileCsv aria-hidden size={15} className="shrink-0 text-ink-3" />
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{csvFile.name}</span>
                    <span className="shrink-0 font-mono text-[11px] text-ink-3 tabular-nums">
                      {(csvFile.size / 1024).toFixed(1)} KB
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={clearCsvFile}
                      aria-label="移除 CSV 文件"
                      disabled={csvSubmitting}
                    >
                      <X aria-hidden size={13} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="flex w-full flex-col items-center justify-center gap-2 rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface px-4 py-7 text-center transition-colors duration-150 hover:bg-[rgba(38,49,72,0.03)]"
                    onClick={() => csvInputRef.current?.click()}
                    disabled={csvSubmitting}
                  >
                    <FileCsv aria-hidden size={22} className="text-ink-3" />
                    <span className="text-[13px] text-ink-2">点击选择 CSV 文件</span>
                    <span className="text-[11px] leading-4 text-ink-3">
                      首行表头须含 title 与 content 两列
                    </span>
                  </button>
                )}
                {csvFieldError ? (
                  <p className="mt-2 text-xs leading-5 text-danger">{csvFieldError}</p>
                ) : null}
              </div>

              {csvPreview !== null && csvReport === null ? (
                <div className="rounded-[6px] border border-line-2 bg-surface px-3 py-2 text-xs leading-5 text-ink-2">
                  预览：共 {csvPreview.total} 行数据，约 {csvPreview.create} 条将创建、
                  {csvPreview.skip} 条将跳过（空值行）。计数仅供参考（引号包裹的
                  逗号/换行可能造成偏差），以后端结果为准。
                  {csvPreview.headerOk ? null : (
                    <span className="mt-1 block text-danger">
                      表头缺少 {csvPreview.missing.join('、')} 列，后端将拒绝整批导入。
                    </span>
                  )}
                </div>
              ) : null}

              {csvReport !== null ? (
                <div className="space-y-3" role="status" aria-label="导入结果报告">
                  <div className="text-xs leading-5 text-ink-2">
                    导入完成：创建 <span className="font-semibold text-ink">{csvReport.created.length}</span> 条
                    <span className="text-ink-3">
                      （已登记：机洗成功者进入待人洗，失败者留在已接入可就地重试）
                    </span>
                    ，跳过 <span className="font-semibold text-ink">{csvReport.skipped.length}</span> 条。
                  </div>
                  {csvReport.created.length > 0 ? (
                    <div className="rounded-[6px] border border-line-2">
                      <div className="border-b border-line-2 px-3 py-1.5 text-[11px] font-medium text-ink-3">
                        已创建
                      </div>
                      <ul>
                        {csvReport.created.map((row) => (
                          <li
                            key={row.asset_id}
                            className="flex items-center gap-2 border-b border-line-1 px-3 py-1.5 text-xs last:border-b-0"
                          >
                            <span className="shrink-0 text-ink-3 tabular-nums">第 {row.row} 行</span>
                            <Link
                              to={`/platform/assets/${row.asset_id}`}
                              className="shrink-0 font-mono text-accent-strong transition-colors duration-150 hover:brightness-110"
                              title="打开资产详情"
                            >
                              {formatAssetId(row.asset_id)}
                            </Link>
                            <span className="min-w-0 flex-1 truncate text-ink" title={row.title}>
                              {row.title}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {csvReport.skipped.length > 0 ? (
                    <div className="rounded-[6px] border border-[rgba(154,91,6,0.25)]">
                      <div className="border-b border-[rgba(154,91,6,0.25)] px-3 py-1.5 text-[11px] font-medium text-warn">
                        已跳过
                      </div>
                      <ul>
                        {csvReport.skipped.map((row) => (
                          <li
                            key={`${row.row}-${row.reason}`}
                            className="flex items-center gap-2 border-b border-line-1 px-3 py-1.5 text-xs last:border-b-0"
                          >
                            <span className="shrink-0 text-ink-3 tabular-nums">第 {row.row} 行</span>
                            <span className="min-w-0 flex-1 truncate text-warn" title={row.reason}>
                              {row.reason}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {csvSubmitError ? <ActionError message={csvSubmitError} /> : null}
            </>
          )}
        </form>

        <div className="flex justify-end gap-2 border-t border-line-2 px-4 py-3">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose} disabled={busy}>
            取消
          </button>
          {mode === 'single' ? (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => void doSubmit()}
              disabled={submitting}
            >
              {submitting ? '登记中…' : '登记到中台'}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => void doImportCsv()}
              disabled={csvSubmitting}
            >
              {csvSubmitting ? '导入中…' : csvReport !== null ? '再导入一次' : '导入 CSV'}
            </button>
          )}
        </div>
      </aside>
    </div>
  )
}
