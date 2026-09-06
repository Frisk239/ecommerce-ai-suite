// 查询规则：客服检索、考核抽题、微调导出、连接层检索，只命中已发布（ADR 0004）。
// 本文件只 import 类型，不产生运行时循环依赖。
import type { AppState } from './store'
import type { Asset, ChatMessage } from './types'

// 规格文档 = 挂在商品上的文档：字段 schema 跟商品的类目走（食品才有保质期，器皿是材质）
export function isSpecDoc(state: AppState, asset: Asset): boolean {
  return asset.kind === '文档' && !!asset.productId && !!productById(state, asset.productId)
}

export function requiredFieldsOfVersion(fields: { key: string; value: string | null; confirmed: boolean; required?: boolean }[]) {
  const required = fields.filter((f) => f.required)
  const missing = required.filter((f) => !f.value).map((f) => f.key)
  const unconfirmed = required.filter((f) => !!f.value && !f.confirmed).map((f) => f.key)
  return { ok: missing.length === 0 && unconfirmed.length === 0, missing, unconfirmed }
}

export function requiredFields(asset: Asset): string[] {
  const review = asset.versions.find((x) => x.role === 'review')
  if (!review) return []
  return review.fields.filter((f) => f.required).map((f) => f.key)
}

export function assetById(state: AppState, id: string): Asset | undefined {
  return state.assets.find((a) => a.id === id)
}

export function productById(state: AppState, id: string) {
  return state.products.find((p) => p.id === id)
}

// 有已发布指针的资产才对外可见（修订中的资产仍以其已发布版参与检索）
export function publishedAssets(state: AppState): Asset[] {
  return state.assets.filter((a) => a.publishedV != null)
}

export function canPublish(
  state: AppState,
  assetId: string
): { ok: boolean; missing: string[]; unconfirmed: string[]; reviewV?: number } {
  const asset = assetById(state, assetId)
  if (!asset) return { ok: false, missing: [], unconfirmed: [] }
  const review = asset.versions.find((x) => x.role === 'review')
  if (!review) return { ok: false, missing: [], unconfirmed: [] }
  const check = requiredFieldsOfVersion(review.fields)
  return { ...check, reviewV: review.v }
}

// ---------- 检索引擎（连接层演示与旧版共用：子串匹配） ----------

// 检索：只搜当前已发布版本的内容与标题
export function searchPublished(
  state: AppState,
  query: string
): { asset: Asset; v: number; snippet: string }[] {
  const q = query.trim()
  if (!q) return []
  const out: { asset: Asset; v: number; snippet: string }[] = []
  for (const asset of publishedAssets(state)) {
    const ver = asset.versions.find((x) => x.v === asset.publishedV)
    if (!ver) continue
    const hay = `${asset.title} ${ver.content}`
    const idx = hay.indexOf(q)
    if (idx >= 0) {
      const content = ver.content
      const cIdx = content.indexOf(q)
      const snippet =
        cIdx >= 0
          ? content.slice(Math.max(0, cIdx - 12), cIdx + q.length + 28)
          : content.slice(0, 48)
      out.push({ asset, v: ver.v, snippet: `${snippet}…` })
    }
  }
  return out
}

// ---------- 客服检索大脑：真的走已发布索引 ----------

const STOPWORDS = [
  '请问', '一下', '麻烦', '谢谢', '您好', '你好', '有没有', '是不是', '什么', '怎么',
  '多少', '多久', '几个', '这个', '那个', '告诉', '想问', '咨询', '还是', '感觉',
  '好像', '我们', '你们', '可以', '能', '会', '呢', '吗', '啊', '呀', '吧', '了',
  '的', '是', '我', '你', '他', '她', '它', '在', '有', '和', '还', '也', '都', '就',
]

export interface RetrievalHit {
  asset: Asset
  v: number
  sentence: string
  score: number
}

