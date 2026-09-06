// 顶栏壳（本刀无侧栏：只做顶栏 + 导航入口；不预建八模块空占位）。

import { NavLink } from 'react-router-dom'
import { Database, SignOut } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

const NAV_ITEMS = [
  { to: '/platform/assets', label: '资产' },
  { to: '/platform/products', label: '商品' },
  { to: '/service', label: '客服' },
]

export default function AppShell({ children }: { children: ReactNode }) {
  const { operator, signOut } = useAuth()
  return (
    <div className="min-h-screen bg-canvas">
      <header className="sticky top-0 z-30 border-b border-line-2 bg-canvas/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-5 px-5">
          <NavLink to="/platform/assets" className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-[6px] bg-[#0F1115]">
              <Database aria-hidden size={13} weight="bold" className="text-white" />
            </span>
            <span className="text-[14px] font-semibold tracking-tight text-ink">电商 AI 套件</span>
            <span className="hidden text-[11px] text-ink-3 sm:inline">治理台</span>
          </NavLink>
          <nav className="flex items-center gap-1" aria-label="主导航">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `flex h-8 items-center rounded-[6px] px-3 text-[13px] transition-colors duration-150 ${
                    isActive
                      ? 'font-medium text-accent'
                      : 'text-ink-2 hover:bg-[rgba(38,49,72,0.04)] hover:text-ink'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <span className="flex-1" />
          <div className="flex items-center gap-2.5">
            <span className="text-xs text-ink-3">操作者</span>
            <span className="max-w-[12rem] truncate text-[13px] font-medium text-ink">
              {operator?.username ?? '—'}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
              <SignOut aria-hidden size={13} />
              退出
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-5 py-6">{children}</main>
    </div>
  )
}
