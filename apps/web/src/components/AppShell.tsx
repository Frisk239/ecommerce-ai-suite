// 操作台壳（视觉优化刀）：固定左侧栏（分组：数据中台靠前）+ 顶栏面包屑 + 内容列。
// 路由行为不变：/login 与 /customer 不进此壳；新页面入口只加 NAV/面包屑条目
// （第 17 刀起含素材中心；第 18 刀起含直播切片；第 19 刀起含销售考核；
// 第 22 刀起含运营 Agent）。

import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  ChatCircleDots,
  Database,
  FilmSlate,
  GraduationCap,
  List,
  Megaphone,
  Package,
  Robot,
  SignOut,
  Storefront,
  X,
} from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

const NAV = [
  { to: '/platform/assets', label: '中台 · 资产', icon: Database, group: '数据中台' },
  { to: '/platform/products', label: '中台 · 商品', icon: Package, group: null },
  { to: '/service', label: 'AI 客服', icon: ChatCircleDots, group: '业务能力' },
  { to: '/material', label: '素材中心', icon: Megaphone, group: null },
  { to: '/clips', label: '直播切片', icon: FilmSlate, group: null },
  { to: '/coach', label: '销售考核', icon: GraduationCap, group: null },
  { to: '/ops', label: '运营 Agent', icon: Robot, group: null },
] as const

function useCrumb(): string | null {
  const { pathname } = useLocation()
  if (pathname.startsWith('/platform/assets/')) return '数据中台 / 中台 · 资产 / 详情'
  if (pathname.startsWith('/platform/assets')) return '数据中台 / 中台 · 资产'
  if (pathname.startsWith('/platform/products')) return '数据中台 / 中台 · 商品'
  if (pathname.startsWith('/material')) return '业务能力 / 素材中心'
  if (pathname.startsWith('/clips')) return '业务能力 / 直播切片'
  if (pathname.startsWith('/coach')) return '业务能力 / 销售考核'
  if (pathname.startsWith('/ops')) return '业务能力 / 运营 Agent'
  if (pathname.startsWith('/service')) return '业务能力 / AI 客服'
  return null
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex h-full flex-col" aria-label="主导航">
      <div className="flex h-14 items-center gap-2.5 border-b border-line-1 px-5">
        <span className="brand-mark flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px]">
          <Database aria-hidden size={14} weight="bold" className="text-white" />
        </span>
        <span className="leading-tight">
          <span className="block text-[15px] font-semibold tracking-tight text-ink">电商 AI 套件</span>
          <span className="block text-[11px] text-caption">治理台</span>
        </span>
      </div>

      <div className="flex-1 overflow-y-auto pb-4 pt-1.5">
        {NAV.map((item) => (
          <div key={item.to}>
            {item.group ? <div className="nav-group">{item.group}</div> : null}
            <NavLink
              to={item.to}
              onClick={onNavigate}
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
            >
              <item.icon aria-hidden size={16} className="shrink-0" />
              <span className="flex-1">{item.label}</span>
            </NavLink>
          </div>
        ))}
      </div>

      <div className="border-t border-line-1 p-3">
        <div className="seed-card flex items-center gap-2.5 px-2 py-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] border border-line-2 bg-surface">
            <Storefront aria-hidden size={15} className="text-ink-3" />
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-[13px] font-medium text-ink">演示店铺</span>
            <span className="block text-[11px] text-caption">种子数据 · 可整包替换</span>
          </span>
        </div>
      </div>
    </nav>
  )
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { operator, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const crumb = useCrumb()
  const { pathname } = useLocation()

  return (
    <div className="flex min-h-screen bg-canvas">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 shrink-0 border-r border-line-1 bg-surface lg:block">
        <SidebarBody />
      </aside>

      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-[rgba(15,17,21,0.35)]"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <aside className="absolute inset-y-0 left-0 w-72 border-r border-line-2 bg-surface shadow-xl">
            <button
              type="button"
              className="absolute right-3 top-4 rounded-[6px] p-1.5 text-caption hover:bg-hover"
              onClick={() => setOpen(false)}
              aria-label="关闭导航"
            >
              <X aria-hidden size={18} />
            </button>
            <SidebarBody onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line-2 bg-canvas/90 px-4 backdrop-blur lg:px-6">
          <button
            type="button"
            className="-ml-1.5 rounded-[6px] p-2 text-ink-2 hover:bg-hover lg:hidden"
            onClick={() => setOpen(true)}
            aria-label="打开导航"
          >
            <List aria-hidden size={20} />
          </button>
          {crumb ? <div className="crumb">{crumb}</div> : null}
          <span className="flex-1" />
          <span className="text-xs text-ink-3">操作者</span>
          <span className="max-w-40 truncate text-[13px] font-medium text-ink">
            {operator?.username ?? '—'}
          </span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
            <SignOut aria-hidden size={13} />
            退出
          </button>
        </header>
        <main key={pathname} className="page-enter mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-6">
          {children}
        </main>
      </div>
    </div>
  )
}
