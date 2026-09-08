// 唯一的 mock store：模块级内存状态 + localStorage 持久化。
// 客服、考核、素材、切片、连接层读的都是这一份（禁止各页私藏 JSON）。
import { useSyncExternalStore } from 'react'
import { makeSeed } from './seed'
import { askService, detectProduct, requiredFieldsOfVersion } from './selectors'
import type {
  Asset,
  AssetKind,
  ChatMessage,
  CoachRecord,
  ClipCandidate,
  ExportRecord,
  KnowledgeGap,
  MaterialTask,
  ModelConfig,
  OpsRun,
  Product,
  ServiceSession,
  SourceKind,
} from './types'

export interface AppState {
  products: Product[]
  assets: Asset[]
  sessions: ServiceSession[]
  currentSessionId: string | null
  models: ModelConfig[]
  activeModelId: string
  materialTasks: MaterialTask[]
  clips: ClipCandidate[]
  opsRun: OpsRun
  opsFailedOnce: boolean
  exports: ExportRecord[]
  knowledgeGaps: KnowledgeGap[]
  lastRegisteredId: string | null
  coachScenarios: ReturnType<typeof makeSeed>['coachScenarios']
  coachRecords: CoachRecord[]
  nextAssetSeq: number
  nextGapSeq: number
}

const STORAGE_KEY = 'eas-prototype-v4'

function initialState(): AppState {
  const seed = makeSeed()
  return {
    products: seed.products,
    assets: seed.assets,
    sessions: [],
    currentSessionId: null,
    models: seed.models,
    activeModelId: seed.models[0].id,
    materialTasks: seed.materialTasks,
    clips: seed.clips,
    opsRun: seed.opsRun,
    opsFailedOnce: false,
    exports: [],
    knowledgeGaps: seed.knowledgeGaps,
    lastRegisteredId: null,
    coachScenarios: seed.coachScenarios,
    coachRecords: [],
    nextAssetSeq: 7,
    nextGapSeq: 2,
  }
}

function load(): AppState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return initialState()
    const parsed = JSON.parse(raw) as AppState & { __v?: string }
    if (parsed.__v !== STORAGE_KEY) return initialState()
    // 刷新兜底：把流到一半的消息补全（中断标记只留给用户主动停止）
    let finalized = false
    for (const s of parsed.sessions ?? []) {
      for (const m of s.messages ?? []) {
        if (m.streaming) {
          m.text = m.pendingFull ?? m.text
          m.streaming = false
          delete m.pendingFull
          finalized = true
        }
      }
    }
    if (finalized) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...parsed, __v: STORAGE_KEY }))
      } catch {
        /* 纯内存运行 */
      }
    }
    return parsed
  } catch {
    return initialState()
  }
}

let state: AppState = load()
const listeners = new Set<() => void>()

function persist(next: AppState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...next, __v: STORAGE_KEY }))
  } catch {
    /* localStorage 不可用时纯内存运行 */
  }
}

function set(updater: (draft: AppState) => void) {
  const draft = structuredClone(state)
  updater(draft)
  state = draft
  persist(state)
  listeners.forEach((l) => l())
}

export function getState() {
  return state
}

export function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function useStore<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state))
}

export function resetDemo() {
  state = initialState()
  persist(state)
  listeners.forEach((l) => l())
}

// ---------- 工具 ----------

function now() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function nextAssetId(draft: AppState) {
  return `A-${String(draft.nextAssetSeq++).padStart(4, '0')}`
}

// 机洗字段抽取：按字段名给正则（可空可弃权，找不到返回 null，禁止编造）
const FIELD_PATTERNS: Record<string, RegExp[]> = {
  净含量: [/(\d+(?:\.\d+)?\s*(?:ml|mL|毫升|L|升))/],
  保质期: [/(\d+\s*(?:个月|月|天|日|年))/],
  材质: [
    /(316不锈钢|304不锈钢|不锈钢|钛钢|纯钛|钛合金|铝合金|Tritan|PP|PET|玻璃|陶瓷|硅胶)/,
    // 通配必须带显式分隔符（材质：X / 材质为X），否则「未标注材质牌号」会被抽成「牌号」
    /材质[:：为是]\s*([^\s，。；、]{1,10})/,
  ],
}

