// 资产详情：治理台核心界面。
// 主栏 = 正在处理的版本（字段确认/补填）；侧栏 = 治理动作（发布闸门）、元数据、版本、留痕。
// 只读语义：待人洗以外状态字段不可编辑（已发布版本是只读证据）。
// dialogue 资产（第 12 刀/ADR 0035）：转写正文只读查看 + qa_pairs QA 对编辑器
// （人洗必须看见转写；机洗草稿改/删/增后确认，confirmed 才在发布时成块入索引）。
// 生命周期出口三件（第 28 刀/ADR 0042）：待人洗版「上传新正文」、未发布修订
// 「放弃修订」、从未发布的失败资产「废弃」——均为二次确认对话框，明示字节删除不可恢复。

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowCounterClockwise,
  ArrowLeft,
  ArrowsClockwise,
  CaretDown,
  CaretRight,
  CheckCircle,
  PencilSimple,
  Plus,
  Prohibit,
  SealCheck,
  Trash,
  UploadSimple,
  Warning,
  XCircle,
} from '@phosphor-icons/react'
import { ApiError, detailText, parsePublishGate, type PublishGateDetail } from '../api/client'
import { api } from '../api/endpoints'
import { resolveFieldValue, toFieldView, type FieldView } from '../api/fields'
import type { AssetVersion, Product, QaPair } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatDateTime, formatAssetId, isStale, sourceKindLabel } from '../labels'
import { ErrorBanner, InfoBanner, SuccessBanner } from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import { LoadingHint } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { KindChip, StatusBadge } from '../components/StateBadge'

