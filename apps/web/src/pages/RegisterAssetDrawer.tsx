// 登记资产抽屉：选商品（可不挂）+ 可选标题 + 上传文件（.txt/.md ≤ 2MB）。
// 前端只做类型/大小校验，415/413/422 以 API 返回文案为准；成功后直达资产详情。

import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, X } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { Product } from '../api/types'
import { useApiData } from '../hooks/useApiData'

const MAX_UPLOAD_BYTES = 2 * 1024 * 1024

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

export default function RegisterAssetDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const productsFetcher = useCallback(
    () => (open ? api.listProducts() : Promise.resolve([] as Product[])),
    [open],
  )
  const productsQ = useApiData(productsFetcher)
  const products = productsQ.state.phase === 'ok' ? productsQ.state.data : []

  const [productId, setProductId] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

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
    try {
      const detail = await api.registerAsset(form)
      onClose()
      navigate(`/platform/assets/${detail.id}`)
    } catch (err) {
      setSubmitError(detailText(err))
      setSubmitting(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void doSubmit()
  }

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="登记资产">
      <div className="modal-backdrop absolute inset-0" onClick={onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-md flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <div className="flex-1 text-[14px] font-semibold text-ink">登记资产</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="关闭登记表单">
            <X aria-hidden size={14} />
          </button>
        </div>

        <form className="flex-1 space-y-4 overflow-y-auto px-4 py-4" onSubmit={submit} noValidate>
          <p className="text-xs leading-5 text-ink-3">
            登记即把文件字节写入对象存储并进入已接入；机洗成功直接到待人洗，失败会停在已接入并给出原因。
          </p>

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

          {submitError ? (
            <div
              role="alert"
              className="rounded-[6px] border border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] px-3 py-2 text-xs leading-5 text-danger"
            >
              {submitError}
            </div>
          ) : null}
        </form>

        <div className="flex justify-end gap-2 border-t border-line-2 px-4 py-3">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => void doSubmit()}
            disabled={submitting}
          >
            {submitting ? '登记中…' : '登记到中台'}
          </button>
        </div>
      </aside>
    </div>
  )
}
