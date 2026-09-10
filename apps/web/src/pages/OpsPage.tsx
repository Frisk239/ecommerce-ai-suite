// 运营 Agent（第 22 刀/ADR 0041）：三步编排轨迹可见、失败可重试、投放前二次确认。
// 对照原型冻结交互：编号圆点（done 绿 / failed 红 / pending 灰）+ 状态徽章 + via
// mono + detail；产出预览带引用芯片 `A-xxxx · vN`（0007：refs 冻结 compose 时刻
// 版本，指针前移不漂移）；无已发布素材时引用区明说「正文由商品规格组装」。
// 编排同步就地执行（gen_material 含 LLM ≤20s，请求回来即稳定态，不轮询演戏）。
// run 不是中台对象（0041）：只在本页回放，不进治理台/检索。「投放发布」是渠道
// 动作（mock：记确认时间），不改变任何资产三态——与治理台的「发布资产」两件事。

import { useCallback, useMemo, useState } from 'react'
import { ArrowsClockwise, PaperPlaneTilt, Play, Robot, Warning } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { OpsRun, OpsStepStatus } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatDateTime } from '../labels'
import CitationChip from '../components/CitationChip'
import ConfirmDialog from '../components/ConfirmDialog'
import { ErrorBanner } from '../components/Banner'
import ActionError from '../components/ActionError'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import ProductSelect from '../components/ProductSelect'

const EMPTY_RUNS: OpsRun[] = []
const EMPTY_PRODUCTS: never[] = []

function StepStatusBadge({ status }: { status: OpsStepStatus }) {
  if (status === 'done') return <span className="badge badge-published">完成</span>
  if (status === 'running') return <span className="badge badge-review">运行中</span>
  if (status === 'failed') return <span className="badge badge-failed">失败 · 可重试</span>
  return <span className="badge badge-ingested">待执行</span>
}

