// 可搜索商品下拉（第 41 刀）：三处原生 select 的共享替换——登记抽屉挂载商品、
// 素材新建任务、运营编排选商品。按 name/category/id 过滤；空值选项文案由调用
// 方定（"不挂商品" / "选择商品…"）。商品数店级规模，全量客户端过滤。

import { useEffect, useMemo, useRef, useState } from 'react'
import { MagnifyingGlass, X } from '@phosphor-icons/react'
import type { Product } from '../api/types'

export default function ProductSelect({
  products,
  value,
  onChange,
  disabled = false,
  inputId,
  ariaLabel,
  emptyLabel,
  loading = false,
  showId = false,
}: {
  products: Product[]
  /** '' = 未选；否则为商品 id 字符串（与既有 productId 状态同形）。 */
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  inputId?: string
  ariaLabel: string
  emptyLabel: string
  loading?: boolean
  showId?: boolean
}) {
  const selected = useMemo(
    () => products.find((p) => String(p.id) === value) ?? null,
    [products, value],
  )
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  // 外部 value 变化（预选/重置）即同步输入框展示
  useEffect(() => {
    setQuery(selected !== null ? selected.name : '')
  }, [selected])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q === '' || (selected !== null && selected.name === query)) return products
    return products.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.category.toLowerCase().includes(q) ||
        String(p.id).includes(q) ||
        `p-${p.id}`.includes(q),
    )
  }, [products, query, selected])

  const pick = (id: string) => {
    onChange(id)
    setOpen(false)
  }

  const labelOf = (p: Product) =>
    showId ? `${p.name}（${p.category} · ${p.id}）` : `${p.name} · ${p.category}`

  return (
    <div ref={boxRef} className="relative">
      <div className="relative">
        <MagnifyingGlass
          aria-hidden
          size={13}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-caption"
        />
        <input
          id={inputId}
          className="input w-full pl-8 pr-8"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          aria-autocomplete="list"
          placeholder={loading ? '加载商品…' : `搜索商品（名称/类目/ID）…`}
          value={query}
          disabled={disabled || loading}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
            // 输入与已选不一致即视为清空选择（调用方按 '' 走原空值语义）
            if (selected !== null && e.target.value !== selected.name) onChange('')
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // 失焦即收起；若输入为空或与选项无关，选择保持 ''（不猜）
            window.setTimeout(() => {
              setOpen(false)
              setQuery(selected !== null ? selected.name : '')
            }, 120)
          }}
        />
        {query !== '' && !disabled ? (
          <button
            type="button"
            className="btn btn-ghost btn-sm absolute right-1 top-1/2 -translate-y-1/2"
            aria-label="清空商品选择"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              onChange('')
              setQuery('')
            }}
          >
            <X aria-hidden size={12} />
          </button>
        ) : null}
      </div>
      {open && !disabled && !loading ? (
        <ul
          role="listbox"
          aria-label={ariaLabel}
          className="absolute z-30 mt-1 max-h-56 w-full overflow-y-auto rounded-[6px] border border-line-2 bg-surface py-1 shadow-lg"
        >
          <li role="option" aria-selected={value === ''}>
            <button
              type="button"
              className="flex w-full items-center px-2.5 py-1.5 text-left text-[13px] text-ink-3 hover:bg-canvas"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick('')}
            >
              {emptyLabel}
            </button>
          </li>
          {filtered.length === 0 ? (
            <li className="px-2.5 py-1.5 text-[13px] text-ink-3">没有匹配的商品</li>
          ) : (
            filtered.map((p) => (
              <li key={p.id} role="option" aria-selected={String(p.id) === value}>
                <button
                  type="button"
                  className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[13px] hover:bg-canvas ${
                    String(p.id) === value ? 'font-medium text-ink' : 'text-ink-2'
                  }`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(String(p.id))}
                >
                  <span className="min-w-0 flex-1 truncate">{labelOf(p)}</span>
                  <span className="shrink-0 font-mono text-xs text-ink-3">P-{p.id}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}
