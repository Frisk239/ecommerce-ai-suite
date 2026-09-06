import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  MagnifyingGlass,
  PlugsConnected,
  Prohibit,
  ArrowUDownLeft,
  FileDoc,
  DownloadSimple,
} from '@phosphor-icons/react'
import PageHeader from '../components/PageHeader'
import CitationChip from '../components/CitationChip'
import { dispatch, getState, useStore } from '../store/store'
import { connectSearch, connectToolList, publishedAssets } from '../store/selectors'

// 连接层（MCP）：把中台能力暴露给外部 Agent 的门面。
// 内部模块走中台接口，不经这里；工具里有登记，没有发布（ADR 0001/0005）。

interface GetResult {
  error?: string
  id?: string
  v?: number
  title?: string
  kind?: string
  excerpt?: string
}

export default function Connect() {
  const state = useStore((s) => s)
  const tools = connectToolList()
  const [q, setQ] = useState('')
  const [searched, setSearched] = useState<string | null>(null)
  const [regTitle, setRegTitle] = useState('')
  const [regBody, setRegBody] = useState('')
  const [regProduct, setRegProduct] = useState('')
  const [lastRegistered, setLastRegistered] = useState<string | null>(null)
  const [getId, setGetId] = useState('')
  const [getResult, setGetResult] = useState<GetResult | null>(null)
  const [exportResult, setExportResult] = useState<string | null>(null)

  const results = searched ? connectSearch(state, searched) : []

  // get_asset：按 资产ID + 版本号 取不可变快照；待人洗工作版本外部取不到
  const runGet = () => {
    const m = getId.trim().match(/^([Aa]-\d{3,4})\s*v?(\d+)?$/)
    if (!m) {
      setGetResult({ error: '格式：资产ID + 版本，如 A-0001 v1' })
      return
    }
    const asset = state.assets.find((a) => a.id === m[1].toUpperCase())
    if (!asset) {
      setGetResult({ error: `资产 ${m[1].toUpperCase()} 不存在` })
      return
    }
    const v = m[2] ? Number(m[2]) : asset.publishedV
    const ver = asset.versions.find((x) => x.v === v)
    if (!v || !ver) {
      setGetResult({ error: `版本 v${m[2] ?? '—'} 不存在（当前已发布：${asset.publishedV != null ? `v${asset.publishedV}` : '无'}）` })
      return
    }
    if (ver.role === 'review') {
      setGetResult({ error: `v${v} 是待人洗工作版本，外部不可取。可取：当前已发布或历史已发布版本。` })
      return
    }
    setGetResult({
      id: asset.id,
      v,
      title: asset.title,
      kind: asset.kind,
      excerpt: ver.content.slice(0, 60) + (ver.content.length > 60 ? '…' : ''),
    })
  }

  // export_published：返回全部已发布资产的 资产ID · vN 清单
  const exportPublished = () => {
    const list = publishedAssets(state).map((a) => `${a.id} v${a.publishedV}`)
    return `[${list.join(', ')}]  // 共 ${list.length} 条`
  }

  return (
    <div className="p-4 lg:p-6 max-w-[960px]">
      <PageHeader
        title="连接层"
        desc="把中台接口以 MCP 工具的形式暴露给外部 Agent（Cursor、Claude、未来的第九个系统）。内部模块互调不走这里。"
      />

      {/* 工具列表 */}
      <div className="panel mb-4 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-line-1 text-sm font-semibold text-ink">
          对外工具（4）
        </div>
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-44">工具</th>
              <th>说明</th>
              <th className="w-20">写入</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((t) => (
              <tr key={t.name}>
                <td>
                  <span className="font-mono text-xs text-accent-strong">{t.name}</span>
                  <div className="text-xs text-ink-3 mt-0.5">{t.label}</div>
                </td>
                <td className="text-ink-2 text-xs leading-5">{t.desc}</td>
                <td>
                  {t.write ? (
                    <span className="badge-review">可写入</span>
                  ) : (
                    <span className="badge-neutral">只读</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex items-center gap-2 px-4 py-2.5 border-t border-line-1 bg-red-50/60 text-sm text-red-700">
          <Prohibit size={14} />
          工具列表里没有「发布」：外部 Agent 可以登记与读取，发布权只在治理台的操作者手里。
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4 items-start">
        {/* 演示：外部 Agent 检索 */}
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-line-1 flex items-center gap-2">
            <MagnifyingGlass size={14} className="text-caption" />
            <span className="text-sm font-semibold text-ink">演示 · 外部 Agent 检索</span>
          </div>
          <div className="p-3.5">
            <div className="flex gap-2">
              <input
                className="input flex-1"
                placeholder="调用 search_published，如：保温"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && setSearched(q)}
              />
              <button className="btn-primary" onClick={() => setSearched(q)} disabled={!q.trim()}>
                调用
              </button>
            </div>
            <div className="mt-1.5 text-xs text-caption leading-5">
              试试搜「口感」：这条口径只存在于待人洗对话，检索不会命中。
            </div>

            {searched && (
              <div className="mt-3 divide-y divide-line-1 border-t border-line-1">
                {results.length === 0 ? (
                  <div className="py-4 text-sm text-ink-3">
                    0 条结果。检索索引里只有已发布资产；未发布内容对外部 Agent 不可见，也不会半透明展示。
                  </div>
                ) : (
                  results.map((r) => (
                    <div key={r.asset.id} className="py-2.5">
                      <div className="flex items-center gap-2">
                        <CitationChip assetId={r.asset.id} v={r.v} />
                        <span className="text-sm text-ink font-medium truncate">
                          {r.asset.title}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-ink-3 leading-5">{r.snippet}</p>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* 演示：外部 Agent 登记 */}
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-line-1 flex items-center gap-2">
            <ArrowUDownLeft size={14} className="text-caption" />
            <span className="text-sm font-semibold text-ink">演示 · 外部 Agent 登记</span>
          </div>
          <div className="p-3.5 space-y-2">
            <input
              className="input w-full"
              placeholder="标题，如：外部整理 · 类目常见问题"
              value={regTitle}
              onChange={(e) => setRegTitle(e.target.value)}
            />
            <textarea
              className="input w-full min-h-[72px] py-2"
              placeholder="内容正文（登记后进入已接入，等待机洗与人洗）"
              value={regBody}
              onChange={(e) => setRegBody(e.target.value)}
            />
            <label className="flex items-center gap-1.5 text-xs text-ink-3">
              挂载商品（可选）
              <select
                className="input h-7 text-xs w-48"
                value={regProduct}
                onChange={(e) => setRegProduct(e.target.value)}
              >
                <option value="">不挂载</option>
                {state.products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} {p.name}
                  </option>
                ))}
              </select>
              <span className="text-caption">挂上的规格文档机洗同样走类目 schema 闸门</span>
            </label>
            <button
              className="btn-primary"
              disabled={!regTitle.trim() || !regBody.trim()}
              onClick={() => {
                dispatch({
                  type: 'CONNECT_REGISTER',
                  title: regTitle.trim(),
                  content: regBody.trim(),
                  ...(regProduct ? { productId: regProduct } : {}),
                })
                // dispatch 同步更新模块级状态，store 记录了刚登记的资产 ID
                setLastRegistered(getState().lastRegisteredId)
                setRegTitle('')
                setRegBody('')
                setRegProduct('')
              }}
            >
              调用 register_asset
            </button>
            {lastRegistered && (
              <div className="text-xs text-ink-3 leading-5 bg-fill-60 border border-line-2 rounded-[6px] px-2.5 py-2">
                登记成功：
                <Link
                  to={`/platform/assets/${lastRegistered}`}
                  className="font-mono text-accent-strong underline ml-1"
                >
                  {lastRegistered}
                </Link>
                <br />
                状态 = 已接入（不是已发布）。要进入检索，需操作者在治理台人洗并发布。
              </div>
            )}
          </div>
        </div>

        {/* 演示：取资产（按 资产ID · vN 取不可变快照；待人洗版本外部取不到） */}
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-line-1 flex items-center gap-2">
            <FileDoc size={14} className="text-caption" />
            <span className="text-sm font-semibold text-ink">演示 · 取资产</span>
          </div>
          <div className="p-3.5 space-y-2">
            <div className="flex gap-2">
              <input
                className="input flex-1 font-mono text-xs"
                placeholder="资产ID 与版本，如 A-0001 v1"
                value={getId}
                onChange={(e) => setGetId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runGet()}
              />
              <button className="btn-primary" onClick={runGet} disabled={!getId.trim()}>
                调用
              </button>
            </div>
            {getResult && (
              <div
                className={`text-xs leading-5 rounded-[6px] px-2.5 py-2 border ${
                  getResult.error
                    ? 'text-red-600 bg-red-50 border-red-100'
                    : 'text-ink-3 bg-fill-60 border-line-2'
                }`}
              >
                {getResult.error ? (
                  getResult.error
                ) : (
                  <>
                    <span className="font-mono text-accent-strong">{getResult.id} · v{getResult.v}</span>{' '}
                    {getResult.title}（{getResult.kind}）
                    <br />
                    正文快照：{getResult.excerpt}
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {/* 演示：导出已发布集 */}
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-line-1 flex items-center gap-2">
            <DownloadSimple size={14} className="text-caption" />
            <span className="text-sm font-semibold text-ink">演示 · 导出已发布集</span>
          </div>
          <div className="p-3.5 space-y-2">
            <button className="btn-primary" onClick={() => setExportResult(exportPublished())}>
              调用 export_published
            </button>
            {exportResult && (
              <div className="text-xs leading-5 bg-fill-60 border border-line-2 rounded-[6px] px-2.5 py-2 font-mono">
                {exportResult}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-1.5 text-xs text-caption">
        <PlugsConnected size={13} />
        新系统接入 = 提示词 + 选这四个工具；内部八块能力走中台接口，不绕连接层。
      </div>
    </div>
  )
}
