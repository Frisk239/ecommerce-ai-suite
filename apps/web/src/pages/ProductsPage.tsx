// 商品页：结构化事实。规格字段按类目 schema 数据驱动；值只在资产发布时写回，
// 来源芯片 A-{id} · v{N} 指向写回它的那版资产（未写回显示 —，绝不预览未发布数据）。
// 第 41 刀：上架抽屉（新建+编辑）——名称/类目/单价直写即时生效；规格按模板逐项
// 展示（新建=待写回预览，编辑=当前值只读，写回只走资产发布）；库存列保持只读。

import { useCallback, useMemo, useState } from 'react'
import { CaretDown, CaretRight, Package, PencilSimple, Plus, Warning, X } from '@phosphor-icons/react'
import { detailText } from '../api/client'
import { api } from '../api/endpoints'
import type { Product } from '../api/types'
import { useApiData } from '../hooks/useApiData'
import ActionError from '../components/ActionError'
import CitationChip from '../components/CitationChip'
import Empty from '../components/Empty'
import { SkeletonRows } from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { ErrorBanner } from '../components/Banner'

// 类目下拉与模板预览（后端 services/category_schema.py 是唯一权威；
// 本常量只做新建表单的选项与待写回预览，提交时 spec_schema 省略即由服务端派生，
// 显式校验也在服务端——前后漂移以后端为准，见 ADR 0045）。
const KNOWN_CATEGORIES: { name: string; fields: { field: string; required: boolean }[] }[] = [
  { name: '食品', fields: [{ field: '净含量', required: true }, { field: '保质期', required: true }] },
  { name: '器皿', fields: [{ field: '净含量', required: true }, { field: '材质', required: true }] },
  { name: '智能手机', fields: [{ field: '品牌', required: true }, { field: '存储容量', required: false }] },
  { name: '笔记本电脑', fields: [{ field: '品牌', required: true }, { field: '内存', required: false }] },
  { name: '平板电脑', fields: [{ field: '品牌', required: true }] },
  { name: '电视机', fields: [{ field: '屏幕尺寸', required: true }] },
  { name: '洗衣机', fields: [{ field: '容量', required: true }] },
  { name: '图书', fields: [{ field: '作者', required: true }] },
  { name: '家具', fields: [{ field: '材质', required: true }] },
]

/** 单价展示：分→元（去尾零）；NULL=未定价；非 CNY 注币种（只存不算）。 */
export function formatProductPrice(priceCents: number | null, currency: string): string {
  if (priceCents === null) return '未定价'
  const text = (priceCents / 100).toFixed(2).replace(/\.?0+$/, '')
  return currency === 'CNY' ? `${text} 元` : `${text} ${currency}`
}

/** 有写回规格：spec_schema 与 spec_values 求交非空——防类目切换后残留旧键误判。 */
function hasWrittenSpec(product: Product): boolean {
  return Object.keys(product.spec_schema).some((k) => product.spec_values?.[k])
}

/** 首屏排序档：0=已定价，1=未定价但有写回规格，2=其余（折进可展开区）。 */
function productRank(product: Product): number {
  if (product.price_cents !== null) return 0
  return hasWrittenSpec(product) ? 1 : 2
}

// 稳定空数组：加载/错误态复用同一引用，排序 useMemo 的依赖才不会每渲染都变。
const EMPTY_PRODUCTS: Product[] = []

/** 元输入→分：空=未定价；非法返回错误文案（不提交，服务端再闸一道）。 */
function parseYuanToCents(raw: string): { cents: number | null } | { error: string } {
  const trimmed = raw.trim()
  if (trimmed === '') return { cents: null }
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return { error: '单价格式非法：非负数，最多两位小数（空=未定价）' }
  return { cents: Math.round(Number(trimmed) * 100) }
}