/** 编号圆点：done 绿 / failed 红 / 其余中性灰（token 色，对照原型 step 轨道）。 */
function StepDot({ index, status }: { index: number; status: OpsStepStatus }) {
  const cls =
    status === 'done'
      ? 'border-[rgba(22,107,69,0.25)] bg-[rgba(22,107,69,0.06)] text-ok'
      : status === 'failed'
        ? 'border-[rgba(180,35,24,0.22)] bg-[rgba(180,35,24,0.05)] text-danger'
        : 'border-line-2 bg-fill text-caption'
  return (
    <span
      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs tabular-nums ${cls}`}
    >
      {index + 1}
    </span>
  )
}

function TracePanel({ run }: { run: OpsRun }) {
  return (
    <div className="panel mb-4 overflow-hidden">
      <div className="border-b border-line-1 px-4 py-2.5 text-sm font-semibold text-ink">
        工具调用轨迹
      </div>
      {run.steps.map((step, i) => (
        <div key={step.key} className="flex gap-3 border-b border-line-1 px-4 py-3 last:border-b-0">
          <StepDot index={i} status={step.status} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-ink">{step.name}</span>
              <StepStatusBadge status={step.status} />
              <span className="font-mono text-xs text-caption">{step.via}</span>
            </div>
            {step.detail ? (
              <div
                className={`mt-1 text-[13px] leading-6 ${
                  step.status === 'failed' ? 'text-danger' : 'text-ink-2'
                }`}
              >
                {step.status === 'failed' ? (
                  <Warning aria-hidden size={13} className="mr-1 inline -align-px" />
                ) : null}
                {step.detail}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  )
}

function OutputPanel({ run }: { run: OpsRun }) {
  const output = run.output
  if (output === null) return null
  return (
    <div className="panel mb-4 px-4 py-3.5">
      <div className="mb-2 text-sm font-semibold text-ink">投放文案（产出预览）</div>
      <div className="text-base font-medium text-ink">{output.title}</div>
      <p className="mt-1 max-w-[70ch] whitespace-pre-wrap text-sm leading-7 text-ink-2">{output.body}</p>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-caption">引用依据：</span>
        {output.refs.length > 0 ? (
          output.refs.map((r) => (
            <CitationChip key={`${r.asset_id}-${r.version_no}`} assetId={r.asset_id} version={r.version_no} />
          ))
        ) : (
          <span className="text-xs text-caption">暂无已发布素材可用，正文由商品卖点组装</span>
        )}
      </div>
    </div>
  )
}

export default function OpsPage() {
  const runsFetcher = useCallback(() => api.listOpsRuns(), [])
  const { state, reload } = useApiData(runsFetcher)
  const runs = state.phase === 'ok' ? state.data : EMPTY_RUNS

  const productsFetcher = useCallback(() => api.listProducts(), [])
  const productsQ = useApiData(productsFetcher)
  const products = productsQ.state.phase === 'ok' ? productsQ.state.data : EMPTY_PRODUCTS

  const [productId, setProductId] = useState('')
  const [activeId, setActiveId] = useState<number | null>(null)
  const [busy, setBusy] = useState<null | 'start' | 'retry' | 'deliver'>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  // 默认盯最新一条（列表倒序）；「重置轨迹」清空后回到待编排态，新建 run 再回填
  const active = useMemo(
    () => (activeId === null ? null : (runs.find((r) => r.id === activeId) ?? null)),
    [activeId, runs],
  )
  const latest = runs[0] ?? null
  const shown = active ?? latest
  const hasFailed = shown !== null && shown.steps.some((s) => s.status === 'failed')
  const allDone = shown !== null && shown.steps.every((s) => s.status === 'done')
  const delivered = shown !== null && shown.delivered_at !== null

  const guard = async (action: 'start' | 'retry' | 'deliver', fn: () => Promise<OpsRun>) => {
    if (busy !== null) return
    setBusy(action)
    setError(null)
    try {
      const run = await fn()
      setActiveId(run.id)
      reload()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(null)
    }
  }

  const start = () => {
    if (productId === '') {
      setError('请先选择要编排的商品')
      return
    }
    void guard('start', () => api.createOpsRun(Number(productId)))
  }

  return (
    <div className="max-w-[900px]">
      <PageHeader
        title="业务能力 · 运营 Agent"
        desc="选商品开始编排：读商品卖点 → 厂商模型生成投放文案草稿 → 组装并引用「当时已发布」的素材/切片。每一步工具轨迹可见、失败可重试；投放发布前需要操作者确认——那是渠道动作，不是治理台的「发布资产」。"
        actions={
          <>
            {shown !== null && (hasFailed || allDone || delivered) ? (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  setActiveId(null)
                  setError(null)
                }}
              >
                <ArrowsClockwise aria-hidden size={14} />
                重置轨迹
              </button>
            ) : null}
            {shown !== null && hasFailed ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy !== null}
                onClick={() => void guard('retry', () => api.retryOpsRun(shown.id))}
              >
                <ArrowsClockwise aria-hidden size={14} weight="bold" />
                {busy === 'retry' ? '续跑中…（≤20s）' : '重试失败步骤'}
              </button>
            ) : null}
          </>
        }
      />

      {state.phase === 'error' ? <ErrorBanner error={state.error} onRetry={reload} /> : null}
      {error ? <ActionError message={error} className="mb-4" /> : null}

      <div className="panel mb-4 flex flex-wrap items-center gap-2.5 px-4 py-3">
        <Robot aria-hidden size={17} className="text-accent-strong" />
        <span className="text-sm font-medium text-ink">编排</span>
        <div className="w-48">
          <ProductSelect
            products={products}
            value={productId}
            onChange={setProductId}
            disabled={busy !== null}
            ariaLabel="选择要编排的商品"
            emptyLabel="选择商品…"
            loading={productsQ.state.phase === 'loading'}
          />
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy !== null || productId === ''}
          onClick={start}
        >
          <Play aria-hidden size={14} weight="fill" />
          {busy === 'start' ? '编排中…（≤20s）' : '开始编排'}
        </button>
        <span className="flex-1" />
        {shown !== null ? (
          <span className="text-sm font-medium text-ink">
            任务：为 {shown.product_name} 生成小红书投放内容
          </span>
        ) : null}
        <span className="font-mono text-xs tabular-nums text-caption">
          {shown !== null ? `P-${String(shown.product_id).padStart(4, '0')}` : ''}
        </span>
        <span className="text-xs text-caption">编排走中台接口，不经连接层</span>
      </div>

      {state.phase === 'loading' ? (
        <div className="panel">
          <SkeletonRows rows={4} />
        </div>
      ) : shown === null ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<Robot aria-hidden size={24} />}
            title="还没有编排任务"
            hint="上面选商品、点「开始编排」：三步轨迹（读商品→生成文案→组装）同步执行，回来即稳定态。生成步依赖厂商模型底座——底座不可用时该步失败，可重试。"
          />
        </div>
      ) : (
        <>
          <TracePanel run={shown} />
          {allDone && !delivered ? <OutputPanel run={shown} /> : null}

          <div className="panel flex flex-wrap items-center gap-3 px-4 py-3.5">
            <div className="min-w-0">
              <div className="text-sm font-medium text-ink">投放发布</div>
              <div className="text-xs leading-5 text-caption">
                把文案投放到渠道。注意：这是运营的渠道动作，不是治理台的「发布资产」，
                不会改变任何资产的三态。
              </div>
            </div>
            <div className="flex-1" />
            {delivered ? (
              <span className="badge badge-published">已投放 · {formatDateTime(shown.delivered_at)}</span>
            ) : (
              <button
                type="button"
                className="btn btn-primary"
                disabled={!allDone || busy !== null}
                onClick={() => setConfirming(true)}
              >
                <PaperPlaneTilt aria-hidden size={14} />
                {busy === 'deliver' ? '投放中…' : '投放发布'}
              </button>
            )}
          </div>

          <ConfirmDialog
            open={confirming}
            title="确认投放发布？"
            body={
              <>
                文案将投放到「小红书 · 店铺号」（演示渠道，v1 mock），投放后进入渠道监测。
                <br />
                这是渠道动作，不改变中台资产状态；素材如需进中台，请在素材中心另行登记。
              </>
            }
            confirmLabel="确认投放"
            busy={busy !== null}
            onCancel={() => setConfirming(false)}
            onConfirm={() => {
              setConfirming(false)
              void guard('deliver', () => api.deliverOpsRun(shown.id))
            }}
          />
        </>
      )}
    </div>
  )
}
