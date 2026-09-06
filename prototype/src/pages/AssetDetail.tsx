import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  CheckCircle,
  MinusCircle,
  PencilSimple,
  Prohibit,
  ArrowsClockwise,
  SealCheck,
  Warning,
} from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Confirm from '../components/Confirm'
import { KindBadge, StateBadgeWithRevision } from '../components/StateBadge'
import { dispatch, useStore } from '../store/store'
import { canPublish, productById, requiredFields } from '../store/selectors'
import type { AssetVersion, ExtractField } from '../store/types'

// 资产详情：治理台核心界面。
// 主栏 = 正在处理的版本（字段抽取 + 正文）；侧栏 = 状态、元数据、治理动作、版本历史。
// ?v=N 进入只读证据视图（给引用芯片落地）。

function FieldRow({
  assetId,
  v,
  field,
  required,
  editable,
}: {
  assetId: string
  v: number
  field: ExtractField
  required: boolean
  editable: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draftVal, setDraftVal] = useState(field.value ?? '')

  const missing = required && (!field.value || !field.confirmed)

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5 border-b border-line-1 last:border-b-0">
      <div className="w-24 shrink-0 text-sm text-ink-2">
        {field.key}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </div>

      {editing ? (
        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
          <input
            className="input flex-1"
            value={draftVal}
            autoFocus
            onChange={(e) => setDraftVal(e.target.value)}
            placeholder={`填写${field.key}（只填有出处的值，没有就保持弃权）`}
          />
          <button
            className="btn-primary btn-sm"
            onClick={() => {
              if (draftVal.trim()) {
                dispatch({ type: 'SET_FIELD', assetId, v, key: field.key, value: draftVal.trim() })
              }
              setEditing(false)
            }}
          >
            保存
          </button>
          <button className="btn-ghost btn-sm" onClick={() => setEditing(false)}>
            取消
          </button>
        </div>
      ) : (
        <>
          {field.value ? (
            <div
              className={`text-sm font-medium flex-1 min-w-[120px] ${
                field.confirmed ? 'text-ink' : 'text-ink-2'
              }`}
            >
              {field.value}
              {field.confirmed && field.inherited ? (
                <span className="ml-1.5 text-xs text-caption">继承自已发布版 · 已确认</span>
              ) : field.confirmed ? (
                <span className="ml-1.5 text-xs text-caption">已确认</span>
              ) : (
                <span className="ml-1.5 text-xs text-amber-600">机洗抽取 · 待确认</span>
              )}
            </div>
          ) : (
            // 弃权：原文没有该字段时显式留空并记录，禁止编造（ADR 0009）
            <div className="flex-1 min-w-[120px] flex items-center gap-1.5 text-sm text-caption">
              <Prohibit size={13} />
              弃权 · {field.abstainReason ?? '原文未找到'}
            </div>
          )}
          {missing && (
            <span className="text-xs text-red-600">发布前必填</span>
          )}
          {editable && !editing && (
            <>
              {field.value && !field.confirmed && (
                <button
                  className="btn-ghost btn-sm"
                  title="机洗抽取的值与原文一致时，一键确认即可"
                  onClick={() =>
                    dispatch({
                      type: 'SET_FIELD',
                      assetId,
                      v,
                      key: field.key,
                      value: field.value!,
                    })
                  }
                >
                  <CheckCircle size={13} />
                  确认
                </button>
              )}
              <button
                className="btn-ghost btn-sm"
                onClick={() => {
                  setDraftVal(field.value ?? '')
                  setEditing(true)
                }}
              >
                <PencilSimple size={13} />
                {field.value ? '修改' : '补填'}
              </button>
            </>
          )}
        </>
      )}
    </div>
  )
}

