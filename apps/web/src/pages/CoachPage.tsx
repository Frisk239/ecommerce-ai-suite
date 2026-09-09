// 销售考核（第 19 刀/ADR 0040）：题库列表 → 作答抽屉（提交评分）→ 记录回放。
// 题库不落库：从已发布对话的 confirmed QA 对动态推导（弃权/空→转写首问兜底），
// 每题带题源锚芯片 `A-xxxx · vN` 链治理台详情（0007：考的是当时的已发布版本）。
// 打分=LLM rubric 无降级（0040）：请求内同步 ≤20s；LLM 未配置/失败/坏输出=
// 未评分行（琥珀徽章 + 原因 + 「重新评分」），不是错误弹窗——记录恒落库可重评。
// 记录不是中台对象（0027 考核不是资产）：只在本页回放，不进治理台/检索。
// 单操作者 v1 自兼受训者与考官：标准答案作评分参照折叠展示（0040 裁决，诚实口径）。

import { useCallback, useState } from 'react'
import {
  ArrowsClockwise,
  CaretDown,
  CaretRight,
  GraduationCap,
  Warning,
  X,
} from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { CoachQuestion, CoachRecord } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import { formatDateTime } from '../labels'
import { ErrorBanner } from '../components/Banner'
import ActionError from '../components/ActionError'
import AssetAnchorChip from '../components/AssetAnchorChip'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'

const EMPTY_QUESTIONS: CoachQuestion[] = []
const EMPTY_RECORDS: CoachRecord[] = []

// 三维 rubric（原型冻结口径，0040）：键与后端 score JSON 字段同名
const DIMS = [
  { key: 'accurate', label: '口径准确', max: 40 },
  { key: 'evidence', label: '证据贴合', max: 30 },
  { key: 'tone', label: '服务语气', max: 30 },
] as const

function UnscoredBadge() {
  return <span className="badge badge-review">未评分</span>
}

