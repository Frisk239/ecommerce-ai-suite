// 资产详情：治理台核心界面。
// 主栏 = 正在处理的版本（字段确认/补填）；侧栏 = 治理动作（发布闸门）、元数据、版本、留痕。
// 只读语义：待人洗以外状态字段不可编辑（已发布版本是只读证据）。

import { useCallback, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowsClockwise,
  CheckCircle,
  PencilSimple,
  Prohibit,
  SealCheck,
  Warning,
} from '@phosphor-icons/react'
import { ApiError, detailText, parsePublishGate, type PublishGateDetail } from '../api/client'
import { api } from '../api/endpoints'
import { resolveFieldValue, toFieldView, type FieldView } from '../api/fields'
import type { AssetVersion, Product } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatDateTime, formatAssetId, sourceKindLabel } from '../labels'
import { ErrorBanner, SuccessBanner } from '../components/Banner'
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
    <div className="border-b border-line-1 px-4 py-2.5 last:border-b-0">
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
                  <span className="tag tag-confirmed">已确认</span>
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
  return action
}

export default function AssetDetailPage() {
  const { id } = useParams()
  const assetId = Number(id)
  const [searchParams] = useSearchParams()

  const detailFetcher = useCallback(() => {
    if (!Number.isInteger(assetId) || assetId <= 0) {
      return Promise.reject(new ApiError('资产不存在', 404, null))
    }
    return api.getAsset(assetId)
  }, [assetId])
  const detailQ = useApiData(detailFetcher)
  const { reload: reloadDetailData } = detailQ
  const detail = detailQ.state.phase === 'ok' ? detailQ.state.data : null

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

  const reloadDetail = useCallback(() => {
    reloadDetailData()
    reloadAudit()
  }, [reloadDetailData, reloadAudit])

  const versions = detail?.versions ?? []
  const latest = versions.length > 0 ? versions[versions.length - 1] : null
  const currentPublishedNo = detail?.current_published_version_no ?? null
  const publishedVersion =
    currentPublishedNo !== null ? versions.find((v) => v.version_no === currentPublishedNo) ?? null : null
  const activeVersion = detail?.status === 'pending_review' ? latest : (publishedVersion ?? latest)
  const editable = detail?.status === 'pending_review' && activeVersion !== null

  // 引用回放锚定（ADR 0007）：?v=N 由引用芯片带入，指向引用所指的那一版。
  // 仅当 N 是该资产真实存在的版本时生效；无参数/无效参数时行为不变。
  const anchorParam = searchParams.get('v')
  const anchorVersionNo =
    anchorParam !== null && /^\d+$/.test(anchorParam) ? Number(anchorParam) : null
  const anchored =
    anchorVersionNo !== null && versions.some((v) => v.version_no === anchorVersionNo)

  const fieldViews = useMemo<FieldView[]>(() => {
    if (activeVersion === null || product === null) return []
    return Object.entries(product.spec_schema).map(([field, rule]) =>
      toFieldView(activeVersion, field, rule.required === true),
    )
  }, [activeVersion, product])

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
  const fieldsNote = editable
    ? '待人洗版本：机洗值可一键确认，弃权字段可补填'
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
      {anchored ? (
        <div className="mb-3 text-xs leading-5 text-accent-strong">
          正在查看 v{anchorVersionNo} · 引用回放锚定——本页由引用芯片跳入，该版本已在下方版本列表中高亮。
        </div>
      ) : null}
      {retryError !== null ? <ErrorBanner error={retryError} /> : null}
      {publishError !== null ? <ErrorBanner error={publishError} onRetry={() => setConfirmOpen(true)} /> : null}

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2.5">
            {detail.title ?? '未命名资产'}
            <StatusBadge
              status={detail.status}
              failed={detail.status === 'ingested' && detail.last_error !== null}
            />
            <KindChip kind={detail.kind} />
            <span className="font-mono text-sm font-normal text-ink-3">{formatAssetId(detail.id)}</span>
          </span>
        }
        desc={
          detail.status === 'pending_review'
            ? '机洗已完成：确认机洗值、补填弃权字段，过发布闸门后即可发布。'
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

          {detail.product !== null && productQ.state.phase === 'loading' ? (
            <div className="panel">
              <LoadingHint text="加载商品规格字段…" />
            </div>
          ) : (
            <div className="panel">
              <div className="panel-title flex-wrap">
                <span>结构化字段 · v{activeVersion?.version_no ?? '—'}</span>
                <span className="text-xs font-normal text-ink-3">{fieldsNote}</span>
                {requiredNames.length > 0 ? (
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
                    versionNo={activeVersion?.version_no ?? 0}
                    view={view}
                    editable={editable}
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
        <div className="space-y-4">
          <div className="panel px-4 py-4">
            <div className="mb-3 text-xs font-medium text-ink-3">治理动作</div>
            {detail.status === 'pending_review' && activeVersion !== null ? (
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
              </div>
            ) : detail.status === 'ingested' ? (
              <div className="text-xs leading-5 text-ink-3">
                已接入资产需先完成机洗（上方可就地重试），才能进入人洗与发布。
              </div>
            ) : (
              <div className="text-xs leading-5 text-ink-3">
                当前已发布 v{currentPublishedNo ?? '—'}；这一版内容不可编辑。改动需要开新版本（修订流在后续刀交付）。
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
                  detail.status === 'pending_review' && version === latest,
                )
                const isAnchored = anchored && version.version_no === anchorVersionNo
                return (
                  <div
                    key={version.version_no}
                    className={
                      isAnchored
                        ? 'border-b border-line-1 bg-[rgba(65,118,230,0.06)] px-4 py-2.5 shadow-[inset_3px_0_0_var(--color-accent)] last:border-b-0'
                        : 'border-b border-line-1 px-4 py-2.5 last:border-b-0'
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
                      <span className="text-xs text-ink-3 tabular-nums">
                        {version.published_at !== null ? formatDateTime(version.published_at) : '—'}
                      </span>
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
                  <span className="font-mono text-ink-3">v{row.version_no}</span>
                  <span className="text-ink-3">操作者 #{row.operator_id}</span>
                  <span className="flex-1" />
                  <span className="text-ink-3 tabular-nums">{formatDateTime(row.created_at)}</span>
                </div>
              ))
            )}
          </div>
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
    </div>
  )
}