export default function AssetDetail() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const fullState = useStore((s) => s)
  const asset = fullState.assets.find((a) => a.id === id)
  const product = asset?.productId ? productById(fullState, asset.productId) : undefined
  const [confirmPublish, setConfirmPublish] = useState(false)
  const [editingContent, setEditingContent] = useState(false)
  const [contentDraft, setContentDraft] = useState('')

  if (!asset) {
    return (
      <div className="p-6">
        <PageHeader title="资产不存在" />
        <Link to="/platform/assets" className="btn-ghost">
          <ArrowLeft size={14} />
          返回资产列表
        </Link>
      </div>
    )
  }

  const citeV = params.get('v') ? Number(params.get('v')) : null
  // 被谁用：会话引用 / 微调导出 / 考核场景（血缘的「下游」一侧）
  const usedByCount =
    fullState.sessions.reduce(
      (n, s) =>
        n + s.messages.filter((m) => m.citations?.some((c) => c.assetId === asset.id)).length,
      0
    ) +
    fullState.exports.filter((e) => e.refs.some((r) => r.assetId === asset.id)).length +
    fullState.coachRecords.filter((r) => r.scenarioId === asset.id).length
  const usedBy = {
    sessions: fullState.sessions.reduce(
      (n, s) =>
        n + s.messages.filter((m) => m.citations?.some((c) => c.assetId === asset.id)).length,
      0
    ),
    exports: fullState.exports.filter((e) => e.refs.some((r) => r.assetId === asset.id)).length,
    coach: fullState.coachRecords.filter((r) => r.scenarioId === asset.id).length,
    total: usedByCount,
  }
  const review = asset.versions.find((x) => x.role === 'review')
  const published = asset.versions.find((x) => x.v === asset.publishedV)
  // 正在看的版本：引用进来的版本 > 待人洗版本 > 当前已发布版本
  const active: AssetVersion | undefined = citeV
    ? asset.versions.find((x) => x.v === citeV)
    : (review ?? published)
  const readonly = citeV != null && citeV !== review?.v
  const pub = canPublish(fullState, asset.id)

  return (
    <div className="p-4 lg:p-6">
      <div className="mb-4">
        <Link
          to="/platform/assets"
          className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-accent-strong"
        >
          <ArrowLeft size={13} />
          中台 · 资产
        </Link>
      </div>

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2.5">
            {asset.title}
            <StateBadgeWithRevision asset={asset} />
            <KindBadge kind={asset.kind} />
            <span className="font-mono text-sm text-caption tabular-nums">{asset.id}</span>
          </span>
        }
      >
        {readonly && (
          <div className="flex items-center gap-2 h-9 px-3 rounded-[6px] bg-accent-soft border border-accent-border text-sm text-accent-strong">
            <SealCheck size={15} />
            只读证据视图：引用指向 v{citeV} 的不可变快照，编辑请在待人洗版本上进行
          </div>
        )}
      </PageHeader>

      <div className="grid lg:grid-cols-[1fr_320px] gap-4 items-start">
        {/* 主栏 */}
        <div className="space-y-4 min-w-0">
          {/* 机洗失败（已接入） */}
          {asset.state === '已接入' && (
            <div className="panel">
              <div className="flex items-start gap-3 px-4 py-3.5 border-b border-line-1">
                <div className="w-8 h-8 rounded-[6px] bg-red-50 text-red-600 flex items-center justify-center shrink-0">
                  <Warning size={16} />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-ink">
                    机洗{asset.machineWash.status === 'failed' ? '失败' : '未运行'} · 留在已接入
                  </div>
                  <div className="mt-0.5 text-sm text-ink-3 leading-6">
                    {asset.machineWash.reason ?? '尚未安排机洗。'}
                  </div>
                  <div className="mt-0.5 text-xs text-caption">
                    只有治理台能看到这条资产；Agent 检索不会命中它。
                  </div>
                </div>
                <div className="flex-1" />
                <button
                  className="btn-ghost btn-sm shrink-0"
                  disabled={
                    asset.machineWash.status === 'failed' && !asset.sourceContent?.trim()
                  }
                  title={
                    asset.machineWash.status === 'failed' && !asset.sourceContent?.trim()
                      ? '需先完成人工转写'
                      : undefined
                  }
                  onClick={() => dispatch({ type: 'MACHINE_WASH', assetId: asset.id })}
                >
                  <ArrowsClockwise size={13} />
                  {asset.machineWash.status === 'failed' ? '转写后重新机洗' : '运行机洗'}
                </button>
              </div>
              {/* 失败件的恢复前置：先人工转写，写入了才允许重新机洗 */}
              {asset.machineWash.status === 'failed' && (
                <TranscribeBox assetId={asset.id} initial={asset.sourceContent ?? ''} />
              )}
              {asset.versions.length === 0 && !asset.sourceContent?.trim() && (
                <div className="px-4 py-6 text-sm text-caption">
                  机洗完成前没有可处理的版本。
                </div>
              )}
            </div>
          )}

          {/* 字段抽取 */}
          {active && active.fields.length > 0 && (
            <div className="panel">
              <div className="px-4 py-3 border-b border-line-1 flex items-center gap-2">
                <div className="text-sm font-semibold text-ink">结构化字段 · v{active.v}</div>
                {requiredFields(asset).length > 0 && (
                  <span className="text-xs text-caption">
                    （规格文档发布必填：{requiredFields(asset).join('、')}）
                  </span>
                )}
              </div>
              {active.fields.map((f) => (
                <FieldRow
                  key={f.key}
                  assetId={asset.id}
                  v={active.v}
                  field={f}
                  required={requiredFields(asset).includes(f.key)}
                  editable={!readonly && active.role === 'review'}
                />
              ))}
            </div>
          )}

          {/* 正文 */}
          {active && (
            <div className="panel">
              <div className="px-4 py-3 border-b border-line-1 flex flex-wrap items-center gap-2">
                <div className="text-sm font-semibold text-ink">正文 · v{active.v}</div>
                <span className="text-xs text-caption">
                  {active.role === 'published' && '当前已发布版'}
                  {active.role === 'review' && '待人洗工作版本'}
                  {active.role === 'archived' && '历史已发布版（可回溯，不进检索）'}
                </span>
                {active.washNote && (
                  <span className="text-xs text-ink-3 bg-fill-60 border border-line-2 rounded-[4px] px-2 py-0.5">
                    {active.washNote}
                  </span>
                )}
                <div className="flex-1" />
                {!readonly && active.role === 'review' && !editingContent && (
                  <button
                    className="btn-ghost btn-sm"
                    onClick={() => {
                      setContentDraft(active.content)
                      setEditingContent(true)
                    }}
                  >
                    <PencilSimple size={13} />
                    编辑正文
                  </button>
                )}
              </div>
              {editingContent ? (
                <div className="p-3">
                  <textarea
                    className="input w-full min-h-[140px] py-2 leading-6"
                    value={contentDraft}
                    onChange={(e) => setContentDraft(e.target.value)}
                  />
                  <div className="mt-2 flex gap-2 justify-end">
                    <button className="btn-ghost btn-sm" onClick={() => setEditingContent(false)}>
                      取消
                    </button>
                    <button
                      className="btn-primary btn-sm"
                      onClick={() => {
                        dispatch({
                          type: 'SET_CONTENT',
                          assetId: asset.id,
                          v: active.v,
                          content: contentDraft,
                        })
                        setEditingContent(false)
                      }}
                    >
                      保存正文
                    </button>
                  </div>
                </div>
              ) : (
                <p className="px-4 py-3.5 text-sm text-ink-2 leading-7 whitespace-pre-wrap">
                  {active.content}
                </p>
              )}
            </div>
          )}

          {/* 待人洗且无字段：提示 */}
          {active && active.role === 'review' && active.fields.length === 0 && (
            <div className="panel px-4 py-3 text-sm text-ink-3 leading-6">
              这个种类没有结构化必填项。核对正文无误后可直接发布。
            </div>
          )}
        </div>

        {/* 侧栏 */}
        <div className="space-y-4">
          <div className="panel p-4 space-y-4">
            <div>
              <div className="text-xs font-medium text-caption mb-3">治理动作</div>
              {review ? (
                <div className="space-y-2">
                  <button
                    className="btn-primary w-full"
                    disabled={!pub.ok}
                    onClick={() => setConfirmPublish(true)}
                  >
                    <SealCheck size={14} weight="bold" />
                    发布 v{review.v}
                  </button>
                  {!pub.ok ? (
                    <div className="text-xs text-red-600 leading-5 bg-red-50 border border-red-100 rounded-[6px] px-2.5 py-2 space-y-0.5">
                      {pub.missing.length > 0 && (
                        <div>缺少必填字段：{pub.missing.join('、')}。请补填，禁止系统代填。</div>
                      )}
                      {pub.unconfirmed.length > 0 && (
                        <div>待确认字段：{pub.unconfirmed.join('、')}。机洗抽取的值需人工确认后才可信。</div>
                      )}
                    </div>
                  ) : (
                    <div className="text-xs text-caption leading-5">
                      {asset.productId
                        ? `发布将把确认过的字段写回商品 ${asset.productId}，并把当前已发布指针从 v${asset.publishedV ?? '-'} 前移到 v${review.v}。`
                        : `发布后 Agent 可检索；当前已发布指针将指到 v${review.v}。`}
                    </div>
                  )}
                </div>
              ) : asset.state === '已发布' ? (
                <div className="space-y-2">
                  <button
                    className="btn-ghost w-full"
                    onClick={() => dispatch({ type: 'OPEN_REVISION', assetId: asset.id })}
                  >
                    <PencilSimple size={14} />
                    开修订（新待人洗版本）
                  </button>
                  <div className="text-xs text-caption leading-5">
                    修订期间客服仍引用 v{asset.publishedV}；修订发布后指针前移。同一资产同时只允许一个修订。
                  </div>
                </div>
              ) : (
                <div className="text-xs text-caption leading-5">
                  已接入资产需先完成机洗，才能进入人洗与发布。
                </div>
              )}
            </div>

            <div className="border-t border-line-1 pt-3">
              <div className="text-xs font-medium text-caption mb-2">元数据</div>
              <dl className="space-y-1.5 text-sm">
                <MetaRow k="资产 ID" v={asset.id} mono />
                <MetaRow k="种类" v={asset.kind} />
                <MetaRow
                  k="挂载商品"
                  v={
                    product ? (
                      <Link
                        to="/platform/products"
                        className="text-accent-strong hover:underline"
                      >
                        {product.id} {product.name}
                      </Link>
                    ) : (
                      '独立存在'
                    )
                  }
                />
                <MetaRow k="来源（血缘）" v={asset.source} />
                <MetaRow k="对象键" v={`oss://demo-bucket/${asset.id}/source`} mono />
                <MetaRow k="登记时间" v={asset.createdAt} mono />
                {asset.publishedV != null && (
                  <MetaRow k="当前已发布" v={`v${asset.publishedV}`} mono />
                )}
                <MetaRow
                  k="被谁用"
                  v={
                    usedBy.total > 0 ? (
                      <span className="tabular-nums">
                        会话 {usedBy.sessions} · 导出 {usedBy.exports} · 考核 {usedBy.coach}
                      </span>
                    ) : (
                      '暂无引用'
                    )
                  }
                />
              </dl>
            </div>
          </div>

          {/* 版本历史 */}
          <div className="panel">
            <div className="px-4 py-3 border-b border-line-1 text-sm font-semibold text-ink">
              版本历史
            </div>
            {[...asset.versions].reverse().map((ver) => (
              <Link
                key={ver.v}
                to={`/platform/assets/${asset.id}?v=${ver.v}`}
                className={`flex items-center gap-2.5 px-4 py-2.5 border-b border-line-1 last:border-b-0 hover:bg-fill-60 transition-colors ${
                  active?.v === ver.v ? 'bg-accent-soft/50' : ''
                }`}
                title={`查看 v${ver.v} 的不可变快照`}
              >
                <span className="font-mono text-xs text-ink-3 tabular-nums w-7">v{ver.v}</span>
                {ver.role === 'published' ? (
                  <span className="badge-published">
                    <CheckCircle size={11} weight="fill" />
                    当前已发布
                  </span>
                ) : ver.role === 'review' ? (
                  <span className="badge-review">待人洗</span>
                ) : (
                  <span className="badge-ingested">
                    <MinusCircle size={11} />
                    历史版
                  </span>
                )}
                <span className="flex-1" />
                <span className="text-xs text-caption tabular-nums">{ver.createdAt.slice(5)}</span>
              </Link>
            ))}
            {asset.versions.length === 0 && (
              <div className="px-4 py-3 text-xs text-caption">尚无版本（机洗未完成）</div>
            )}
          </div>
        </div>
      </div>

      <Confirm
        open={confirmPublish}
        title={`发布 ${asset.id} · v${review?.v}`}
        body={
          <>
            发布后此版本进入检索索引：客服、考核、素材引用、连接层、微调导出都会以
            <span className="font-mono"> {asset.id} · v{review?.v} </span>
            为证据。
            {asset.productId && requiredFields(asset).length > 0 && (
              <>
                <br />
                确认过的{requiredFields(asset).join('、')}将写回商品 {asset.productId}。
              </>
            )}
          </>
        }
        confirmLabel="确认发布"
        onCancel={() => setConfirmPublish(false)}
        onConfirm={() => {
          dispatch({ type: 'PUBLISH', assetId: asset.id, v: review!.v })
          setConfirmPublish(false)
        }}
      />
    </div>
  )
}