/** 得分三卡 + 评语 + 评分底座徽章（0040：model_name 是打分时刻的底座快照）。 */
function ScorePanel({ record }: { record: CoachRecord }) {
  const score = record.score
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2.5">
        {DIMS.map((dim) => (
          <div key={dim.key} className="rounded-[8px] border border-line-2 bg-canvas px-3 py-2.5">
            <div className="text-xs text-ink-3">{dim.label}</div>
            <div className="mt-1 font-mono text-[20px] font-semibold tabular-nums leading-7 text-ink">
              {score ? score[dim.key] : '—'}
              <span className="text-xs font-normal text-ink-3">/{dim.max}</span>
            </div>
          </div>
        ))}
      </div>
      {score ? (
        <div>
          <span className="field-label">评语</span>
          <p className="rounded-[6px] border border-line-2 bg-canvas px-3 py-2 text-[13px] leading-5 text-ink-2">
            {score.comment}
          </p>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {record.model_name ? (
          <span className="badge badge-published">评分底座：{record.model_name}</span>
        ) : null}
        {record.status === 'unscored' ? (
          <span
            className="flex min-w-0 items-center gap-1.5 text-danger"
            title={record.last_error ?? undefined}
          >
            <Warning aria-hidden size={13} className="shrink-0" />
            <span className="truncate">{record.last_error ?? '评分未完成'}</span>
          </span>
        ) : null}
      </div>
    </div>
  )
}

// ---------- 作答抽屉 ----------

function AnswerDrawer({
  question,
  onClose,
  onScored,
}: {
  question: CoachQuestion
  onClose: () => void
  onScored: () => void
}) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [showStandard, setShowStandard] = useState(false)
  const [result, setResult] = useState<CoachRecord | null>(null)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (busy) return
    if (answer.trim() === '') {
      setError('先写下你的应答话术')
      return
    }
    setBusy(true)
    setError(null)
    try {
      // 同步打分（≤20s）：返回恒 200——scored 带三维分，unscored 带原因可重评
      const record = await api.createCoachAttempt(question.key, answer.trim())
      setResult(record)
      onScored()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(false)
    }
  }

  const rescore = async () => {
    if (busy || result === null) return
    setBusy(true)
    setError(null)
    try {
      const record = await api.rescoreCoachRecord(result.id)
      setResult(record)
      onScored()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="销售考核作答">
      <div className="modal-backdrop absolute inset-0" onClick={busy ? undefined : onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-lg flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <div className="flex-1 text-[14px] font-semibold text-ink">作答与评分</div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭作答抽屉"
          >
            <X aria-hidden size={14} />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {/* AI 扮客 v1=题面即开场（0040 单轮）：顾客气泡直接呈现题面 */}
          <div className="flex flex-col items-start gap-1">
            <span className="text-xs text-ink-3">顾客（题面即开场）</span>
            <p className="max-w-[90%] rounded-[8px] rounded-tl-[2px] border border-line-2 bg-canvas px-3.5 py-2.5 text-[14px] leading-6 text-ink">
              {question.question}
            </p>
            <span className="mt-0.5 flex items-center gap-1.5 text-xs text-ink-3">
              题源：
              <AssetAnchorChip assetId={question.key.asset_id} version={question.key.version_no} />
              {question.asset_title ? <span className="truncate text-caption">{question.asset_title}</span> : null}
            </span>
          </div>

          <div>
            <button
              type="button"
              className="btn btn-ghost btn-sm px-2"
              onClick={() => setShowStandard((v) => !v)}
            >
              {showStandard ? <CaretDown aria-hidden size={12} /> : <CaretRight aria-hidden size={12} />}
              标准答案（评分参照）
              {question.standard_answer === null ? <span className="ml-1 text-caption">· 兜底题，无</span> : null}
            </button>
            {showStandard ? (
              <p className="mt-1.5 rounded-[6px] border border-line-2 bg-canvas px-3 py-2 text-[13px] leading-5 text-ink-2">
                {question.standard_answer ?? '（转写兜底题没有标准答案，评分只看题面与 rubric 另两维）'}
              </p>
            ) : null}
          </div>

          {result === null ? (
            <div>
              <label className="field-label" htmlFor="coach-answer">
                你的应答话术
              </label>
              <textarea
                id="coach-answer"
                className="input min-h-32 w-full resize-y"
                placeholder="以客服口吻回答这位顾客…"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                disabled={busy}
              />
            </div>
          ) : (
            <div>
              <span className="field-label">你的作答（已记录）</span>
              <p className="rounded-[6px] border border-line-2 bg-canvas px-3 py-2 text-[13px] leading-5 text-ink-2">
                {result.trainee_answer}
              </p>
            </div>
          )}

          {result !== null ? (
            <div>
              <span className="field-label">
                {result.status === 'scored' ? '三维得分' : '本次未评分（无降级：打分是考核的本体）'}
              </span>
              <ScorePanel record={result} />
            </div>
          ) : null}

          {error ? <ActionError message={error} /> : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line-2 px-4 py-3">
          {result === null ? (
            <>
              <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
                取消
              </button>
              <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
                <GraduationCap aria-hidden size={14} />
                {busy ? '评分中…（≤20s）' : '提交评分'}
              </button>
            </>
          ) : (
            <>
              {result.status === 'unscored' ? (
                <button type="button" className="btn btn-primary" onClick={() => void rescore()} disabled={busy}>
                  <ArrowsClockwise aria-hidden size={14} />
                  {busy ? '重评中…（≤20s）' : '重新评分'}
                </button>
              ) : null}
              <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
                关闭
              </button>
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

// ---------- 记录回放列表 ----------

function RecordsPanel({
  records,
  statePhase,
  error,
  onRetry,
  onRescored,
}: {
  records: CoachRecord[]
  statePhase: string
  error: unknown
  onRetry: () => void
  onRescored: () => void
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [rescoringId, setRescoringId] = useState<number | null>(null)

  const rescore = async (record: CoachRecord) => {
    if (rescoringId !== null) return
    setRescoringId(record.id)
    try {
      await api.rescoreCoachRecord(record.id)
      onRescored()
    } finally {
      setRescoringId(null)
    }
  }

  return (
    <div className="panel overflow-x-auto">
      <table className="table-gov">
        <thead>
          <tr>
            <th className="w-20">记录</th>
            <th>题面</th>
            <th className="w-36">得分（口径/证据/语气）</th>
            <th className="w-40">评分底座</th>
            <th className="w-44">时间</th>
            <th className="w-28"></th>
          </tr>
        </thead>
        <tbody>
          {statePhase === 'loading' ? (
            <tr>
              <td colSpan={6}>
                <SkeletonRows rows={3} />
              </td>
            </tr>
          ) : statePhase === 'error' ? (
            <tr>
              <td colSpan={6}>
                <ErrorBanner error={error} onRetry={onRetry} />
              </td>
            </tr>
          ) : records.length === 0 ? (
            <tr>
              <td colSpan={6} className="text-[13px] text-ink-3">
                还没有考核记录——从上面题库点一题开始作答。
              </td>
            </tr>
          ) : (
            records.map((r) => {
              const expanded = expandedId === r.id
              return (
                <tr key={r.id} className="group/row">
                  <td colSpan={6} className="p-0">
                    <div
                      className="row-click flex cursor-pointer items-center gap-3 px-3 py-2.5"
                      role="button"
                      aria-expanded={expanded}
                      onClick={() => setExpandedId(expanded ? null : r.id)}
                    >
                      <span className="font-mono text-xs text-ink-3">#{r.id}</span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink" title={r.question_text}>
                        {r.question_text}
                      </span>
                      {r.status === 'scored' && r.score ? (
                        <span className="font-mono text-xs tabular-nums text-ink-2">
                          {r.score.accurate}/40 · {r.score.evidence}/30 · {r.score.tone}/30
                        </span>
                      ) : (
                        <UnscoredBadge />
                      )}
                      <span className="w-36 truncate font-mono text-xs text-ink-3">
                        {r.model_name ?? '—'}
                      </span>
                      <span className="w-40 shrink-0 text-xs tabular-nums text-ink-3">
                        {formatDateTime(r.created_at)}
                      </span>
                      {r.status === 'unscored' ? (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          disabled={rescoringId !== null}
                          onClick={(e) => {
                            e.stopPropagation()
                            void rescore(r)
                          }}
                        >
                          <ArrowsClockwise aria-hidden size={12} />
                          {rescoringId === r.id ? '重评中…' : '重新评分'}
                        </button>
                      ) : null}
                      <span className="row-caret flex items-center text-caption">
                        {expanded ? <CaretDown aria-hidden size={13} /> : <CaretRight aria-hidden size={13} />}
                      </span>
                    </div>
                    {expanded ? (
                      <div className="border-t border-line-2 bg-canvas/60 px-4 py-3">
                        <div className="grid gap-3 lg:grid-cols-2">
                          <div>
                            <span className="field-label">受训者答案（{r.operator_name}）</span>
                            <p className="whitespace-pre-wrap break-words text-[13px] leading-5 text-ink-2">
                              {r.trainee_answer}
                            </p>
                          </div>
                          <div>
                            <span className="field-label">评语</span>
                            <p className="text-[13px] leading-5 text-ink-2">
                              {r.score ? r.score.comment : r.last_error ?? '（未评分）'}
                            </p>
                            {r.standard_answer ? (
                              <>
                                <span className="field-label mt-2">标准答案（参照）</span>
                                <p className="text-[13px] leading-5 text-ink-3">{r.standard_answer}</p>
                              </>
                            ) : null}
                            <span className="field-label mt-2">题源</span>
                            <span className="flex items-center gap-1.5 text-xs text-ink-2">
                              <AssetAnchorChip
                                assetId={r.question_key.asset_id}
                                version={r.question_key.version_no}
                                title="题源对话资产（治理台）"
                                stopPropagation
                              />
                              <span className="text-caption">
                                {r.question_key.source === 'qa'
                                  ? `QA 对 #${r.question_key.pair_index}`
                                  : '转写首问兜底'}
                              </span>
                            </span>
                          </div>
                        </div>
                        {r.status === 'scored' ? <div className="mt-3"><ScorePanel record={r} /></div> : null}
                      </div>
                    ) : null}
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </div>
  )
}

// ---------- 页面 ----------

export default function CoachPage() {
  const questionsFetcher = useCallback(() => api.listCoachQuestions(), [])
  const questionsQ = useApiData(questionsFetcher)
  const recordsFetcher = useCallback(() => api.listCoachRecords(), [])
  const recordsQ = useApiData(recordsFetcher)

  const questions = questionsQ.state.phase === 'ok' ? questionsQ.state.data : EMPTY_QUESTIONS
  const records = recordsQ.state.phase === 'ok' ? recordsQ.state.data : EMPTY_RECORDS

  const [active, setActive] = useState<CoachQuestion | null>(null)
  const reloadAll = () => {
    recordsQ.reload()
  }

  return (
    <div>
      <PageHeader
        title="业务能力 · 销售考核"
        desc="用真实顾客场景做模拟考核：题库从「已发布」对话动态推导（confirmed QA 对逐题，弃权/空则转写首问兜底），未发布的对话不进题库。单轮作答后由厂商模型按三维 rubric（口径准确 40 / 证据贴合 30 / 服务语气 30）打分——打分无降级，底座不可用时该次记「未评分」，可重新评分。考核记录不是中台对象，只在本页回放。"
      />

      <div className="mb-6">
        <div className="panel overflow-x-auto">
          <table className="table-gov">
            <thead>
              <tr>
                <th>题面（顾客问）</th>
                <th className="w-36">题源</th>
                <th className="w-32">评分参照</th>
                <th className="w-16"></th>
              </tr>
            </thead>
            <tbody>
              {questionsQ.state.phase === 'loading' ? (
                <tr>
                  <td colSpan={4}>
                    <SkeletonRows rows={4} />
                  </td>
                </tr>
              ) : questionsQ.state.phase === 'error' ? (
                <tr>
                  <td colSpan={4}>
                    <ErrorBanner error={questionsQ.state.error} onRetry={questionsQ.reload} />
                  </td>
                </tr>
              ) : questions.length === 0 ? (
                <tr>
                  <td colSpan={4}>
                    <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
                      <Empty
                        icon={<GraduationCap aria-hidden size={24} />}
                        title="题库是空的"
                        hint="题库从已发布对话推导：去「AI 客服」会话回流登记、人洗确认 QA 对（或直接发布），再回这里——治理过的口径就是最好的考题。"
                      />
                    </div>
                  </td>
                </tr>
              ) : (
                questions.map((q) => (
                  <tr key={JSON.stringify(q.key)} className="row-click" onClick={() => setActive(q)}>
                    <td className="max-w-[30rem]">
                      <span className="line-clamp-1 text-[13px] leading-5 text-ink" title={q.question}>
                        {q.question}
                      </span>
                    </td>
                    <td>
                      <span className="flex items-center gap-1.5">
                        <AssetAnchorChip
                          assetId={q.key.asset_id}
                          version={q.key.version_no}
                          title={q.asset_title ?? '题源对话资产（治理台）'}
                          stopPropagation
                        />
                        <span className="kind-chip">{q.key.source === 'qa' ? 'QA 对' : '转写兜底'}</span>
                      </span>
                    </td>
                    <td>
                      {q.standard_answer ? (
                        <span className="text-xs text-ink-2" title={q.standard_answer}>
                          有标准答案
                        </span>
                      ) : (
                        <span className="text-xs text-ink-3">兜底题 · 无</span>
                      )}
                    </td>
                    <td>
                      <span className="row-caret flex items-center justify-end text-caption">
                        <CaretRight aria-hidden size={13} />
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mb-2 flex items-baseline gap-2">
        <h2 className="panel-title">考核记录</h2>
        <span className="text-xs text-ink-3">共 {records.length} 条 · 倒序回放</span>
      </div>
      <RecordsPanel
        records={records}
        statePhase={recordsQ.state.phase}
        error={recordsQ.state.phase === 'error' ? recordsQ.state.error : null}
        onRetry={recordsQ.reload}
        onRescored={reloadAll}
      />

      {active ? (
        <AnswerDrawer key={JSON.stringify(active.key)} question={active} onClose={() => setActive(null)} onScored={reloadAll} />
      ) : null}
    </div>
  )
}