// 关键词打分检索：整段命中权重 3，二元组命中权重 1；阈值 3。
// 这不是正则剧本：把 A-0006 发布后，问口感就能命中它（接待闭环对「新发布」成立）
export function retrievePublished(state: AppState, text: string): RetrievalHit | null {
  let s = text
  for (const w of STOPWORDS) s = s.split(w).join(' ')
  const chunks = s.split(/[\s，。？！、,.?!；;：:]+/).filter((t) => t.length >= 2)
  const grams = new Set<string>()
  for (const c of chunks) {
    for (let i = 0; i + 2 <= c.length; i++) grams.add(c.slice(i, i + 2))
  }
  if (chunks.length === 0 && grams.size === 0) return null

  let best: RetrievalHit | null = null
  for (const asset of publishedAssets(state)) {
    const ver = asset.versions.find((x) => x.v === asset.publishedV)
    if (!ver) continue
    const hay = `${asset.title} ${ver.content}`
    let score = 0
    let anchor = ''
    for (const c of chunks) {
      if (hay.includes(c)) {
        score += 3
        anchor ||= c
      }
    }
    for (const g of grams) {
      if (hay.includes(g)) {
        score += 1
        anchor ||= g
      }
    }
    if (score < 3) continue
    // 抽证据句：优先包含锚点词的句子，否则首句
    const sentences = ver.content.split(/(?<=[。！？\n])/).filter((x) => x.trim())
    const sentence = sentences.find((x) => x.includes(anchor))?.trim() ?? sentences[0]?.trim() ?? asset.title
    const specBonus = isSpecDoc(state, asset) ? 0.5 : 0 // 同分时规格文档优先
    const total = score + specBonus
    if (!best || total > best.score) best = { asset, v: ver.v, sentence, score: total }
  }
  return best
}

// ---------- 客服 mock 大脑 ----------

interface Reply {
  text: string
  citations?: { assetId: string; v: number }[]
  toolCall?: { name: string; args: string; result: string }
  refused?: boolean
  handoff?: boolean
}

function detectProduct(text: string): 'P-0001' | 'P-0002' | null {
  if (/杯|保温/.test(text)) return 'P-0002'
  if (/水|饮用/.test(text)) return 'P-0001'
  return null
}

