import { useState } from 'react'
import { ArrowsClockwise, PaperPlaneTilt, Play, Robot, Warning } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import Confirm from '../components/Confirm'
import CitationChip from '../components/CitationChip'
import { dispatch, useStore } from '../store/store'
import type { OpsStep } from '../store/types'

// 运营 Agent：内部编排走中台接口（不经连接层）。
// 轨迹可见、失败可重试；最后的「投放发布」是渠道动作，与治理台的「发布资产」是两件事，
// 不改变资产三态（除非素材另行回流登记）。

function StepStatus({ status }: { status: OpsStep['status'] }) {
  if (status === 'done') return <span className="badge-published">完成</span>
  if (status === 'running') return <span className="badge-accent">运行中</span>
  if (status === 'failed') return <span className="badge-failed">失败 · 可重试</span>
  return <span className="badge-ingested">待执行</span>
}

export default function Ops() {
  const run = useStore((s) => s.opsRun)
  const products = useStore((s) => s.products)
  const [confirming, setConfirming] = useState(false)

  const product = products.find((p) => p.id === run.productId)
  const hasFailed = run.steps.some((s) => s.status === 'failed')
  const allDone = run.steps.every((s) => s.status === 'done')
  const delivered = run.delivery != null

  return (
    <div className="p-4 lg:p-6 max-w-[900px]">
      <PageHeader
        title="运营 Agent"
        desc="用一句话驱动「读商品卖点 → 调素材中心 → 组装投放文案」。每一步的工具轨迹可见、失败可重试；投放发布前需要操作者确认。"
        actions={
          <>
            {(hasFailed || allDone || delivered) && (
              <button className="btn-ghost" onClick={() => dispatch({ type: 'OPS_RESET' })}>
                <ArrowsClockwise size={14} />
                重置轨迹
              </button>
            )}
            {hasFailed ? (
              <button className="btn-primary" onClick={() => dispatch({ type: 'OPS_RETRY' })}>
                <ArrowsClockwise size={14} weight="bold" />
                重试失败步骤
              </button>
            ) : (
              <button
                className="btn-primary"
                disabled={allDone}
                onClick={() => dispatch({ type: 'OPS_ADVANCE' })}
              >
                <Play size={14} weight="fill" />
                执行下一步
              </button>
            )}
          </>
        }
      />

      <div className="panel mb-4 px-4 py-3 flex flex-wrap items-center gap-2">
        <Robot size={17} className="text-accent-strong" />
        <span className="text-sm font-medium text-ink">
          任务：为 {product?.name} 生成小红书投放内容
        </span>
        <span className="font-mono text-xs text-caption tabular-nums">{run.productId}</span>
        <span className="flex-1" />
        <span className="text-xs text-caption">编排走中台接口，不经连接层</span>
      </div>

      {/* 工具调用轨迹 */}
      <div className="panel mb-4">
        <div className="px-4 py-2.5 border-b border-line-1 text-sm font-semibold text-ink">
          工具调用轨迹
        </div>
        {run.steps.map((step, i) => (
          <div key={step.key} className="step-row">
            <div className="w-6 shrink-0 text-center">
              <span
                className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-mono tabular-nums ${
                  step.status === 'done'
                    ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                    : step.status === 'failed'
                      ? 'bg-red-50 text-red-700 border border-red-200'
                      : 'bg-fill-60 text-caption border border-line-2'
                }`}
              >
                {i + 1}
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-ink">{step.name}</span>
                <StepStatus status={step.status} />
                <span className="text-xs text-caption font-mono">{step.via}</span>
              </div>
              {step.detail && (
                <div
                  className={`mt-1 text-sm leading-6 ${
                    step.status === 'failed' ? 'text-red-600' : 'text-ink-2'
                  }`}
                >
                  {step.status === 'failed' && <Warning size={13} className="inline mr-1 -align-px" />}
                  {step.detail}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 产出预览：文案由 compose 步从「当时已发布」的素材/切片动态组装 */}
      {allDone && !delivered && run.output && (
        <div className="panel mb-4 px-4 py-3.5">
          <div className="text-sm font-semibold text-ink mb-2">投放文案（产出预览）</div>
          <div className="text-base font-medium text-ink">{run.output.title}</div>
          <p className="mt-1 text-sm text-ink-2 leading-7 max-w-[70ch] whitespace-pre-wrap">
            {run.output.body}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-caption">引用依据：</span>
            {run.output.refs.length > 0 ? (
              run.output.refs.map((r, i) => <CitationChip key={i} assetId={r.assetId} v={r.v} />)
            ) : (
              <span className="text-xs text-caption">暂无已发布素材可用，正文由商品卖点组装</span>
            )}
          </div>
        </div>
      )}

      {/* 投放发布：渠道动作，二次确认 */}
      <div className="panel px-4 py-3.5 flex flex-wrap items-center gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-ink">投放发布</div>
          <div className="text-xs text-caption leading-5">
            把文案投放到渠道。注意：这是运营的渠道动作，不是治理台的「发布资产」，不会改变任何资产的三态。
          </div>
        </div>
        <div className="flex-1" />
        {delivered ? (
          <span className="badge-published">已投放 · {run.delivery!.channel}</span>
        ) : (
          <button className="btn-primary" disabled={!allDone} onClick={() => setConfirming(true)}>
            <PaperPlaneTilt size={14} />
            投放发布
          </button>
        )}
      </div>

      <Confirm
        open={confirming}
        title="确认投放发布？"
        body={
          <>
            文案将投放到「小红书 · 店铺号」，投放后进入渠道监测。
            <br />
            这是渠道动作，不改变中台资产状态；素材如需进中台，请在素材中心另行登记。
          </>
        }
        confirmLabel="确认投放"
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          dispatch({ type: 'OPS_DELIVER' })
          setConfirming(false)
        }}
      />
    </div>
  )
}