function FieldRow({
  assetId,
  versionNo,
  view,
  editable,
  onSaved,
}: {
  assetId: number
  versionNo: number
  view: FieldView
  editable: boolean
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const patch = async (value: string) => {
    setSaving(true)
    setError(null)
    try {
      await api.confirmFields(assetId, versionNo, { [view.field]: value })
      setEditing(false)
      onSaved()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="field-row border-b border-line-1 px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="w-24 shrink-0 text-[13px] text-ink-2">
          {view.field}
          {view.required ? <span className="ml-0.5 text-danger">*</span> : null}
        </div>

        {editing ? (
          <div className="flex min-w-[16rem] flex-1 items-center gap-2">
            <input
              className="input h-7 flex-1 text-[13px]"
              value={draft}
              autoFocus
              placeholder={`填写${view.field}（只填有出处的值，没有出处保持弃权）`}
              disabled={saving}
              onChange={(e) => {
                setDraft(e.target.value)
                if (error !== null) setError(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && draft.trim() !== '') void patch(draft.trim())
              }}
            />
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={saving || draft.trim() === ''}
              onClick={() => void patch(draft.trim())}
            >
              {saving ? '保存中…' : '保存'}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={saving}
              onClick={() => setEditing(false)}
            >
              取消
            </button>
          </div>
        ) : (
          <>
            {view.value !== null ? (
              <div className="flex min-w-[10rem] flex-1 items-center gap-2">
                <span
                  className={
                    view.source === 'human' ? 'text-[13px] font-medium text-ink' : 'text-[13px] text-ink'
                  }
                >
                  {view.value}
                </span>
                {view.source === 'human' ? (
                  <span className="tag tag-confirmed">
                    {view.inherited ? '继承自已发布版 · 已确认' : '已确认'}
                  </span>
                ) : (
                  <span className="tag tag-machine">机洗</span>
                )}
              </div>
            ) : view.abstained ? (
              <div className="flex min-w-[10rem] flex-1 items-center gap-1.5 text-[13px] text-ink-3">
                <Prohibit aria-hidden size={13} />
                弃权 · 原文未找到
              </div>
            ) : (
              <div className="flex min-w-[10rem] flex-1 text-[13px] text-ink-3">未抽取</div>
            )}

            {editable ? (
              <div className="flex shrink-0 items-center gap-1.5">
                {view.source === 'machine' ? (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    title="机洗抽取的值与原文一致时，一键确认即可"
                    disabled={saving}
                    onClick={() => void patch(view.value ?? '')}
                  >
                    <CheckCircle aria-hidden size={12} />
                    {saving ? '确认中…' : '确认'}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={saving}
                  onClick={() => {
                    setDraft(view.value ?? '')
                    setEditing(true)
                    setError(null)
                  }}
                >
                  <PencilSimple aria-hidden size={12} />
                  {view.value !== null ? '修改' : '补填'}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>
      {error !== null ? (
        <div className="mt-1.5 pl-[7.5rem] text-xs leading-5 text-danger">{error}</div>
      ) : null}
    </div>
  )
}

/** 版本正文（只读，text/plain 直读）：dialogue 人洗必须看得见转写原文；UX-B
 * 只读证据视图复用它展示被引用版本的正文。title/note/errorLabel 由调用方给，
 * dialogue 传对话文案，只读证据传「该版本正文」。 */
function VersionTextPanel({
  assetId,
  versionNo,
  title,
  note,
  errorLabel = '正文',
  emptyText,
}: {
  assetId: number
  versionNo: number
  title: string
  note?: string
  errorLabel?: string
  emptyText?: string
}) {
  const fetcher = useCallback(() => api.getVersionText(assetId, versionNo), [assetId, versionNo])
  const q = useApiData(fetcher)
  return (
    <div className="panel">
      <div className="panel-title flex-wrap">
        <span>
          {title} · v{versionNo}
        </span>
        {note !== undefined ? (
          <span className="text-xs font-normal text-ink-3">{note}</span>
        ) : null}
      </div>
      {q.state.phase === 'loading' ? <LoadingHint text={`加载${errorLabel}…`} /> : null}
      {q.state.phase === 'error' ? (
        <div className="px-4 py-3 text-[13px] leading-6 text-ink-3">
          {errorLabel}读取失败：{detailText(q.state.error)}
        </div>
      ) : null}
      {q.state.phase === 'ok' ? (
        emptyText !== undefined && q.state.data.trim() === '' ? (
          <div className="px-4 py-3 text-[13px] leading-6 text-ink-3">{emptyText}</div>
        ) : (
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-sans text-[13px] leading-6 text-ink-2">
            {q.state.data}
          </pre>
        )
      ) : null}
    </div>
  )
}

/** 血缘分组小标（墨线分组：淡画布底 + 下缘墨线，token 对照元数据小标）。 */
function LineageGroupTitle({ children }: { children: ReactNode }) {
  return (
    <div className="border-b border-line-1 bg-canvas/60 px-4 py-1 text-xs font-medium text-ink-3">
      {children}
    </div>
  )
}

function LineageTime({ at }: { at: string }) {
  return <span className="shrink-0 text-xs tabular-nums text-ink-3">{formatDateTime(at)}</span>
}

/**
 * 血缘视图（第 20 刀/ADR 0026）：资产详情上拼出来的派生只读视图——无表、
 * 不进检索、不能发布。「从哪来/版本与审计」已由元数据与留痕面板承担，本面板
 * 只回答「被谁用过」：引用样例（问句+版本+时间，后端截断+计数）、写回事件
 * （发布/回滚各带徽章，字段名按该版确认值派生）、考核抽题（题面+版本+时间）、
 * 导出（第 26 刀：MCP export_published 留痕拼装，版本+时间，操作者恒 mcp）。
 * 详情加载完成后独立请求（不阻塞主栏人洗）；四块全空=「还没有被使用的记录」。
 */
function LineagePanel({ assetId }: { assetId: number }) {
  const [open, setOpen] = useState(true)
  const fetcher = useCallback(() => api.getAssetLineage(assetId), [assetId])
  const q = useApiData(fetcher)
  const data = q.state.phase === 'ok' ? q.state.data : null
  const used =
    data !== null &&
    (data.usages.citations.total > 0 ||
      data.usages.writebacks.length > 0 ||
      data.usages.coaching.length > 0 ||
      data.usages.exports.length > 0)

  return (
    <div className="panel">
      <button
        type="button"
        className="panel-title w-full cursor-pointer bg-transparent text-left"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>血缘</span>
        <span className="text-xs font-normal text-ink-3">
          {data === null
            ? ''
            : used
              ? `引用 ${data.usages.citations.total} · 写回 ${data.usages.writebacks.length} · 考核 ${data.usages.coaching.length} · 导出 ${data.usages.exports.length}`
              : '未被使用'}
        </span>
        <span className="flex-1" />
        {open ? <CaretDown aria-hidden size={13} /> : <CaretRight aria-hidden size={13} />}
      </button>
      {!open ? null : q.state.phase === 'loading' ? (
        <div className="px-4 py-3 text-xs text-ink-3">加载血缘…</div>
      ) : q.state.phase === 'error' ? (
        <div className="px-4 py-3 text-[13px] leading-6 text-ink-3">
          血缘读取失败：{detailText(q.state.error)}
        </div>
      ) : data === null || !used ? (
        <div className="px-4 py-3.5 text-[13px] leading-6 text-ink-3">
          还没有被使用的记录。
          <span className="mt-0.5 block text-xs">客服引用、发布写回、考核抽题、MCP 导出发生后在这里拼装。</span>
        </div>
      ) : (
        <>
          {data.usages.citations.total > 0 ? (
            <div>
              <LineageGroupTitle>引用 · {data.usages.citations.total}</LineageGroupTitle>
              {data.usages.citations.samples.map((s) => (
                <div
                  key={`${s.session_id}-${s.at}`}
                  className="border-b border-line-1 px-4 py-2 last:border-b-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink" title={s.question}>
                      {s.question}
                    </span>
                    <span className="font-mono text-xs text-ink-3">v{s.version_no}</span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-3">会话 #{s.session_id}</span>
                    <span className="flex-1" />
                    <LineageTime at={s.at} />
                  </div>
                </div>
              ))}
              {data.usages.citations.samples.length < data.usages.citations.total ? (
                <div className="px-4 py-1.5 text-xs text-ink-3">
                  共 {data.usages.citations.total} 条引用，只列最近 {data.usages.citations.samples.length} 条。
                </div>
              ) : null}
            </div>
          ) : null}
          {data.usages.writebacks.length > 0 ? (
            <div>
              <LineageGroupTitle>写回 · {data.usages.writebacks.length}</LineageGroupTitle>
              {data.usages.writebacks.map((w) => (
                <div key={`${w.at}-${w.version_no}-${w.action}`} className="border-b border-line-1 px-4 py-2 last:border-b-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-2">v{w.version_no}</span>
                    <span className={w.action === 'rollback' ? 'tag tag-machine' : 'tag tag-confirmed'}>
                      {w.action === 'rollback' ? '回滚写回' : '发布写回'}
                    </span>
                    <span className="text-xs text-ink-3">
                      {w.product_id !== null ? `商品 #${w.product_id}` : '未挂商品'}
                    </span>
                    <span className="flex-1" />
                    <span className="text-xs text-ink-3">{w.operator}</span>
                    <LineageTime at={w.at} />
                  </div>
                  {w.fields.length > 0 ? (
                    <div className="mt-0.5 truncate text-xs text-ink-3" title={w.fields.join('、')}>
                      {w.fields.join('、')}
                    </div>
                  ) : null}
                </div>
              ))}
              <div className="px-4 py-1.5 text-xs text-ink-3">
                写回随发布/回滚同事务；字段按该版确认值如实派生。
              </div>
            </div>
          ) : null}
          {data.usages.coaching.length > 0 ? (
            <div>
              <LineageGroupTitle>考核 · {data.usages.coaching.length}</LineageGroupTitle>
              {data.usages.coaching.map((c) => (
                <div
                  key={c.record_id}
                  className="border-b border-line-1 px-4 py-2 last:border-b-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink" title={c.question}>
                      {c.question}
                    </span>
                    <span className="font-mono text-xs text-ink-3">v{c.version_no}</span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-3">记录 #{c.record_id}</span>
                    <span className="flex-1" />
                    <LineageTime at={c.at} />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          {data.usages.exports.length > 0 ? (
            <div>
              <LineageGroupTitle>导出 · {data.usages.exports.length}</LineageGroupTitle>
              {data.usages.exports.map((e, i) => (
                <div
                  key={`${e.at}-${e.version_no}-${i}`}
                  className="border-b border-line-1 px-4 py-2 last:border-b-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-2">v{e.version_no}</span>
                    <span className="tag tag-machine">导出 · MCP</span>
                    <span className="flex-1" />
                    <span className="text-xs text-ink-3">{e.operator}</span>
                    <LineageTime at={e.at} />
                  </div>
                </div>
              ))}
              <div className="px-4 py-1.5 text-xs text-ink-3">
                连接层 export_published 的留痕：每次导出每份资产一行，只记版本与时间，不存正文。
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}

/** QA 对编辑器：qa_pairs（dialogue 唯一字段，ADR 0035）。列表=每对 q/a 两个
 * 输入 + 删除；底部「添加一对」；确认走既有 PATCH 流程（source=human）。
 * 空列表确认=「没有 QA」（发布闸门对 dialogue 不设必填，弃权/空都可发布）。 */
function QaPairsEditor({
  assetId,
  versionNo,
  view,
  editable,
  onSaved,
}: {
  assetId: number
  versionNo: number
  view: FieldView
  editable: boolean
  onSaved: () => void
}) {
  // 初始值=外部草稿/确认值（调用方按 key 重挂本组件：版本或来源变化即重置）
  const [pairs, setPairs] = useState<QaPair[]>(view.qaPairs ?? [])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const invalid = pairs.some((p) => p.q.trim() === '' || p.a.trim() === '')

  const confirmAll = async () => {
    if (invalid || saving) return
    setSaving(true)
    setError(null)
    try {
      await api.confirmFields(assetId, versionNo, {
        qa_pairs: pairs.map((p) => ({ q: p.q.trim(), a: p.a.trim() })),
      })
      onSaved()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setSaving(false)
    }
  }

  const updatePair = (index: number, next: QaPair) => {
    setPairs((prev) => prev.map((p, i) => (i === index ? next : p)))
    if (error !== null) setError(null)
  }

  return (
    <div className="panel">
      <div className="panel-title flex-wrap">
        <span>QA 对 · v{versionNo}</span>
        {view.source === 'human' ? (
          <span className="tag tag-confirmed">
            {view.inherited ? '继承自已发布版 · 已确认' : '已确认'}
          </span>
        ) : view.source === 'machine' ? (
          <span className="tag tag-machine">机洗草稿</span>
        ) : null}
        <span className="text-xs font-normal text-ink-3">
          确认后发布时按对成块「问：…/答：…」入检索；机洗草稿未确认不进索引
        </span>
      </div>
      {pairs.length === 0 ? (
        <div className="px-4 py-3.5 text-[13px] leading-6 text-ink-3">
          {view.abstained
            ? '机洗未抽出 QA（对话无可抽问答，或未配置模型）。可手动添加，也可直接确认「没有 QA」。'
            : '暂无 QA 对：可手动添加，确认后发布只保留转写轮次证据。'}
        </div>
      ) : (
        pairs.map((pair, i) => (
          <div key={i} className="border-b border-line-1 px-4 py-3">
            <div className="flex items-start gap-2">
              <span className="mt-2 w-5 shrink-0 font-mono text-xs tabular-nums text-ink-3">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-1.5">
                <input
                  className="input h-8 w-full text-[13px]"
                  value={pair.q}
                  placeholder="问（顾客问法）"
                  disabled={!editable || saving}
                  onChange={(e) => updatePair(i, { ...pair, q: e.target.value })}
                />
                <input
                  className="input h-8 w-full text-[13px]"
                  value={pair.a}
                  placeholder="答（客服口径，以转写为出处）"
                  disabled={!editable || saving}
                  onChange={(e) => updatePair(i, { ...pair, a: e.target.value })}
                />
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm mt-1 shrink-0"
                title="删除这一对"
                disabled={!editable || saving}
                onClick={() => setPairs((prev) => prev.filter((_, j) => j !== i))}
              >
                <Trash aria-hidden size={13} />
              </button>
            </div>
            {pair.q.trim() === '' || pair.a.trim() === '' ? (
              <div className="mt-1.5 pl-7 text-xs text-warn">问与答都须非空</div>
            ) : null}
          </div>
        ))
      )}
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={!editable || saving}
          onClick={() => setPairs((prev) => [...prev, { q: '', a: '' }])}
        >
          <Plus aria-hidden size={13} />
          添加一对
        </button>
        <span className="flex-1" />
        {error !== null ? <span className="text-xs text-danger">{error}</span> : null}
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={!editable || saving || invalid}
          onClick={() => void confirmAll()}
        >
          <CheckCircle aria-hidden size={13} />
          {saving ? '确认中…' : pairs.length > 0 ? `确认 ${pairs.length} 对` : '确认「没有 QA」'}
        </button>
      </div>
    </div>
  )
}

function versionRoleLabel(
  version: AssetVersion,
  currentPublishedNo: number | null,
  isLatestPending: boolean,
): { text: string; className: string } {
  if (currentPublishedNo === version.version_no) {
    return { text: '当前已发布', className: 'text-ok' }
  }
  if (version.published_at !== null) return { text: '历史已发布', className: 'text-ink-3' }
  if (isLatestPending) return { text: '待人洗', className: 'text-warn' }
  return { text: '未发布', className: 'text-ink-3' }
}

function auditActionLabel(action: string): string {
  if (action === 'publish') return '发布'
  if (action === 'confirm') return '确认字段'
  if (action === 'rollback') return '回滚'
  // 第 39 刀保鲜动作：重新验证（Guru 验证语义最小版，刷新 last_verified_at）
  if (action === 'verify') return '重新验证'
  // 0042 负向出口动作：留痕列不裸显英文码
  if (action === 'discard_revision') return '放弃修订'
  if (action === 'discard_asset') return '废弃'
  // 第 26 刀：连接层导出留痕（22 刀起在写）不再裸显英文码；留痕列已带操作者，
  // 标签点明「导出 · MCP」与血缘导出块同口径
  if (action === 'export') return '导出 · MCP'
  // 第 41 刀改价留痕（action='price_change'）是产品档，不进资产留痕视图；
  // 这里兜底中文化，实际按 assetId 过滤时天然排除。
  if (action === 'price_change') return '改价'
  return action
}

/** 换正文文件校验（对齐登记抽屉口径）：.txt/.md、≤2MB、非空；后端闸门兜底。 */
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024

function validateTextFile(file: File): string | null {
  const lower = file.name.toLowerCase()
  if (!lower.endsWith('.txt') && !lower.endsWith('.md')) return '仅接受 .txt 或 .md 文本文件'
  if (file.size > MAX_UPLOAD_BYTES) return '文件超过 2MB 上限'
  if (file.size === 0) return '空文件不能上传'
  return null
}

export default function AssetDetailPage() {
  const { id } = useParams()
  const assetId = Number(id)
  const [searchParams] = useSearchParams()
  const location = useLocation()

  // 第 30 刀：从缺口「去补文档」登记成功跳入时带一次性提示（navigate state，
  // useState 初始化器只取一次——刷新/重进不带）。成功链先例：publish/rollback
  // 等 SuccessBanner note。
  const [registerNote] = useState(
    () => (location.state as { registerNote?: string } | null)?.registerNote ?? null,
  )

  const detailFetcher = useCallback(() => {
    if (!Number.isInteger(assetId) || assetId <= 0) {
      return Promise.reject(new ApiError('资产不存在', 404, null))
    }
    return api.getAsset(assetId)
  }, [assetId])
  const detailQ = useApiData(detailFetcher)
  const { reload: reloadDetailData } = detailQ
  const detail = detailQ.state.phase === 'ok' ? detailQ.state.data : null

  // 第 43 刀深链 ?verify=1（总览「被踩最多」的「去重新验证」跳入）：把「重新
  // 验证」块滚入视口并短暂高亮——只定位，不自动点按钮（深链触发写是坏模式，
  // 刷新/分享链接会写库）。只执行一次：验证动作会 reload detail，不重复高亮。
  const verifyParam = searchParams.get('verify')
  const verifyBlockRef = useRef<HTMLDivElement>(null)
  const [verifyHighlight, setVerifyHighlight] = useState(false)
  const verifyScrolled = useRef(false)
  useEffect(() => {
    if (verifyParam !== '1' || verifyScrolled.current) return
    const el = verifyBlockRef.current
    if (el === null) return
    verifyScrolled.current = true
    // 动效降级：偏好减弱动画时用即时滚动（同 CSS 的 reduced-motion 纪律）
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
    setVerifyHighlight(true)
    const timer = window.setTimeout(() => setVerifyHighlight(false), 1600)
    return () => window.clearTimeout(timer)
  }, [verifyParam, detail])

  const productId = detail?.product?.id ?? null
  const productFetcher = useCallback(
    () => (productId !== null ? api.getProduct(productId) : Promise.resolve(null)),
    [productId],
  )
  const productQ = useApiData(productFetcher)
  const product: Product | null = productQ.state.phase === 'ok' ? productQ.state.data : null

  const auditFetcher = useCallback(
    () => (Number.isInteger(assetId) && assetId > 0 ? api.listAudit(assetId) : Promise.resolve([])),
    [assetId],
  )
  const auditQ = useApiData(auditFetcher)
  const { reload: reloadAudit } = auditQ
  const audits = auditQ.state.phase === 'ok' ? auditQ.state.data : null

  const [confirmOpen, setConfirmOpen] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publishGate, setPublishGate] = useState<PublishGateDetail | null>(null)
  const [publishError, setPublishError] = useState<unknown>(null)
  const [publishedNote, setPublishedNote] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<unknown>(null)
  // 重新验证（第 39 刀保鲜）：仅已发布资产（后端 409 闸门同口径）
  const [verifying, setVerifying] = useState(false)
  const [verifyError, setVerifyError] = useState<unknown>(null)
  const [verifyNote, setVerifyNote] = useState<string | null>(null)
  const [openingRevision, setOpeningRevision] = useState(false)
  const [revisionError, setRevisionError] = useState<unknown>(null)
  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null)
  const [rollingBack, setRollingBack] = useState(false)
  const [rollbackError, setRollbackError] = useState<unknown>(null)
  const [rollbackNote, setRollbackNote] = useState<string | null>(null)
  // 生命周期出口（0042）：换正文/放弃修订/废弃
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadFieldError, setUploadFieldError] = useState<string | null>(null)
  const [uploadingBytes, setUploadingBytes] = useState(false)
  const [uploadError, setUploadError] = useState<unknown>(null)
  const [uploadNote, setUploadNote] = useState<string | null>(null)
  const [discardRevOpen, setDiscardRevOpen] = useState(false)
  const [discardingRevision, setDiscardingRevision] = useState(false)
  const [discardRevError, setDiscardRevError] = useState<unknown>(null)
  const [discardRevNote, setDiscardRevNote] = useState<string | null>(null)
  const [discardAssetOpen, setDiscardAssetOpen] = useState(false)
  const [discardingAsset, setDiscardingAsset] = useState(false)
  const [discardAssetError, setDiscardAssetError] = useState<unknown>(null)
  const [discardAssetNote, setDiscardAssetNote] = useState<string | null>(null)

  const reloadDetail = useCallback(() => {
    reloadDetailData()
    reloadAudit()
  }, [reloadDetailData, reloadAudit])

  const versions = detail?.versions ?? []
  const latest = versions.length > 0 ? versions[versions.length - 1] : null
  const currentPublishedNo = detail?.current_published_version_no ?? null
  const publishedVersion =
    currentPublishedNo !== null ? versions.find((v) => v.version_no === currentPublishedNo) ?? null : null
  const unpublished = versions.find((v) => v.published_at === null) ?? null
  const revising = detail?.revising === true
  const activeVersion = revising
    ? (unpublished ?? latest)
    : detail?.status === 'pending_review'
      ? latest
      : (publishedVersion ?? latest)
  const editable = activeVersion !== null && activeVersion.published_at === null && (revising || detail?.status === 'pending_review')

  // 引用回放锚定（ADR 0007）：?v=N 由引用芯片带入，指向引用所指的那一版。
  // 仅当 N 是该资产真实存在的版本时生效；无参数/无效参数时行为不变。
  const anchorParam = searchParams.get('v')
  const anchorVersionNo =
    anchorParam !== null && /^\d+$/.test(anchorParam) ? Number(anchorParam) : null
  const anchorVersion =
    anchorVersionNo !== null ? versions.find((v) => v.version_no === anchorVersionNo) ?? null : null
  const anchored = anchorVersion !== null

  // UX-B 只读证据视图：anchor 指向「已发布的不可变快照」时进入只读——主栏展示
  // 该版正文与快照字段，全部写动作隐藏。判据只要求 anchor 已发布：引用芯片恒指向
  // 已发布版（检索只回已发布），撞上「当前已发布版」恰恰是最常见情形，必须能看见
  // 该版原文（否则引用仍不可核验）。anchor 指向未发布草稿（手改 URL 才会发生）时
  // 仍是治理编辑页——那正是要去编辑的那一版。
  const readOnlyEvidence = anchorVersion !== null && anchorVersion.published_at !== null
  const evidenceVersionNo = readOnlyEvidence ? anchorVersionNo : null

  // 只读证据视图下主栏字段也锁到 anchor 那一版：正文与字段必须同属一个快照，
  // 否则会出现「正文 v1、字段 v3」的自相矛盾。其余情况仍是工作版本（editable 数据源不动）。
  const viewedVersion = readOnlyEvidence ? anchorVersion : activeVersion

  const fieldViews = useMemo<FieldView[]>(() => {
    if (viewedVersion === null || product === null) return []
    return Object.entries(product.spec_schema).map(([field, rule]) =>
      toFieldView(viewedVersion, field, rule.required === true),
    )
  }, [viewedVersion, product])

  // dialogue 的唯一结构化字段：qa_pairs（ADR 0035）；编辑器读它的数组值
  const qaView = useMemo<FieldView | null>(
    () => (detail?.kind === 'dialogue' && viewedVersion !== null
      ? toFieldView(viewedVersion, 'qa_pairs', false)
      : null),
    [detail?.kind, viewedVersion],
  )

  const requiredNames = useMemo(
    () =>
      product === null
        ? []
        : Object.entries(product.spec_schema)
            .filter(([, rule]) => rule.required === true)
            .map(([field]) => field),
    [product],
  )

  const writeBackFields = useMemo(() => {
    if (activeVersion === null || product === null) return []
    return Object.keys(product.spec_schema).filter(
      (field) => resolveFieldValue(activeVersion, field) !== null,
    )
  }, [activeVersion, product])

  const retryWash = async () => {
    if (detail === null || retrying) return
    setRetrying(true)
    setRetryError(null)
    try {
      await api.retryMachineWash(detail.id)
      reloadDetail()
    } catch (err) {
      setRetryError(err)
    } finally {
      setRetrying(false)
    }
  }

  // 重新验证（第 39 刀保鲜）：刷新 last_verified_at + audit 留痕（后端闸门：
  // 只有已发布资产可验证，未发布 409）
  const verifyAsset = async () => {
    if (detail === null || verifying) return
    setVerifying(true)
    setVerifyError(null)
    try {
      const updated = await api.verifyAsset(detail.id)
      setVerifyNote(
        `已重新验证：保鲜时间刷新为 ${formatDateTime(updated.last_verified_at)}。`,
      )
      reloadDetail()
    } catch (err) {
      setVerifyError(err)
    } finally {
      setVerifying(false)
    }
  }

  const publish = async () => {
    if (detail === null || publishing) return
    setPublishing(true)
    setPublishGate(null)
    setPublishError(null)
    try {
      const updated = await api.publishAsset(detail.id)
      setConfirmOpen(false)
      setPublishedNote(
        `已发布：v${updated.current_published_version_no ?? '—'} 成为当前已发布版本${
          updated.product !== null ? `，确认过的规格已写回商品「${updated.product.name}」` : ''
        }。`,
      )
      reloadDetail()
    } catch (err) {
      setConfirmOpen(false)
      const gate = parsePublishGate(err)
      if (gate !== null) setPublishGate(gate)
      else setPublishError(err)
    } finally {
      setPublishing(false)
    }
  }

  const openRevision = async () => {
    if (detail === null || openingRevision) return
    setOpeningRevision(true)
    setRevisionError(null)
    try {
      await api.openRevision(detail.id)
      reloadDetail()
    } catch (err) {
      setRevisionError(err)
    } finally {
      setOpeningRevision(false)
    }
  }

  const rollback = async () => {
    if (detail === null || rollbackTarget === null || rollingBack) return
    setRollingBack(true)
    setRollbackError(null)
    try {
      const updated = await api.rollbackAsset(detail.id, rollbackTarget)
      setRollbackTarget(null)
      setRollbackNote(
        `已回滚：当前已发布版本回到 v${updated.current_published_version_no ?? rollbackTarget}${
          updated.product !== null ? `，规格已按该版写回商品「${updated.product.name}」` : ''
        }。`,
      )
      reloadDetail()
    } catch (err) {
      setRollbackTarget(null)
      setRollbackError(err)
    } finally {
      setRollingBack(false)
    }
  }

  // 生命周期出口三件（0042）：闸门与后端同口径——换正文=待人洗未发布版；
  // 放弃修订=有 version_no>1 的未发布版；废弃=已接入且从未发布（指针空）。
  // 第 46 刀：视频资产不出换正文（ADR 0047——正文由转写字段承载、字节是切片，
  // 换字节会把键写成 .mp4 装文本并清空转写；后端 409 同口径）。
  const canUploadBytes = editable && activeVersion !== null && detail?.kind !== 'video'
  const canDiscardRevision = unpublished !== null && unpublished.version_no > 1
  const canDiscardAsset = detail?.status === 'ingested' && currentPublishedNo === null

  const pickUploadFile = () => {
    if (uploadingBytes) return
    setUploadFieldError(null)
    fileInputRef.current?.click()
  }

  const onUploadFilePicked = (files: FileList | null) => {
    const file = files?.[0] ?? null
    if (file === null) return
    const err = validateTextFile(file)
    if (err !== null) {
      setUploadFieldError(err)
      return
    }
    setUploadFieldError(null)
    setUploadFile(file) // 只暂存：确认对话框里明示后果，确认才上传
  }

  const replaceBytes = async () => {
    if (detail === null || activeVersion === null || uploadFile === null || uploadingBytes) return
    setUploadingBytes(true)
    setUploadError(null)
    try {
      const form = new FormData()
      form.append('file', uploadFile)
      const versionNo = activeVersion.version_no
      await api.replaceVersionBytes(detail.id, versionNo, form)
      setUploadFile(null)
      setUploadNote(
        `已替换 v${versionNo} 正文：机洗已按新字节重跑，已确认/继承的字段保留。`,
      )
      reloadDetail()
    } catch (err) {
      setUploadError(err)
    } finally {
      setUploadingBytes(false)
    }
  }

  const discardRevision = async () => {
    if (detail === null || discardingRevision) return
    setDiscardingRevision(true)
    setDiscardRevError(null)
    try {
      const versionNo = unpublished?.version_no ?? '—'
      await api.discardRevision(detail.id)
      setDiscardRevOpen(false)
      setDiscardRevNote(`已放弃修订：v${versionNo} 已删除，现在可以回滚或重新开修订。`)
      reloadDetail()
    } catch (err) {
      setDiscardRevOpen(false)
      setDiscardRevError(err)
    } finally {
      setDiscardingRevision(false)
    }
  }

  const discardAsset = async () => {
    if (detail === null || discardingAsset) return
    setDiscardingAsset(true)
    setDiscardAssetError(null)
    try {
      await api.discardAsset(detail.id)
      setDiscardAssetOpen(false)
      setDiscardAssetNote('已废弃：资产从列表隐藏，全部字节已清除，留痕保留。')
      reloadDetail()
    } catch (err) {
      setDiscardAssetOpen(false)
      setDiscardAssetError(err)
    } finally {
      setDiscardingAsset(false)
    }
  }

  const backLink = (
    <Link
      to="/platform/assets"
      className="mb-4 inline-flex items-center gap-1 text-[13px] text-ink-3 transition-colors duration-150 hover:text-accent-strong"
    >
      <ArrowLeft aria-hidden size={13} />
      中台 · 资产
    </Link>
  )

  if (detailQ.state.phase === 'loading') {
    return (
      <div>
        {backLink}
        <LoadingHint text="加载资产详情…" />
      </div>
    )
  }

  if (detailQ.state.phase === 'error') {
    if (detailQ.state.error.status === 404) {
      return (
        <div>
          {backLink}
          <div className="panel px-6 py-14 text-center">
            <div className="text-[14px] font-medium text-ink">资产不存在</div>
            <p className="mt-1.5 text-[13px] text-ink-3">这个资产 ID 没有对应记录，可能已被删除或从未登记。</p>
            <Link to="/platform/assets" className="btn btn-secondary mt-5">
              返回资产列表
            </Link>
          </div>
        </div>
      )
    }
    return (
      <div>
        {backLink}
        <ErrorBanner error={detailQ.state.error} onRetry={detailQ.reload} />
      </div>
    )
  }

  if (detail === null) return null

  const pub = detail.publishability
  const fieldsNote = readOnlyEvidence
    ? '只读证据视图 · 快照字段只读'
    : editable
      ? revising
        ? '修订中：继承的确认值可直接发布，改动会丢掉继承标记'
        : '待人洗版本：机洗值可一键确认，弃权字段可补填'
      : detail.status === 'published'
        ? '当前已发布版本 · 只读（已发布字段不可编辑）'
        : '机洗未完成 · 无可编辑字段'

  const confirmBody = (
    <div className="space-y-2">
      {product !== null ? (
        writeBackFields.length > 0 ? (
          <div>
            发布将把{' '}
            <span className="font-medium text-ink">{writeBackFields.join('、')}</span>{' '}
            写回商品「{product.name}」的规格字段。
          </div>
        ) : (
          <div>该资产挂了商品，但当前没有可写回的规格字段值。</div>
        )
      ) : (
        <div>此资产未挂商品：发布不写回规格，只作为已发布内容存在。</div>
      )}
      <div>
        v{activeVersion?.version_no ?? '—'} 将成为当前已发布版本
        {currentPublishedNo !== null
          ? `（线上版本从 v${currentPublishedNo} 前移）`
          : '（该资产的第一个已发布版本）'}
        ，此后成为线上口径，可被引用与导出。
      </div>
    </div>
  )

  return (
    <div>
      {backLink}

      {publishedNote !== null ? <SuccessBanner>{publishedNote}</SuccessBanner> : null}
      {rollbackNote !== null ? <SuccessBanner>{rollbackNote}</SuccessBanner> : null}
      {uploadNote !== null ? <SuccessBanner>{uploadNote}</SuccessBanner> : null}
      {discardRevNote !== null ? <SuccessBanner>{discardRevNote}</SuccessBanner> : null}
      {discardAssetNote !== null ? <SuccessBanner>{discardAssetNote}</SuccessBanner> : null}
      {registerNote !== null ? <SuccessBanner>{registerNote}</SuccessBanner> : null}
      {readOnlyEvidence ? (
        <InfoBanner
          action={
            <Link to={`/platform/assets/${detail.id}`} className="btn btn-ghost btn-sm shrink-0">
              去工作版本
            </Link>
          }
        >
          只读证据视图：引用指向 v{anchorVersionNo} 的不可变快照。要改内容，请在待人洗版本上开修订。
        </InfoBanner>
      ) : anchored ? (
        <div className="mb-3 text-xs leading-5 text-accent-strong">
          正在查看 v{anchorVersionNo} · 引用回放锚定——本页由引用芯片跳入，该版本已在下方版本列表中高亮。
        </div>
      ) : null}
      {retryError !== null ? <ErrorBanner error={retryError} /> : null}
      {verifyNote !== null ? <SuccessBanner>{verifyNote}</SuccessBanner> : null}
      {verifyError !== null ? <ErrorBanner error={verifyError} /> : null}
      {revisionError !== null ? <ErrorBanner error={revisionError} /> : null}
      {rollbackError !== null ? <ErrorBanner error={rollbackError} /> : null}
      {uploadError !== null ? <ErrorBanner error={uploadError} onRetry={pickUploadFile} /> : null}
      {discardRevError !== null ? <ErrorBanner error={discardRevError} /> : null}
      {discardAssetError !== null ? <ErrorBanner error={discardAssetError} /> : null}
      {uploadFieldError !== null ? (
        <div className="mb-3 rounded-[6px] border border-[rgba(180,35,24,0.2)] bg-[rgba(180,35,24,0.04)] px-3 py-2 text-[13px] leading-5 text-danger">
          {uploadFieldError}
        </div>
      ) : null}
      {publishError !== null ? <ErrorBanner error={publishError} onRetry={() => setConfirmOpen(true)} /> : null}

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2.5">
            {detail.title ?? '未命名资产'}
            <StatusBadge
              status={detail.status}
              failed={detail.status === 'ingested' && detail.last_error !== null}
              revising={revising}
              publishedVersionNo={currentPublishedNo}
            />
            <KindChip kind={detail.kind} />
            {isStale(detail.last_verified_at) ? (
              <span
                className="badge badge-review"
                title="距上次验证超过 90 天：该资产的检索证据已降权，点侧栏「重新验证」刷新保鲜时间"
              >
                未验证 &gt;90 天
              </span>
            ) : null}
            <span className="font-mono text-sm font-normal text-ink-3">{formatAssetId(detail.id)}</span>
          </span>
        }
        desc={
          readOnlyEvidence
            ? `只读证据：引用指向 v${anchorVersionNo} 的已发布不可变快照。`
            : revising
              ? `修订中：线上仍引用 v${currentPublishedNo ?? '—'}；发布修订后指针前移。`
              : detail.status === 'pending_review'
                ? '机洗已完成：确认机洗值、补填必填项后可发布。'
                : detail.status === 'published'
                  ? '已发布版本是只读证据；线上内容以此版本为准。'
                  : '已接入：机洗未完成或失败，重试成功后进入待人洗。'
        }
      />

      <div className="grid items-start gap-4 lg:grid-cols-[1fr_340px]">
        {/* 主栏 */}
        <div className="min-w-0 space-y-4">
          {detail.status === 'ingested' ? (
            <div className="panel">
              <div className="flex items-start gap-3 border-b border-line-2 px-4 py-3.5">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] bg-[rgba(180,35,24,0.07)] text-danger">
                  <Warning aria-hidden size={16} weight="bold" />
                </span>
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-ink">
                    机洗未完成 · 留在已接入
                  </div>
                  <div className="mt-0.5 text-[13px] leading-6 text-ink-2">
                    {detail.last_error ?? '尚未安排机洗。'}
                  </div>
                  <div className="mt-0.5 text-xs text-ink-3">
                    只有治理台能看到这条资产，不会出现在任何对外表面。
                  </div>
                </div>
                <span className="flex-1" />
                <button
                  type="button"
                  className="btn btn-secondary btn-sm shrink-0"
                  disabled={retrying}
                  onClick={() => void retryWash()}
                >
                  <ArrowsClockwise aria-hidden size={13} />
                  {retrying ? '重试中…' : '重试机洗'}
                </button>
              </div>
              <div className="px-4 py-3 text-[13px] leading-6 text-ink-3">
                机洗成功前没有可处理的字段；就地重试不会产生新版本。
              </div>
            </div>
          ) : null}

          {evidenceVersionNo !== null ? (
            <VersionTextPanel
              assetId={detail.id}
              versionNo={evidenceVersionNo}
              title="该版本正文"
              note="引用指向的已发布不可变快照 · 只读"
              emptyText="该版本正文为空。"
            />
          ) : null}

          {!readOnlyEvidence && detail.kind === 'dialogue' && activeVersion !== null && qaView !== null ? (
            <>
              <VersionTextPanel
                assetId={detail.id}
                versionNo={activeVersion.version_no}
                title="对话转写"
                note="登记时的转写原文（只读）；发布后按轮成块入检索"
                errorLabel="转写"
              />
              <QaPairsEditor
                key={`${activeVersion.version_no}:${qaView.source ?? 'none'}:${JSON.stringify(qaView.qaPairs ?? [])}`}
                assetId={detail.id}
                versionNo={activeVersion.version_no}
                view={qaView}
                editable={editable}
                onSaved={reloadDetail}
              />
            </>
          ) : null}

          {detail.product !== null && productQ.state.phase === 'loading' ? (
            <div className="panel">
              <LoadingHint text="加载商品规格字段…" />
            </div>
          ) : (
            <div className="panel">
              <div className="panel-title flex-wrap">
                <span>结构化字段 · v{viewedVersion?.version_no ?? '—'}</span>
                <span className="text-xs font-normal text-ink-3">{fieldsNote}</span>
                {!readOnlyEvidence && requiredNames.length > 0 ? (
                  <span className="text-xs font-normal text-ink-3">
                    （发布必填：{requiredNames.join('、')}）
                  </span>
                ) : null}
              </div>
              {fieldViews.length > 0 ? (
                fieldViews.map((view) => (
                  <FieldRow
                    key={view.field}
                    assetId={detail.id}
                    versionNo={viewedVersion?.version_no ?? 0}
                    view={view}
                    editable={editable && !readOnlyEvidence}
                    onSaved={reloadDetail}
                  />
                ))
              ) : (
                <div className="px-4 py-3.5 text-[13px] leading-6 text-ink-3">
                  {detail.product !== null
                    ? '机洗尚未产出字段（或全部待产出）。'
                    : '未挂商品：这个资产没有规格必填项，发布没有字段闸门。'}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 侧栏 */}
        <div className="space-y-4 lg:sticky lg:top-[72px]">
          <div className="panel px-4 py-4">
            <div className="mb-3 text-xs font-medium text-ink-3">治理动作</div>
            {readOnlyEvidence ? (
              <div className="text-xs leading-5 text-ink-3">
                只读证据视图：本页是引用指向 v{anchorVersionNo} 的不可变快照，不提供发布、修订、
                换正文等治理写动作。要改内容，请回工作版本。
              </div>
            ) : (detail.status === 'pending_review' || revising) && activeVersion !== null ? (
              <div className="space-y-2.5">
                <button
                  type="button"
                  className="btn btn-primary w-full"
                  disabled={!pub.publishable}
                  onClick={() => setConfirmOpen(true)}
                >
                  <SealCheck aria-hidden size={14} weight="bold" />
                  发布 v{activeVersion.version_no}
                </button>
                {!pub.publishable ? (
                  <div className="space-y-1.5">
                    {pub.missing.length > 0 ? (
                      <div className="rounded-[6px] border border-[rgba(180,35,24,0.2)] bg-[rgba(180,35,24,0.04)] px-2.5 py-2 text-xs leading-5 text-danger">
                        缺少必填字段：{pub.missing.join('、')}。请在左侧补填，禁止系统代填。
                      </div>
                    ) : null}
                    {pub.unconfirmed.length > 0 ? (
                      <div className="rounded-[6px] border border-[rgba(154,91,6,0.25)] bg-[rgba(154,91,6,0.05)] px-2.5 py-2 text-xs leading-5 text-warn">
                        待确认字段：{pub.unconfirmed.join('、')}。机洗抽取的值需人工确认后才可信。
                      </div>
                    ) : null}
                  </div>
                ) : product !== null && writeBackFields.length > 0 ? (
                  <div className="text-xs leading-5 text-ink-3">
                    发布将把确认过的 {writeBackFields.join('、')} 写回商品「{product.name}」，并把当前已发布指针
                    {currentPublishedNo !== null ? `从 v${currentPublishedNo} ` : ''}
                    前移到 v{activeVersion.version_no}。
                  </div>
                ) : (
                  <div className="text-xs leading-5 text-ink-3">
                    未挂商品：发布后成为已发布内容，可被引用；不写回规格。
                  </div>
                )}
                {publishGate !== null ? (
                  <div className="space-y-1.5 border-t border-line-1 pt-2.5">
                    {publishGate.missing.length > 0 ? (
                      <div className="text-xs leading-5 text-danger">
                        服务器闸门拦截 · 缺少必填字段：{publishGate.missing.join('、')}
                      </div>
                    ) : null}
                    {publishGate.unconfirmed.length > 0 ? (
                      <div className="text-xs leading-5 text-warn">
                        服务器闸门拦截 · 待确认字段：{publishGate.unconfirmed.join('、')}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {/* 0042 出口：待人洗版换正文；有未发布修订（v>1）可放弃 */}
                <div className="flex flex-wrap items-center gap-2 border-t border-line-1 pt-2.5">
                  {canUploadBytes ? (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      disabled={uploadingBytes}
                      onClick={pickUploadFile}
                    >
                      <UploadSimple aria-hidden size={13} />
                      {uploadingBytes ? '上传中…' : '上传新正文'}
                    </button>
                  ) : null}
                  {canDiscardRevision ? (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm text-danger"
                      disabled={discardingRevision}
                      onClick={() => setDiscardRevOpen(true)}
                    >
                      <XCircle aria-hidden size={13} />
                      {discardingRevision ? '放弃中…' : `放弃修订 v${unpublished?.version_no}`}
                    </button>
                  ) : null}
                </div>
                <div className="text-xs leading-5 text-ink-3">
                  传错了文件可在这里替换正文：旧对象键字节删除，机洗按新正文重跑，已确认字段保留。
                </div>
              </div>
            ) : detail.status === 'ingested' ? (
              <div className="space-y-2.5">
                <div className="text-xs leading-5 text-ink-3">
                  已接入资产需先完成机洗（上方可就地重试），才能进入人洗与发布。
                </div>
                {canDiscardAsset ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm text-danger"
                      disabled={discardingAsset}
                      onClick={() => setDiscardAssetOpen(true)}
                    >
                      <Trash aria-hidden size={13} />
                      {discardingAsset ? '废弃中…' : '废弃这份资产'}
                    </button>
                    <div className="text-xs leading-5 text-ink-3">
                      传错文件的最终出口：从未发布过的失败资产可废弃——字节清除不可恢复，列表随即隐藏。
                    </div>
                  </>
                ) : null}
              </div>
            ) : (
              <div className="space-y-2.5">
                {/* 已发布无在开修订时这是页面唯一主动作：近黑主钮（锚：主按钮近黑） */}
                <button
                  type="button"
                  className="btn btn-primary w-full"
                  disabled={openingRevision}
                  onClick={() => void openRevision()}
                >
                  <PencilSimple aria-hidden size={14} />
                  {openingRevision ? '开修订中…' : '开修订（新待人洗版本）'}
                </button>
                <div className="text-xs leading-5 text-ink-3">
                  修订期间客服仍引用 v{currentPublishedNo ?? '—'}；修订发布后指针前移。同一资产同时只允许一个修订。
                </div>
                {/* 第 39 刀保鲜：重新验证（刷新 last_verified_at + audit 留痕）。
                    NULL=新灌未验证按新鲜处理不降权，只有验证过后超阈值才降权。 */}
                <div
                  ref={verifyBlockRef}
                  className={`space-y-1.5 border-t border-line-1 pt-2.5${verifyHighlight ? ' verify-flash' : ''}`}
                >
                  <button
                    type="button"
                    className="btn btn-secondary w-full"
                    disabled={verifying}
                    onClick={() => void verifyAsset()}
                    title="核对线上口径仍成立：保鲜时间刷新为现在，检索降权重新计时"
                  >
                    <CheckCircle aria-hidden size={13} />
                    {verifying ? '验证中…' : '重新验证'}
                  </button>
                  <div className="text-xs leading-5 text-ink-3">
                    {detail.last_verified_at === null
                      ? '尚未验证过（发布即验证快照）；超 90 天未再验证的资产检索会被降权。'
                      : `最近验证：${formatDateTime(detail.last_verified_at)}；超 90 天未再验证会降权。`}
                  </div>
                </div>
              </div>
            )}

            <div className="mt-4 border-t border-line-1 pt-3">
              <div className="mb-2 text-xs font-medium text-ink-3">元数据</div>
              <dl className="space-y-1.5 text-[13px]">
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">资产 ID</dt>
                  <dd className="min-w-0 font-mono text-xs text-ink-2">{formatAssetId(detail.id)}</dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">种类</dt>
                  <dd className="min-w-0 text-ink-2">
                    <KindChip kind={detail.kind} />
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">来源</dt>
                  <dd className="min-w-0 text-ink-2" title={detail.source_kind}>
                    {sourceKindLabel(detail.source_kind)}
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">所挂商品</dt>
                  <dd className="min-w-0 text-ink-2">
                    {detail.product !== null ? (
                      <Link
                        to="/platform/products"
                        className="text-accent transition-colors duration-150 hover:text-accent-strong hover:underline"
                      >
                        {detail.product.name}（{detail.product.category}）
                      </Link>
                    ) : (
                      '未挂商品（独立存在）'
                    )}
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">线上版本</dt>
                  <dd className="min-w-0 font-mono text-xs text-ink-2">
                    {currentPublishedNo !== null ? `v${currentPublishedNo}` : '未发布'}
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">版本数</dt>
                  <dd className="min-w-0 font-mono text-xs text-ink-2 tabular-nums">
                    {versions.length}
                  </dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 pt-0.5 text-xs text-ink-3">最近验证</dt>
                  <dd className="min-w-0 text-xs text-ink-2">
                    {detail.last_verified_at === null ? (
                      <span title="未验证按新鲜处理，不降权">未验证</span>
                    ) : (
                      <span className={isStale(detail.last_verified_at) ? 'text-warn' : ''}>
                        {formatDateTime(detail.last_verified_at)}
                      </span>
                    )}
                  </dd>
                </div>
              </dl>
            </div>
          </div>

          <div className="panel">
            <div className="panel-title">版本</div>
            {versions.length === 0 ? (
              <div className="px-4 py-3 text-xs text-ink-3">尚无版本（机洗未完成）。</div>
            ) : (
              [...versions].reverse().map((version) => {
                const role = versionRoleLabel(
                  version,
                  currentPublishedNo,
                  (detail.status === 'pending_review' || revising) && version === latest,
                )
                const canRollback =
                  !readOnlyEvidence &&
                  version.published_at !== null &&
                  version.version_no !== currentPublishedNo &&
                  !revising
                const isAnchored = anchored && version.version_no === anchorVersionNo
                return (
                  <div
                    key={version.version_no}
                    className={
                      isAnchored
                        ? 'border-b border-line-1 bg-[rgba(65,118,230,0.06)] px-4 py-2.5 shadow-[inset_3px_0_0_var(--color-accent)] last:border-b-0'
                        : 'border-b border-line-1 px-4 py-2.5 transition-colors duration-150 last:border-b-0 hover:bg-hover'
                    }
                  >
                    <div className="flex items-center gap-2.5">
                      <span
                        className={`w-7 font-mono text-xs ${
                          isAnchored ? 'font-medium text-accent-strong' : 'text-ink-2'
                        }`}
                      >
                        v{version.version_no}
                      </span>
                      {isAnchored ? (
                        <span className="text-[11px] font-medium text-accent-strong">引用锚定</span>
                      ) : null}
                      <span className={`text-[11px] font-medium ${role.className}`}>{role.text}</span>
                      <span className="flex-1" />
                      {canRollback ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => setRollbackTarget(version.version_no)}
                        >
                          <ArrowCounterClockwise aria-hidden size={12} />
                          回滚到此版
                        </button>
                      ) : (
                        <span className="text-xs text-ink-3 tabular-nums">
                          {version.published_at !== null ? formatDateTime(version.published_at) : '—'}
                        </span>
                      )}
                    </div>
                    <div
                      className="mt-1 break-all font-mono text-[11px] leading-4 text-ink-3"
                      title={version.object_key}
                    >
                      {version.object_key}
                    </div>
                  </div>
                )
              })
            )}
          </div>

          <div className="panel">
            <div className="panel-title">留痕</div>
            {auditQ.state.phase === 'loading' ? (
              <div className="px-4 py-3 text-xs text-ink-3">加载留痕…</div>
            ) : audits === null || audits.length === 0 ? (
              <div className="px-4 py-3 text-xs text-ink-3">暂无留痕。</div>
            ) : (
              audits.map((row) => (
                <div
                  key={row.id}
                  className="flex items-center gap-2.5 border-b border-line-1 px-4 py-2 text-xs last:border-b-0"
                >
                  <span className="font-medium text-ink-2">{auditActionLabel(row.action)}</span>
                  <span className="font-mono text-ink-3">v{row.version_no ?? '—'}</span>
                  <span className="text-ink-3">操作者 #{row.operator_id}</span>
                  <span className="flex-1" />
                  <span className="text-ink-3 tabular-nums">{formatDateTime(row.created_at)}</span>
                </div>
              ))
            )}
          </div>

          {/* 血缘（第 20 刀/ADR 0026）：详情加载后独立请求的派生只读视图 */}
          <LineagePanel assetId={detail.id} />
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={`发布 ${formatAssetId(detail.id)} · v${activeVersion?.version_no ?? '—'}`}
        body={confirmBody}
        confirmLabel="确认发布"
        busy={publishing}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void publish()}
      />
      <ConfirmDialog
        open={rollbackTarget !== null}
        title={`回滚 ${formatAssetId(detail.id)} · v${rollbackTarget ?? '—'}`}
        body={
          <div>
            回滚会把当前已发布指针移回 v{rollbackTarget ?? '—'}，并按该版确认字段写回商品。
            仍算一次发布动作；旧切块保留，客服与连接层立即跟随新指针。
          </div>
        }
        confirmLabel="确认回滚"
        busy={rollingBack}
        onCancel={() => setRollbackTarget(null)}
        onConfirm={() => void rollback()}
      />

      {/* 0042 出口三件的确认框与隐藏文件选择器；均明示字节删除不可恢复 */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,.md,text/plain,text/markdown"
        className="hidden"
        onChange={(e) => {
          onUploadFilePicked(e.target.files)
          e.target.value = '' // 允许再次选择同一文件
        }}
      />
      <ConfirmDialog
        open={uploadFile !== null}
        title={`替换正文 ${formatAssetId(detail.id)} · v${activeVersion?.version_no ?? '—'}`}
        body={
          <div className="space-y-2">
            <div>
              将以 <span className="font-medium text-ink">{uploadFile?.name}</span>（{uploadFile === null ? '' : `${Math.ceil(uploadFile.size / 1024)} KB`}
              ）替换 v{activeVersion?.version_no ?? '—'} 的正文。
            </div>
            <div className="text-danger">
              该版旧对象键的字节随即删除，不可恢复；机洗将按新正文重跑，已确认/继承的字段保留。
              线上版本不受影响。
            </div>
          </div>
        }
        confirmLabel="确认上传"
        busy={uploadingBytes}
        onCancel={() => setUploadFile(null)}
        onConfirm={() => void replaceBytes()}
      />
      <ConfirmDialog
        open={discardRevOpen}
        title={`放弃修订 ${formatAssetId(detail.id)} · v${unpublished?.version_no ?? '—'}`}
        body={
          <div className="space-y-2">
            <div>将放弃未发布修订 v{unpublished?.version_no ?? '—'}（不是线上版本）。</div>
            <div className="text-danger">
              该修订版本与字节一并删除，不可恢复；放弃后可回滚到历史版本，或重新开修订。
            </div>
          </div>
        }
        confirmLabel="确认放弃"
        busy={discardingRevision}
        onCancel={() => setDiscardRevOpen(false)}
        onConfirm={() => void discardRevision()}
      />
      <ConfirmDialog
        open={discardAssetOpen}
        title={`废弃资产 ${formatAssetId(detail.id)}`}
        body={
          <div className="space-y-2">
            <div>
              这份资产从未发布过（机洗失败停在已接入），废弃后从列表隐藏，不是删除记录；留痕保留。
            </div>
            <div className="text-danger">全部版本字节将被删除，不可恢复。</div>
          </div>
        }
        confirmLabel="确认废弃"
        busy={discardingAsset}
        onCancel={() => setDiscardAssetOpen(false)}
        onConfirm={() => void discardAsset()}
      />
    </div>
  )
}