function extractFieldFromText(key: string, text: string): string | null {
  const patterns = FIELD_PATTERNS[key] ?? [new RegExp(`${key}[:：为是]\\s*([^\\s，。；、]{1,12})`)]
  for (const re of patterns) {
    const m = text.match(re)
    if (m?.[1]) return m[1].trim()
  }
  return null
}

function nextGapId(draft: AppState) {
  return `G-${String(draft.nextGapSeq++).padStart(4, '0')}`
}

function registerAsset(
  draft: AppState,
  input: {
    kind: AssetKind
    title: string
    sourceKind: SourceKind
    sourceNote?: string
    content: string
    productId?: string
    fillsGapId?: string
  },
): Asset {
  const id = nextAssetId(draft)
  const asset: Asset = {
    id,
    kind: input.kind,
    title: input.title,
    state: '已接入',
    sourceKind: input.sourceKind,
    sourceContent: input.content,
    createdAt: now(),
    machineWash: { status: 'pending' },
    versions: [],
    ...(input.sourceNote ? { sourceNote: input.sourceNote } : {}),
    ...(input.productId ? { productId: input.productId } : {}),
    ...(input.fillsGapId ? { fillsGapId: input.fillsGapId } : {}),
  }
  draft.assets.unshift(asset)
  if (input.fillsGapId) {
    const gap = draft.knowledgeGaps.find((g) => g.id === input.fillsGapId)
    if (gap && gap.status === 'open') gap.filledAssetId = id
  }
  return asset
}

function openKnowledgeGap(
  draft: AppState,
  input: { question: string; sessionId?: string; productId?: string },
): KnowledgeGap {
  const gap: KnowledgeGap = {
    id: nextGapId(draft),
    question: input.question,
    createdAt: now(),
    status: 'open',
    ...(input.sessionId ? { sessionId: input.sessionId } : {}),
    ...(input.productId ? { productId: input.productId } : {}),
  }
  draft.knowledgeGaps.unshift(gap)
  return gap
}

// 运维 Agent「调素材中心」步骤对应的任务：首次调用时创建，失败/重试引用同一个 ID
function ensureOpsTask(draft: AppState): MaterialTask {
  const existing = draft.materialTasks.find((t) => t.origin === 'ops')
  if (existing) return existing
  const id = `T-${String(draft.materialTasks.length + 1).padStart(4, '0')}`
  const refs = draft.assets
    .filter((a) => a.publishedV != null)
    .slice(0, 1)
    .map((a) => ({ assetId: a.id, v: a.publishedV! }))
  const task: MaterialTask = {
    id,
    productId: draft.opsRun.productId,
    brief: '钛钢保温杯 · 投放文案（运营 Agent 发起）',
    status: '待质检',
    origin: 'ops',
    output: {
      title: '通勤党的冬天续命杯',
      body: '早上灌的咖啡，下班还是温的。按键一弹就能单手开盖，放包里几乎无感，500ml 刚好一杯的量。',
      refs,
    },
  }
  draft.materialTasks.unshift(task)
  return task
}

// ---------- 流式输出引擎（模块级，不持久化）----------
// 首拍延迟 ~500ms 模拟检索（TTFT），随后每 ~35ms 揭示 1-3 字
const streamTimers = new Map<string, { first: ReturnType<typeof setTimeout>; tick?: ReturnType<typeof setInterval> }>()

