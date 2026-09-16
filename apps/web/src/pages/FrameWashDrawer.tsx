// 洗帧抽屉（第 94c 刀/ADR 0053）：已发布切片视频资产的详情页入口。
// 流程：开始洗帧（采样 + VLM 打分，分钟级同步请求）→ 候选帧网格（缩略图 +
// 分数 + 时间点 + VLM 一句话）→ 勾选 → 「登记所选帧」→ 回执列表（新资产
// A-xxxx + 标题「… · 实拍帧 mm:ss」+ 切自锚）。
// 候选是**请求态**（不落库、刷新即重算）；确认时只回传 at_second——服务器从
// 已发布版字节重新抽全尺寸帧，不信任请求里的缩略图。VLM 无 key：按钮禁用
// （父页按 /clips/frames/status 判定），候选端点自身也 409（后端是唯一闸）。

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle, Camera, X } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { FrameCandidatesResult, FrameRegisterResult } from '../api/types'
import { useEscapeClose } from '../hooks/useEscapeClose'
import { formatAssetId } from '../labels'
import ActionError from '../components/ActionError'

export default function FrameWashDrawer({
  open,
  onClose,
  assetId,
  assetTitle,
}: {
  open: boolean
  onClose: () => void
  assetId: number
  assetTitle: string
}) {
  const [result, setResult] = useState<FrameCandidatesResult | null>(null)
  const [washing, setWashing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 勾选集：以 at_second 为键（候选无 id——它是请求态，不是中台对象）
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [registering, setRegistering] = useState(false)
  const [registered, setRegistered] = useState<FrameRegisterResult[]>([])
  const [registerError, setRegisterError] = useState<string | null>(null)

  const busy = washing || registering
  useEscapeClose(open, onClose, !busy)

  const reset = useCallback(() => {
    setResult(null)
    setError(null)
    setSelected(new Set())
    setRegistered([])
    setRegisterError(null)
  }, [])

  // 打开时清场：上次会话的候选不跨开（刷新即重算的同一口径）
  useEffect(() => {
    if (open) reset()
  }, [open, reset])

  if (!open) return null

  const wash = async () => {
    if (washing) return
    setWashing(true)
    setError(null)
    try {
      const outcome = await api.listFrameCandidates(assetId)
      setResult(outcome)
      setSelected(new Set())
    } catch (err) {
      setError(detailText(err))
    } finally {
      setWashing(false)
    }
  }

  const toggle = (atSecond: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(atSecond)) next.delete(atSecond)
      else next.add(atSecond)
      return next
    })
  }

  const registerSelected = async () => {
    if (registering || selected.size === 0) return
    setRegistering(true)
    setRegisterError(null)
    const frames = (result?.candidates ?? []).filter((c) => selected.has(c.at_second))
    const done: FrameRegisterResult[] = []
    let failed: string | null = null
    // 逐帧顺序确认（同切片拣选的「逐候选登记」形态）：第 k 帧失败时前 k-1 帧
    // 已完整落库，失败原因如实展示，可重试剩余
    for (const frame of frames) {
      try {
        done.push(await api.registerFrame(assetId, frame.at_second, frame.note))
      } catch (err) {
        failed = `第 ${frame.at_time} 帧登记失败：${detailText(err)}（此前 ${done.length} 帧已登记）`
        break
      }
    }
    setRegistered((prev) => [...done.reverse(), ...prev])
    setRegisterError(failed)
    setRegistering(false)
  }

  const candidates = result?.candidates ?? []

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="洗帧到素材库">
      <div className="modal-backdrop absolute inset-0" onClick={busy ? undefined : onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-lg flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <Camera aria-hidden size={15} className="text-ink-3" />
          <div className="min-w-0 flex-1 truncate text-[14px] font-semibold text-ink">
            洗帧到素材库 · {formatAssetId(assetId)}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭洗帧抽屉"
          >
            <X aria-hidden size={14} />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <p className="text-xs leading-5 text-ink-3">
            对已发布视频「{assetTitle}」每 5 秒抽一帧，VLM 打分挑清晰商品帧（≥6 分，
            上限 8）；勾选确认后登记为独立图片资产（来源=直播洗帧、挂同一商品），走
            图片描述人洗 → 发布后可被顾客检索与出图。候选是临时预览，刷新即重算。
          </p>

          {result === null ? (
            <button
              type="button"
              className="btn btn-primary w-full"
              disabled={washing}
              onClick={() => void wash()}
            >
              {washing ? '洗帧中（采样 + 打分，最长约一分钟）…' : '开始洗帧'}
            </button>
          ) : (
            <div className="text-xs leading-5 text-ink-2" role="status">
              已采样 {result.sampled} 帧、{candidates.length} 帧过线
              （视频 {Math.round(result.duration_seconds)}s）。
              {candidates.length === 0 ? ' 没有帧达到 6 分——可再洗一次或改用人工截图上传。' : null}
            </div>
          )}
          {error !== null ? <ActionError message={error} /> : null}

          {candidates.length > 0 ? (
            <div className="grid grid-cols-2 gap-3">
              {candidates.map((frame) => {
                const picked = selected.has(frame.at_second)
                return (
                  <button
                    key={frame.at_second}
                    type="button"
                    className={`flex flex-col overflow-hidden rounded-[8px] border text-left transition-colors duration-150 ${
                      picked
                        ? 'border-accent bg-[rgba(65,118,230,0.06)]'
                        : 'border-line-2 hover:bg-hover'
                    }`}
                    disabled={registering}
                    aria-pressed={picked}
                    onClick={() => toggle(frame.at_second)}
                  >
                    <span className="relative block">
                      <img
                        src={frame.thumbnail_data_url}
                        alt={`${frame.at_time} 处的画面缩略图`}
                        loading="lazy"
                        className="block aspect-video w-full bg-canvas object-contain"
                      />
                      <span
                        className={`absolute left-1.5 top-1.5 rounded-[4px] px-1.5 py-0.5 font-mono text-[11px] tabular-nums ${
                          picked ? 'bg-accent text-white' : 'bg-[rgba(22,26,35,0.65)] text-white'
                        }`}
                      >
                        {frame.score} 分 · {frame.at_time}
                      </span>
                    </span>
                    <span className="flex items-start gap-1.5 px-2 py-1.5">
                      {picked ? (
                        <CheckCircle aria-hidden size={13} className="mt-0.5 shrink-0 text-accent-strong" weight="fill" />
                      ) : null}
                      <span className="min-w-0 flex-1 text-[11px] leading-4 text-ink-2" title={frame.note}>
                        {frame.note || '（VLM 未给说明）'}
                      </span>
                    </span>
                  </button>
                )
              })}
            </div>
          ) : null}

          {registered.length > 0 ? (
            <div className="space-y-2" role="status" aria-label="登记回执">
              <div className="text-xs font-medium text-ink-2">
                已登记 {registered.length} 帧为图片资产（待人洗）：
              </div>
              <ul className="rounded-[6px] border border-line-2">
                {registered.map((row) => (
                  <li
                    key={row.asset.id}
                    className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-line-1 px-3 py-1.5 text-xs last:border-b-0"
                  >
                    <Link
                      to={`/platform/assets/${row.asset.id}`}
                      className="shrink-0 font-mono text-accent-strong transition-colors duration-150 hover:brightness-110"
                      title="打开资产详情（补描述、发布）"
                    >
                      {formatAssetId(row.asset.id)}
                    </Link>
                    <span className="min-w-0 flex-1 truncate text-ink" title={row.asset.title ?? ''}>
                      {row.asset.title ?? '未命名资产'}
                    </span>
                    <span className="shrink-0 text-ink-3">切自 {row.cut_from}</span>
                  </li>
                ))}
              </ul>
              <div className="text-[11px] leading-4 text-ink-3">
                登记后走 94a 治理：VLM 出「图片描述」草稿 → 人洗确认 → 发布 → 顾客问图片内容词命中可出图。
              </div>
            </div>
          ) : null}
          {registerError !== null ? <ActionError message={registerError} /> : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-line-2 px-4 py-3">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose} disabled={busy}>
            关闭
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={registering || selected.size === 0}
            onClick={() => void registerSelected()}
          >
            {registering ? '登记中…' : `登记所选帧（${selected.size}）`}
          </button>
        </div>
      </aside>
    </div>
  )
}