export function askService(state: AppState, raw: string): ChatMessage {
  const text = raw.trim()
  const activeModel = state.models.find((m) => m.id === state.activeModelId) ?? state.models[0]
  const ft = activeModel.type === 'finetuned'
  const d = new Date()
  const at = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${d.toTimeString().slice(0, 5)}`
  let reply: Reply

  const pid = detectProduct(text)

  // 用检索结果组织回答：按资产种类注明来源性质（对话不是检测报告）
  const answerFromRetrieval = (hit: RetrievalHit) => {
    const prefix =
      hit.asset.kind === '对话'
        ? ft
          ? '帮您翻到一条已发布的客服对话记录：'
          : '根据已发布的客服对话记录：'
        : '根据已发布口径：'
    return {
      text: `${prefix}${hit.sentence}`,
      citations: [{ assetId: hit.asset.id, v: hit.v }],
    }
  }

  // 规格类问题：先读商品上已确认的字段（ADR 0010）；商品没有再查已发布索引；都没有才拒答
  if (/保质期|净含量|规格|容量|多少毫升|材质/.test(text)) {
    const product = state.products.find((p) => p.id === pid)
    const complete =
      product && product.specSchema.length > 0 &&
      product.specSchema.every((k) => product.specs[k])
    if (product && complete) {
      const src = state.assets.find(
        (a) => a.productId === product.id && a.publishedV != null && isSpecDoc(state, a)
      )
      // 回答由商品已写回字段动态拼出，不再硬编码字段名
      const pairs = product.specSchema.map((k) => `${k} ${product.specs[k]}`).join('，')
      reply = {
        text: ft
          ? `帮您确认过啦：${product.name} ${pairs}。还有想了解的随时问我～`
          : `${product.name}：${pairs}。如需其他信息请继续提问。`,
        citations: src ? [{ assetId: src.id, v: src.publishedV! }] : undefined,
      }
    } else {
      const hit = retrievePublished(state, text)
      if (hit) {
        reply = answerFromRetrieval(hit)
      } else {
        // 索引里也没有：拒答并转人工，不幻觉（死亡三问之一）
        reply = {
          text: ft
            ? '这款的官方规格还在核对中，怕说错耽误您，马上请人工同事来确认。'
            : '抱歉，该商品的官方规格仍在核对中，暂无法提供准确答复。已为您转人工核实，请留意消息。',
          refused: true,
          handoff: true,
        }
      }
    }
  } else if (/库存|现货|还有货|有货吗|发货/.test(text)) {
    const product = state.products.find((p) => p.id === pid)
    if (!product) {
      // 没识别到具体商品时不猜：反问澄清，避免把 A 商品的库存答给 B
      reply = {
        text: ft
          ? '想帮您查库存，先告诉我是哪款呀：高山天然饮用水，还是钛钢保温杯？'
          : '请问您想查询哪款商品的库存：高山天然饮用水 550ml，或钛钢保温杯 500ml？',
      }
    } else {
      // 库存走查询接口，不是 RAG；对顾客只说有无货，内部数量留在工具条里
      reply = {
        text: ft
          ? `帮您看过啦：${product.name} 目前有现货，可以直接下单～`
          : `${product.name} 目前有现货，可以直接下单。`,
        toolCall: {
          name: '查询库存',
          args: `商品 ${product.id}`,
          result: `${product.stock} ${product.stockUnit}`,
        },
      }
    }
  } else {
    // 其余所有问题：真的查已发布索引（发布 A-0006 后问口感即命中；修订发布后口径自动跟随）
    const hit = retrievePublished(state, text)
    if (hit) {
      reply = answerFromRetrieval(hit)
    } else {
      const hasKeywords = text.replace(/[？?！!，,。.、\s_a-zA-Z0-9]/g, '').length >= 2
      if (hasKeywords) {
        reply = {
          text: ft
            ? '这个问题我这边还没有可用的官方口径，不敢乱答，马上请人工同事来核实。'
            : '该问题暂无可引用的官方口径，已为您转人工核实，请留意消息。',
          refused: true,
          handoff: true,
        }
      } else {
        reply = {
          text: ft
            ? '在的呢～商品规格、库存和售后政策都可以问我。'
            : '您可以向我询问商品规格、库存与售后政策。',
        }
      }
    }
  }

  return {
    id: `m${Math.random().toString(36).slice(2, 8)}`,
    role: 'agent',
    text: reply.text,
    citations: reply.citations,
    toolCall: reply.toolCall,
    refused: reply.refused,
    handoff: reply.handoff,
    model: { name: activeModel.name, type: activeModel.type, source: activeModel.source },
    at,
  }
}

// ---------- 连接层演示查询 ----------

// MCP「检索」工具：与客服同一条规则，只返回已发布
export function connectSearch(state: AppState, query: string) {
  return searchPublished(state, query)
}

export function connectToolList() {
  return [
    {
      name: 'search_published',
      label: '检索已发布',
      desc: '按关键词检索已发布资产，返回 资产ID · 版本号 与摘要',
      write: false,
    },
    {
      name: 'get_asset',
      label: '取资产',
      desc: '按 资产ID + 版本号 取一条不可变快照',
      write: false,
    },
    {
      name: 'register_asset',
      label: '登记',
      desc: '把外部内容登记进中台，落入已接入，等待治理',
      write: true,
    },
    {
      name: 'export_published',
      label: '导出已发布集',
      desc: '导出已发布资产集合供外部 Agent 使用',
      write: false,
    },
    // 连接层没有 publish 工具（ADR 0001/0005）
  ]
}