export function startStream(sessionId: string, msgId: string) {
  stopStreamTimers(msgId)
  const first = setTimeout(() => {
    const tick = setInterval(() => dispatch({ type: 'STREAM_TICK', sessionId, msgId }), 35)
    streamTimers.set(msgId, { first, tick })
  }, 500)
  streamTimers.set(msgId, { first })
}

function stopStreamTimers(msgId: string) {
  const t = streamTimers.get(msgId)
  if (!t) return
  clearTimeout(t.first)
  if (t.tick) clearInterval(t.tick)
  streamTimers.delete(msgId)
}

function finishStream(draft: AppState, sessionId: string, msgId: string, interrupted: boolean) {
  const s = draft.sessions.find((x) => x.id === sessionId)
  const m = s?.messages.find((x) => x.id === msgId)
  if (m) {
    if (interrupted) m.interrupted = true
    else m.text = m.pendingFull ?? m.text
    m.streaming = false
    delete m.pendingFull
  }
  stopStreamTimers(msgId)
}

// ---------- Actions ----------

export type Action =
  | { type: 'SET_FIELD'; assetId: string; v: number; key: string; value: string }
  | { type: 'SET_CONTENT'; assetId: string; v: number; content: string }
  | { type: 'PUBLISH'; assetId: string; v: number }
  | { type: 'OPEN_REVISION'; assetId: string }
  | { type: 'MACHINE_WASH'; assetId: string }
  | { type: 'TRANSCRIBE_SOURCE'; assetId: string; content: string }
  | { type: 'REGISTER_MANUAL'; kind: AssetKind; title: string; content: string; productId?: string; fillsGapId?: string }
  | { type: 'CREATE_MATERIAL_TASK'; productId: string; brief: string }
  | { type: 'QC_PASS'; taskId: string }
  | { type: 'QC_REJECT'; taskId: string }
  | { type: 'REGISTER_MATERIAL'; taskId: string }
  | { type: 'REGISTER_CLIPS'; clipIds: string[] }
  | { type: 'NEW_SESSION' }
  | { type: 'SEND_MESSAGE'; text: string }
  | { type: 'STREAM_TICK'; sessionId: string; msgId: string }
  | { type: 'STREAM_STOP' }
  | { type: 'END_SESSION' }
  | { type: 'SET_ACTIVE_MODEL'; id: string }
  | { type: 'ADD_MODEL'; input: Omit<ModelConfig, 'id' | 'createdAt'> }
  | { type: 'UPDATE_MODEL'; id: string; patch: Partial<Pick<ModelConfig, 'name' | 'endpoint' | 'provider' | 'params'>> }
  | { type: 'REMOVE_MODEL'; id: string }
  | { type: 'OPS_ADVANCE' }
  | { type: 'OPS_RETRY' }
  | { type: 'OPS_RESET' }
  | { type: 'OPS_DELIVER' }
  | { type: 'CREATE_EXPORT'; assetIds: string[] }
  | { type: 'SAVE_COACH'; scenarioId: string; scores: { dim: string; score: number }[]; dialog: { role: 'customer' | 'trainee'; text: string }[] }
  | { type: 'CONNECT_REGISTER'; title: string; content: string; productId?: string }