function ProductDrawer({
  product,
  onClose,
  onSaved,
}: {
  /** null=上架（新建），非空=编辑该商品。 */
  product: Product | null
  onClose: () => void
  onSaved: () => void
}) {
  const editing = product !== null
  const [name, setName] = useState(product?.name ?? '')
  const [category, setCategory] = useState(product?.category ?? '食品')
  const [priceYuan, setPriceYuan] = useState(
    product !== null && product.price_cents !== null ? String(product.price_cents / 100) : '',
  )
  const [currency, setCurrency] = useState(product?.currency ?? 'CNY')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 规格模板逐项：编辑=服务端下发的该商品模板；新建=已知类目常量预览
  // （未知类目无预览，提交后以服务端派生为准）。
  const templateFields = useMemo(() => {
    if (product !== null) {
      return Object.entries(product.spec_schema).map(([field, rule]) => ({
        field,
        required: rule.required === true,
      }))
    }
    return KNOWN_CATEGORIES.find((c) => c.name === category)?.fields ?? null
  }, [product, category])

  const submit = async () => {
    if (submitting) return
    if (name.trim() === '') {
      setError('商品名不能为空')
      return
    }
    if (!/^[A-Za-z]{3}$/.test(currency.trim())) {
      setError('币种须为 3 字母（如 CNY），v1 单币种不结算')
      return
    }
    const parsed = parseYuanToCents(priceYuan)
    if ('error' in parsed) {
      setError(parsed.error)
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      if (editing && product !== null) {
        await api.updateProduct(product.id, {
          name: name.trim(),
          category,
          price_cents: parsed.cents,
          currency: currency.trim().toUpperCase(),
        })
      } else {
        await api.createProduct({
          name: name.trim(),
          category,
          price_cents: parsed.cents,
          currency: currency.trim().toUpperCase(),
        })
      }
      onSaved()
      onClose()
    } catch (err) {
      setError(detailText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={editing ? '编辑商品' : '上架商品'}>
      <div className="modal-backdrop absolute inset-0" onClick={submitting ? undefined : onClose} aria-hidden />
      <aside className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-md flex-col">
        <div className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
          <div className="flex-1 text-[14px] font-semibold text-ink">
            {editing ? `编辑商品 P-${product?.id}` : '上架商品'}
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={submitting} aria-label="关闭商品抽屉">
            <X aria-hidden size={14} />
          </button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <div>
            <label className="field-label" htmlFor="product-name">
              商品名
            </label>
            <input
              id="product-name"
              className="input w-full"
              value={name}
              disabled={submitting}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：帆布包"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="field-label" htmlFor="product-category">
                类目
              </label>
              <select
                id="product-category"
                className="input w-full"
                value={KNOWN_CATEGORIES.some((c) => c.name === category) ? category : ''}
                disabled={submitting}
                onChange={(e) => setCategory(e.target.value)}
              >
                {editing && !KNOWN_CATEGORIES.some((c) => c.name === category) ? (
                  <option value="">{category}（存量类目）</option>
                ) : null}
                {KNOWN_CATEGORIES.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="product-currency">
                币种
              </label>
              <input
                id="product-currency"
                className="input w-full font-mono"
                value={currency}
                maxLength={3}
                disabled={submitting}
                onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                placeholder="CNY"
              />
            </div>
          </div>
          <div>
            <label className="field-label" htmlFor="product-price">
              单价（元，空=未定价）
            </label>
            <input
              id="product-price"
              className="input w-full font-mono"
              value={priceYuan}
              inputMode="decimal"
              disabled={submitting}
              onChange={(e) => setPriceYuan(e.target.value)}
              placeholder="如：129（未定价留空）"
            />
            <p className="mt-1.5 text-[11px] leading-4 text-ink-3">
              直写即时生效：目录报价读实时行价；改价记留痕（产品档，非资产发布）。
            </p>
          </div>
          {editing && product !== null && category !== product.category ? (
            <div className="rounded-[6px] border border-[rgba(154,91,6,0.25)] bg-[rgba(154,91,6,0.05)] px-3 py-2 text-xs leading-5 text-warn">
              类目已变更：保存后规格模板按新类目重置，旧规格值按键保留（模板外旧值自然失效）。
            </div>
          ) : null}
          <div>
            <span className="field-label">规格字段（按模板逐项）</span>
            {templateFields === null ? (
              <p className="text-[13px] leading-5 text-ink-3">
                该类目暂无模板预览——提交后以服务端模板为准，规格值只在资产发布时写回。
              </p>
            ) : templateFields.length === 0 ? (
              <p className="text-[13px] leading-5 text-ink-3">该类目无规格字段。</p>
            ) : (
              <div className="overflow-hidden rounded-[8px] border border-line-2">
                <table className="table-gov">
                  <thead>
                    <tr>
                      <th className="w-24">规格字段</th>
                      <th>当前值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {templateFields.map(({ field, required }) => {
                      const entry = editing && product !== null ? product.spec_values[field] : undefined
                      return (
                        <tr key={field}>
                          <td className="text-ink-2">
                            {field}
                            {required ? <span className="ml-0.5 text-danger">*</span> : null}
                          </td>
                          <td className={entry ? 'font-medium text-ink' : 'text-ink-3'}>
                            {entry ? entry.value : editing ? '—' : '待资产发布写回'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-1.5 text-[11px] leading-4 text-ink-3">
              规格值只读：只在资产发布时写回，不在这里手填。
            </p>
          </div>
          {error ? <ActionError message={error} /> : null}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-line-2 px-4 py-3">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={submitting}>
            {submitting ? '保存中…' : editing ? '保存' : '上架'}
          </button>
        </div>
      </aside>
    </div>
  )
}

export default function ProductsPage() {
  const fetcher = useCallback(() => api.listProducts(), [])
  const { state, reload } = useApiData(fetcher)
  const products = state.phase === 'ok' ? state.data : EMPTY_PRODUCTS

  const [drawer, setDrawer] = useState<{ open: boolean; product: Product | null }>({
    open: false,
    product: null,
  })
  // 其余商品默认收起：首屏只留可卖物（已定价 / 有写回规格），百科货不抢视线。
  const [restOpen, setRestOpen] = useState(false)

  // 稳定排序：档位优先，同档按 id 升序兜底（否则每次渲染跳位）。
  const ordered = useMemo(
    () => [...products].sort((a, b) => productRank(a) - productRank(b) || a.id - b.id),
    [products],
  )
  const featured = ordered.filter((p) => productRank(p) < 2)
  const rest = ordered.filter((p) => productRank(p) === 2)

  const renderCard = (product: Product) => (
    <div key={product.id} className="panel panel-hover">
      <div className="flex items-center gap-3 border-b border-line-2 px-4 py-3.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[6px] bg-accent-soft text-accent-strong">
          <Package aria-hidden size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold leading-6 text-ink">{product.name}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            <span className="kind-chip">{product.category}</span>
            <span className="font-mono text-xs text-ink-3">P-{product.id}</span>
            {/* 库存列（第 30 刀）：操作者只读自查（客服工具同源 mock 值）；
                NULL=未设置显示 —，不可编辑（不升格为中台对象）。 */}
            <span
              className={
                product.stock === 0
                  ? 'font-mono text-xs text-danger'
                  : 'font-mono text-xs text-ink-2'
              }
              title="库存可 mock：只读自查，不可在这里编辑"
            >
              库存 {product.stock !== null ? product.stock : '—'}
            </span>
            {/* 单价（第 41 刀）：直写即时生效，抽屉里改；NULL=未定价。 */}
            <span className="font-mono text-xs text-ink-2" title="单价：抽屉里直写即时生效，改价记留痕">
              单价 {formatProductPrice(product.price_cents, product.currency)}
            </span>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-sm shrink-0"
          title="编辑商品（名称/类目/单价）"
          onClick={() => setDrawer({ open: true, product })}
        >
          <PencilSimple aria-hidden size={13} />
          编辑
        </button>
      </div>

      <table className="table-gov">
        <thead>
          <tr>
            <th className="w-24">规格字段</th>
            <th>当前值</th>
            <th className="w-32">写回来源</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(product.spec_schema).map(([field, rule]) => {
            const entry = product.spec_values[field]
            return (
              <tr key={field}>
                <td className="text-ink-2">
                  {field}
                  {rule.required === true ? <span className="ml-0.5 text-danger">*</span> : null}
                </td>
                <td className={entry ? 'font-medium text-ink' : 'text-ink-3'}>
                  {entry ? entry.value : '—'}
                </td>
                <td>
                  {entry ? (
                    <CitationChip assetId={entry.source.asset_id} version={entry.source.version} />
                  ) : (
                    <span className="text-ink-3">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )

  return (
    <div>
      <PageHeader
        title="中台 · 商品"
        desc="可卖对象的结构化事实。规格字段按类目定（食品有保质期，器皿是材质与净含量），只在资产发布时写回；未写回的字段显示 —。单价直写即时生效（目录报价读实时行价），改价记留痕。"
        actions={
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setDrawer({ open: true, product: null })}
          >
            <Plus aria-hidden size={14} weight="bold" />
            上架商品
          </button>
        }
      />

      {state.phase === 'loading' ? (
        <div className="panel">
          <SkeletonRows rows={8} />
        </div>
      ) : state.phase === 'error' ? (
        <>
          <ErrorBanner error={state.error} onRetry={reload} />
          <div className="panel">
            <Empty icon={<Warning aria-hidden size={26} />} title="商品加载失败" hint="上面的横幅可重试。" />
          </div>
        </>
      ) : products.length === 0 ? (
        <div className="rounded-[8px] border-[1.5px] border-dashed border-line-3 bg-surface/60">
          <Empty
            icon={<Package aria-hidden size={24} />}
            title="还没有商品"
            hint="右上「上架商品」建第一个可卖对象。"
            action={
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => setDrawer({ open: true, product: null })}
              >
                <Plus aria-hidden size={13} weight="bold" />
                上架商品
              </button>
            }
          />
        </div>
      ) : (
        <div>
          <div className="grid items-start gap-4 md:grid-cols-2">{featured.map(renderCard)}</div>
          {rest.length > 0 ? (
            <div className="mt-4">
              <button
                type="button"
                className="panel panel-hover flex w-full cursor-pointer items-center gap-2 px-4 py-2.5 text-left"
                aria-expanded={restOpen}
                onClick={() => setRestOpen((v) => !v)}
              >
                <span className="flex items-center text-caption">
                  {restOpen ? <CaretDown aria-hidden size={13} /> : <CaretRight aria-hidden size={13} />}
                </span>
                <span className="text-[13px] font-medium text-ink">其余商品 {rest.length}</span>
                <span className="text-xs text-ink-3">未定价且尚无写回规格，收在这里</span>
              </button>
              {restOpen ? (
                <div className="mt-4 grid items-start gap-4 md:grid-cols-2">{rest.map(renderCard)}</div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      {drawer.open ? (
        <ProductDrawer
          product={drawer.product}
          onClose={() => setDrawer({ open: false, product: null })}
          onSaved={reload}
        />
      ) : null}
    </div>
  )
}
