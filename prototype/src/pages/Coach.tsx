import { useState } from 'react'
import { CaretRight, GraduationCap, Play, SealCheck } from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import CitationChip from '../components/CitationChip'
import Empty from '../components/Empty'
import { dispatch, useStore } from '../store/store'

// 销售考核：场景只能从「已发布」对话资产抽取（ADR 0004）。
// 回放页能看到场景来源引用（ADR 0007）。

const AI_FOLLOWUP = [
  '那冬天装冰水呢，会不会很快变温？',
  '杯子放在包里会漏吗？盖上能放平吗？',
]

export default function Coach() {
  const records = useStore((s) => s.coachRecords)
  // 考核读当前底座：顾客扮演与客服共用同一份模型配置
  const activeModel = useStore((s) => s.models.find((m) => m.id === s.activeModelId) ?? s.models[0])
  // 选择器必须返回稳定引用：先取数组再在组件内过滤
  const allAssets = useStore((s) => s.assets)
  const convAssets = allAssets.filter((a) => a.kind === '对话' && a.publishedV != null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [turn, setTurn] = useState(0)
  const [answer, setAnswer] = useState('')
  const [dialog, setDialog] = useState<{ role: 'customer' | 'trainee'; text: string }[]>([])
  const [scored, setScored] = useState<{ dim: string; score: number }[] | null>(null)
  const [replayId, setReplayId] = useState<string | null>(null)

  // 题库从「全部已发布对话资产」动态抽取：新回流发布的对话当天就能成为考题
  const RUBRIC = [
    { dim: '口径准确', max: 40 },
    { dim: '证据引用', max: 30 },
    { dim: '服务语气', max: 30 },
  ]
  const scenarios = convAssets.map((a) => {
    const ver = a.versions.find((x) => x.v === a.publishedV)
    const firstCustomer =
      ver?.content
        .split('\n')
        .find((line) => line.trim().startsWith('顾客：'))
        ?.replace(/^顾客：/, '')
        .trim() ?? ver?.content.slice(0, 40) ?? ''
    return {
      id: a.id,
      source: { assetId: a.id, v: a.publishedV! },
      title: a.title,
      opening: firstCustomer,
      rubric: RUBRIC,
    }
  })

  const scenario = scenarios.find((s) => s.id === activeId)
  const availableScenarios = scenarios

  const start = (id: string) => {
    const sc = scenarios.find((s) => s.id === id)!
    setActiveId(id)
    setTurn(0)
    setAnswer('')
    setDialog([{ role: 'customer', text: sc.opening }])
    setScored(null)
  }

  const submitAnswer = () => {
    if (!answer.trim() || !scenario) return
    const next: { role: 'customer' | 'trainee'; text: string }[] = [
      ...dialog,
      { role: 'trainee', text: answer.trim() },
    ]
    setAnswer('')
    if (turn < AI_FOLLOWUP.length) {
      next.push({ role: 'customer', text: AI_FOLLOWUP[turn] })
      setTurn(turn + 1)
    } else {
      // mock 评分按维度用不同信号：口径=关键词命中、证据=数字/单位、语气=礼貌用语
      const traineeReplies = next.filter((d) => d.role === 'trainee').map((d) => d.text).join(' ')
      const hasCaliber = /保温|小时|度|60|95|12/i.test(traineeReplies)
      const hasEvidence = /\d/.test(traineeReplies)
      const hasTone = /您|呢|～|吗|好的|亲/.test(traineeReplies)
      const ratio = (hit: boolean) => (hit ? 0.85 : 0.55)
      const scores = scenario.rubric.map((r) => {
        const hit = r.dim === '口径准确' ? hasCaliber : r.dim === '证据引用' ? hasEvidence : hasTone
        return { dim: r.dim, score: Math.min(r.max, Math.round(r.max * ratio(hit))) }
      })
      setScored(scores)
      dispatch({ type: 'SAVE_COACH', scenarioId: scenario.id, scores, dialog: next })
    }
    setDialog(next)
  }

  return (
    <div className="p-4 lg:p-6 max-w-[900px]">
      <PageHeader
        title="销售考核"
        desc="上岗前用真实顾客场景做模拟考核。场景只能从已发布对话资产抽取：未发布的对话不进题库，保证考的是被治理过的口径。"
        actions={
          <span
            className="badge-accent"
            title="顾客扮演与客服共用同一份模型配置；到「模型配置」切换底座"
          >
            顾客扮演底座：{activeModel.name}
          </span>
        }
      />

      {!scenario ? (
        <>
          {availableScenarios.length === 0 ? (
            <div className="panel">
              <Empty
                icon={<GraduationCap size={28} />}
                title="题库里没有可用场景"
                hint="场景来自已发布对话资产。先在治理台把对话资产发布，这里才有题可抽。"
              />
            </div>
          ) : (
            <div className="space-y-3">
              {availableScenarios.map((sc) => (
                <div key={sc.id} className="panel px-4 py-3.5 flex flex-wrap items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">{sc.title}</div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <span className="text-xs text-caption">场景来源：</span>
                      <CitationChip assetId={sc.source.assetId} v={sc.source.v} />
                    </div>
                  </div>
                  <button className="btn-primary" onClick={() => start(sc.id)}>
                    <Play size={13} weight="fill" />
                    开始考核
                  </button>
                </div>
              ))}
            </div>
          )}

          {records.length > 0 && (
            <div className="panel mt-4">
              <div className="px-4 py-2.5 border-b border-line-1 text-sm font-semibold text-ink">
                考核记录（点击回放）
              </div>
              {records.map((r) => {
                const sc = scenarios.find((s) => s.id === r.scenarioId)
                const open = replayId === r.id
                return (
                  <div key={r.id}>
                    <button
                      className="step-row items-center w-full text-left"
                      onClick={() => setReplayId(open ? null : r.id)}
                    >
                      <CaretRight
                        size={13}
                        className={`text-caption transition-transform ${open ? 'rotate-90' : ''}`}
                      />
                      <div className="min-w-0 flex-1">
                        <span className="font-mono text-xs text-caption tabular-nums">{r.id}</span>{' '}
                        <span className="text-sm text-ink">{sc?.title}</span>
                        <span className="ml-2 text-xs text-caption tabular-nums">{r.at}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {sc && <CitationChip assetId={sc.source.assetId} v={sc.source.v} />}
                        <span className="tabular-nums text-sm font-semibold text-ink">
                          {r.scores.reduce((a, b) => a + b.score, 0)} 分
                        </span>
                      </div>
                    </button>
                    {open && (
                      <div className="px-4 pb-4 pt-1 space-y-3 bg-fill-60/60 border-t border-line-1">
                        <div className="flex flex-wrap gap-3">
                          {r.model && (
                            <span className="text-xs text-ink-3">
                              底座
                              <span className="ml-1 font-medium text-ink-2">{r.model}</span>
                            </span>
                          )}
                          {r.scores.map((s) => (
                            <span key={s.dim} className="text-xs text-ink-3">
                              {s.dim}
                              <span className="ml-1 tabular-nums font-semibold text-ink">
                                {s.score}
                              </span>
                            </span>
                          ))}
                        </div>
                        {r.dialog.map((d, i) => (
                          <div key={i} className={`flex ${d.role === 'customer' ? '' : 'justify-end'}`}>
                            <div
                              className={`max-w-[80%] rounded-[6px] px-3 py-2 text-sm ${
                                d.role === 'customer'
                                  ? 'bg-base border border-line-2'
                                  : 'bg-accent-soft border border-accent-border/60'
                              }`}
                            >
                              {d.role === 'customer' && (
                                <div className="text-xs text-caption mb-0.5">AI 扮演顾客</div>
                              )}
                              {d.text}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      ) : (
        <div className="panel">
          <div className="px-4 py-3 border-b border-line-1 flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink">{scenario.title}</span>
            <span className="text-xs text-caption">场景来源：</span>
            <CitationChip assetId={scenario.source.assetId} v={scenario.source.v} />
            <span className="flex-1" />
            <button className="btn-ghost btn-sm" onClick={() => setActiveId(null)}>
              退出考核
            </button>
          </div>

          <div className="px-4 py-4 space-y-3">
            {dialog.map((d, i) => (
              <div key={i} className={`flex ${d.role === 'customer' ? '' : 'justify-end'}`}>
                <div className={d.role === 'customer' ? 'bubble-customer' : 'bubble-agent'}>
                  {d.role === 'customer' && (
                    <div className="text-xs text-caption mb-0.5">AI 扮演顾客</div>
                  )}
                  {d.text}
                </div>
              </div>
            ))}
          </div>

          {scored ? (
            <div className="px-4 pb-4">
              <div className="rounded-[6px] border border-emerald-200 bg-emerald-50 px-3.5 py-3">
                <div className="flex items-center gap-2 text-sm font-medium text-emerald-800">
                  <SealCheck size={15} weight="fill" />
                  考核完成 · 总分 {scored.reduce((a, b) => a + b.score, 0)} / 100
                </div>
                <div className="mt-2 space-y-1">
                  {scored.map((s) => (
                    <div key={s.dim} className="flex items-center gap-3 text-sm text-emerald-900">
                      <span className="w-20">{s.dim}</span>
                      <span className="tabular-nums">{s.score}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="px-4 pb-4 flex flex-wrap gap-2">
              <input
                className="input flex-1 min-w-[200px]"
                placeholder={turn < AI_FOLLOWUP.length ? '以销售身份回答顾客…' : '最后一问：补充你的收尾话术'}
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitAnswer()}
              />
              <button className="btn-primary" onClick={submitAnswer} disabled={!answer.trim()}>
                提交回答
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