function MetaRow({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0 text-xs text-caption pt-0.5">{k}</dt>
      <dd className={`text-ink-2 min-w-0 break-words ${mono ? 'font-mono text-xs tabular-nums' : ''}`}>
        {v}
      </dd>
    </div>
  )
}

// 失败件的人工转写：把扫描件内容敲成文本，作为重新机洗的前置
function TranscribeBox({ assetId, initial }: { assetId: string; initial: string }) {
  const [val, setVal] = useState(initial)
  const saved = initial === val
  return (
    <div className="px-4 py-3 border-b border-line-1">
      <div className="text-xs font-medium text-ink-3 mb-1.5">
        人工转写（恢复前置）：把扫描件内容敲成文本，保存后才能重新机洗
      </div>
      <textarea
        className="input w-full min-h-[88px] py-2 leading-6"
        placeholder="例：食品经营许可证，编号 JY1140103XXXXXX，有效期至 2028-05-11……"
        value={val}
        onChange={(e) => setVal(e.target.value)}
      />
      <div className="mt-2 flex items-center gap-2">
        <span className="text-xs text-caption">
          {val.trim() ? `已录入 ${val.trim().length} 字，保存后「重新机洗」可用` : '尚未录入'}
        </span>
        <span className="flex-1" />
        <button
          className="btn-ghost btn-sm"
          disabled={!val.trim() || saved}
          onClick={() => dispatch({ type: 'TRANSCRIBE_SOURCE', assetId, content: val })}
        >
          保存转写
        </button>
      </div>
    </div>
  )
}