export function dispatch(action: Action) {
  set((draft) => {
    switch (action.type) {
      // 人洗：填字段即确认（ADR 0009：人洗改字段和正文）；改动后不再视为继承。
      // store 层闸门：只允许改待人洗工作版本，已发布/历史版本不可变（ADR 0006）
      case 'SET_FIELD': {
        const asset = draft.assets.find((a) => a.id === action.assetId)
        const ver = asset?.versions.find((x) => x.v === action.v)
        if (ver?.role !== 'review') break
        const field = ver.fields.find((f) => f.key === action.key)
        if (field) {
          field.value = action.value
          field.confirmed = true
          field.inherited = false
          delete field.abstainReason
        }
        break
      }

      case 'SET_CONTENT': {
        const asset = draft.assets.find((a) => a.id === action.assetId)
        const ver = asset?.versions.find((x) => x.v === action.v)
        if (ver?.role === 'review') ver.content = action.content
        break
      }

      // 发布：只有操作者能做（ADR 0005）；action 层再校验一次闸门，
      // 不依赖按钮禁用——必填缺失或未确认的发布在这里直接拒绝
      case 'PUBLISH': {
        const asset = draft.assets.find((a) => a.id === action.assetId)
        if (!asset) break
        const target = asset.versions.find((x) => x.v === action.v)
        if (!target) break
        const check = requiredFieldsOfVersion(target.fields)
        if (!check.ok) break
        for (const ver of asset.versions) {
          if (ver.role === 'published') ver.role = 'archived'
        }
        target.role = 'published'
        asset.publishedV = action.v
        asset.state = '已发布'
        if (asset.fillsGapId) {
          const gap = draft.knowledgeGaps.find((g) => g.id === asset.fillsGapId)
          if (gap) {
            gap.status = 'filled'
            gap.filledAssetId = asset.id
          }
        }
        if (asset.productId) {
          const product = draft.products.find((p) => p.id === asset.productId)
          if (product) {
            for (const f of target.fields) {
              if (!f.confirmed || !f.value) continue
              if (product.specSchema.includes(f.key)) product.specs[f.key] = f.value
            }
          }
        }
        break
      }

      // 修订：已发布资产上开待人洗新版本；旧已发布版继续服务；同时最多一个修订（ADR 0006）。
      // 未改动的字段继承上一版的确认状态（修订是增量动作，不该逼操作者重存一遍）
      case 'OPEN_REVISION': {
        const asset = draft.assets.find((a) => a.id === action.assetId)
        if (!asset || asset.publishedV == null) break
        if (asset.versions.some((x) => x.role === 'review')) break
        const maxV = Math.max(...asset.versions.map((x) => x.v))
        const from = asset.versions.find((x) => x.v === asset.publishedV)
        asset.versions.push({
          v: maxV + 1,
          role: 'review',
          createdAt: now(),
          content: from?.content ?? '',
          fields: (from?.fields ?? []).map((f) => ({
            ...f,
            confirmed: f.confirmed,
            inherited: f.confirmed,
          })),
        })
        asset.state = '待人洗'
        break
      }

      // 机洗：把已接入推进待人洗，从原始内容生成工作版本（ADR 0009）。
      // 挂商品的文档按商品 specSchema 抽结构化字段，抽不到就弃权——闸门不因「新摄入」而失守
      case 'MACHINE_WASH': {
        const asset = draft.assets.find((a) => a.id === action.assetId)
        if (!asset || asset.state !== '已接入') break
        if (asset.machineWash.status === 'failed' && !asset.sourceContent?.trim()) break
        asset.machineWash = { status: 'done' }
        const product = asset.productId ? draft.products.find((p) => p.id === asset.productId) : undefined
        const fields =
          product && asset.kind === '文档'
            ? product.specSchema.map((key) => {
                const hit = extractFieldFromText(key, asset.sourceContent ?? '')
                return {
                  key,
                  value: hit,
                  ...(hit ? {} : { abstainReason: '原文未找到' }),
                  confirmed: false,
                  required: true,
                }
              })
            : []
        asset.versions.push({
          v: 1,
          role: 'review',
          createdAt: now(),
          content: asset.sourceContent ?? '（无可解析文本，请人工转写）',
          washNote:
            fields.length > 0
              ? '机洗完成：已按商品类目 schema 抽取结构化字段（可弃权），待人洗核对确认。'
              : '机洗完成：文本层已解析；该资产无结构化必填项，待人洗核对正文。',
          fields,
        })
        asset.state = '待人洗'
        break
      }

      // 失败件的恢复前置：人工先把扫描件转写成文本，才允许重新机洗
      case 'TRANSCRIBE_SOURCE': {
        const asset = draft.assets.find((a) => a.id === action.assetId)
        if (asset && asset.state === '已接入' && action.content.trim()) {
          asset.sourceContent = action.content.trim()
        }
        break
      }

      // 治理台自己的登记入口：操作者把供应商文档等直接写入中台（落入已接入）
      case 'REGISTER_MANUAL': {
        if (!action.title.trim() || !action.content.trim()) break
        registerAsset(draft, {
          kind: action.kind,
          title: action.title.trim(),
          sourceKind: '上传',
          sourceNote: '治理台登记',
          content: action.content.trim(),
          ...(action.productId ? { productId: action.productId } : {}),
          ...(action.fillsGapId ? { fillsGapId: action.fillsGapId } : {}),
        })
        break
      }

      // 素材中心新建生成任务（mock：直接产出成品，引用已发布资产）
      case 'CREATE_MATERIAL_TASK': {
        const id = `T-${String(draft.materialTasks.length + 1).padStart(4, '0')}`
        const published = draft.assets.filter((a) => a.publishedV != null)
        const refs = published.slice(0, 2).map((a) => ({ assetId: a.id, v: a.publishedV! }))
        draft.materialTasks.unshift({
          id,
          productId: action.productId,
          brief: action.brief.trim(),
          status: '待质检',
          output: {
            title: action.brief.trim().split('·').pop()?.trim() || action.brief.trim(),
            body: `围绕「${action.brief.trim()}」生成的投放文案草稿：卖点取自所引已发布资产，口径与中台一致。确认无误后可登记为资产进入治理。`,
            refs,
          },
        })
        break
      }

      case 'QC_PASS': {
        const task = draft.materialTasks.find((t) => t.id === action.taskId)
        if (task && (task.status === '待质检' || task.status === '已打回')) task.status = '已完成'
        break
      }

      case 'QC_REJECT': {
        const task = draft.materialTasks.find((t) => t.id === action.taskId)
        if (task && task.status === '待质检' && !task.registeredAssetId) task.status = '已打回'
        break
      }

      case 'REGISTER_MATERIAL': {
        const task = draft.materialTasks.find((t) => t.id === action.taskId)
        if (!task || task.registeredAssetId || task.status !== '已完成') break
        const asset = registerAsset(draft, {
          kind: '素材',
          title: `素材 · ${task.output?.title ?? task.brief}`,
          sourceKind: '素材生成',
          sourceNote: task.id,
          content: task.output?.body ?? '',
          productId: task.productId,
        })
        task.registeredAssetId = asset.id
        break
      }

      case 'REGISTER_CLIPS': {
        for (const clipId of action.clipIds) {
          const clip = draft.clips.find((c) => c.id === clipId)
          if (!clip || clip.registeredAssetId) continue
          const asset = registerAsset(draft, {
            kind: '视频',
            title: `直播切片 · ${clip.topic} ${clip.timecode}`,
            sourceKind: '切片拣选',
            sourceNote: clip.id,
            content: clip.transcript,
            productId: clip.productId,
          })
          clip.registeredAssetId = asset.id
        }
        break
      }

      case 'NEW_SESSION': {
        const s: ServiceSession = {
          id: `S-${1000 + draft.sessions.length + 25}`,
          startedAt: now(),
          messages: [],
        }
        draft.sessions.unshift(s)
        draft.currentSessionId = s.id
        break
      }

      case 'SEND_MESSAGE': {
        const s = draft.sessions.find((x) => x.id === draft.currentSessionId)
        if (!s) break
        if (s.messages.some((m) => m.streaming)) break
        const customerMsg: ChatMessage = {
          id: `m${s.messages.length + 1}`,
          role: 'customer',
          text: action.text,
          at: now(),
        }
        s.messages.push(customerMsg)
        const reply = askService(draft, action.text)
        if (reply.refused && !reply.toolCall) {
          const gap = openKnowledgeGap(draft, {
            question: action.text,
            sessionId: s.id,
            ...(detectProduct(action.text) ? { productId: detectProduct(action.text)! } : {}),
          })
          reply.gapId = gap.id
        }
        const full = reply.text
        s.messages.push({ ...reply, text: '', streaming: true, pendingFull: full })
        startStream(s.id, reply.id)
        break
      }

      case 'STREAM_TICK': {
        const s = draft.sessions.find((x) => x.id === action.sessionId)
        const m = s?.messages.find((x) => x.id === action.msgId)
        if (!s || !m || !m.streaming || !m.pendingFull) break
        // 每拍 1~3 字，节奏略随机更像真流
        const step = 1 + Math.floor(Math.random() * 3)
        m.text = m.pendingFull.slice(0, m.text.length + step)
        if (m.text.length >= m.pendingFull.length) finishStream(draft, s.id, m.id, false)
        break
      }

      case 'STREAM_STOP': {
        const s = draft.sessions.find((x) => x.id === draft.currentSessionId)
        const m = s?.messages.find((x) => x.streaming)
        if (s && m) finishStream(draft, s.id, m.id, true)
        break
      }

      // 回流登记：会话结束登记为对话资产，只能落到已接入，不能直接已发布（ADR 0005）
      case 'END_SESSION': {
        const s = draft.sessions.find((x) => x.id === draft.currentSessionId)
        if (!s || s.messages.length === 0 || s.registeredAssetId) break
        if (s.messages.some((m) => m.streaming)) break // 答复未落完不能结束回流
        const asset = registerAsset(draft, {
          kind: '对话',
          title: `客服会话回流 ${s.id}`,
          sourceKind: '会话回流',
          sourceNote: s.id,
          content: s.messages
            .map((m) => `${m.role === 'customer' ? '顾客' : '客服'}：${m.text}`)
            .join('\n'),
        })
        s.registeredAssetId = asset.id
        draft.currentSessionId = null
        break
      }

      case 'SET_ACTIVE_MODEL': {
        if (draft.models.some((m) => m.id === action.id)) draft.activeModelId = action.id
        break
      }

      case 'ADD_MODEL': {
        const id = `M-${String(draft.models.length + 1).padStart(2, '0')}`
        draft.models.push({ ...action.input, id, createdAt: now() })
        break
      }

      case 'UPDATE_MODEL': {
        const model = draft.models.find((m) => m.id === action.id)
        if (model) Object.assign(model, action.patch)
        break
      }

      case 'REMOVE_MODEL': {
        // 当前客服底座不可删，至少保留一个基座
        if (action.id === draft.activeModelId) break
        if (draft.models.length <= 1) break
        draft.models = draft.models.filter((m) => m.id !== action.id)
        break
      }

      case 'OPS_ADVANCE': {
        const run = draft.opsRun
        const step = run.steps.find((s) => s.status === 'pending' || s.status === 'running')
        if (!step) break
        if (step.key === 'read-product') {
          step.status = 'done'
          const p = draft.products.find((x) => x.id === run.productId)
          step.detail = `${p?.id} ${p?.name}：${p?.sellingPoints.join(' / ')}`
        } else if (step.key === 'gen-material') {
          // 调素材中心会新建任务（同一个任务 ID 贯穿失败与重试，不引用历史任务）
          const task = ensureOpsTask(draft)
          if (!draft.opsFailedOnce) {
            draft.opsFailedOnce = true
            step.status = 'failed'
            step.detail = `素材任务 ${task.id} 返回 429（限流），轨迹已暂停，可重试。`
          } else {
            step.status = 'done'
            step.detail = `任务 ${task.id} 已完成：成品「${task.output?.title}」`
          }
        } else if (step.key === 'compose') {
          step.status = 'done'
          // 投放文案跟「当时已发布」的素材/切片走：正文取自它们的已发布版，不是写死的
          const usable = draft.assets.filter(
            (a) => a.publishedV != null && (a.kind === '素材' || a.kind === '视频'),
          )
          const refs = usable.map((a) => ({ assetId: a.id, v: a.publishedV! }))
          const product = draft.products.find((p) => p.id === run.productId)
          const firstBody = usable[0]?.versions.find((x) => x.v === usable[0].publishedV)?.content ?? ''
          const title = usable[0]
            ? `投放文案 · ${product?.name ?? ''}（素材 ${usable[0].id} · v${usable[0].publishedV}）`
            : `投放文案 · ${product?.name ?? ''}（商品卖点组装）`
          const body = firstBody
            ? `${firstBody.slice(0, 80)}${firstBody.length > 80 ? '…' : ''}\n（正文取自已发布素材，卖点：${product?.sellingPoints.join(' / ')}）`
            : `${product?.sellingPoints.join('；')}。文案由商品卖点组装——登记并发布素材后重新运行，正文会跟随素材。`
          run.output = { title, body, refs }
          step.detail = `已生成投放文案 v1（引用 ${usable.length} 件已发布素材与商品卖点）`
        }
        break
      }

      case 'OPS_RETRY': {
        const step = draft.opsRun.steps.find((s) => s.status === 'failed')
        if (!step) break
        step.status = 'done'
        if (step.key === 'gen-material') {
          const task = ensureOpsTask(draft)
          step.detail = `重试成功：任务 ${task.id} 已完成，成品「${task.output?.title}」`
        }
        break
      }

      case 'OPS_RESET': {
        const seed = makeSeed()
        draft.opsRun = seed.opsRun
        draft.opsFailedOnce = false
        break
      }

      // 投放发布（mock，渠道动作）：不改变资产三态，与治理台的「发布资产」是两件事
      case 'OPS_DELIVER': {
        const run = draft.opsRun
        if (run.steps.some((s) => s.status !== 'done')) break
        run.delivery = { at: now(), channel: '小红书 · 店铺号' }
        break
      }

      case 'CREATE_EXPORT': {
        // 导出即冻结版本号（ADR 0007）：之后指针前移，记录仍指向当时的版本
        const refs = action.assetIds
          .map((id) => draft.assets.find((a) => a.id === id))
          .filter((a): a is Asset => !!a && a.publishedV != null)
          .map((a) => ({ assetId: a.id, v: a.publishedV! }))
        const rec: ExportRecord = {
          id: `E-${String(draft.exports.length + 1).padStart(3, '0')}`,
          createdAt: now(),
          refs,
          // 对话/文档是纯文本，量级按 KB 估
          size: `${(refs.length * 46 + 18).toFixed(0)} KB`,
        }
        draft.exports.unshift(rec)
        break
      }

      case 'SAVE_COACH': {
        draft.coachRecords.unshift({
          id: `R-${String(draft.coachRecords.length + 1).padStart(3, '0')}`,
          scenarioId: action.scenarioId,
          at: now(),
          scores: action.scores,
          dialog: action.dialog,
          // 考核时用的顾客扮演底座（与客服同一份模型配置）
          model: draft.models.find((m) => m.id === draft.activeModelId)?.name,
        })
        break
      }

      // 连接层「登记」工具：外部 Agent 只能登记，不能发布（ADR 0001/0005）
      // 连接层「登记」工具：外部 Agent 只能登记，不能发布（ADR 0001/0005）。
      // 可挂商品——挂上的规格文档机洗同样走类目 schema 闸门，不因为是外部登记就豁免
      case 'CONNECT_REGISTER': {
        const asset = registerAsset(draft, {
          kind: '文档',
          title: action.title,
          sourceKind: '连接层登记',
          sourceNote: '外部 Agent',
          content: action.content,
          ...(action.productId ? { productId: action.productId } : {}),
        })
        draft.lastRegisteredId = asset.id
        break
      }
  }
})
}
