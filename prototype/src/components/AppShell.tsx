import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Brain,
  ChatCircleDots,
  Database,
  FilmSlate,
  GearSix,
  GraduationCap,
  ImageSquare,
  List,
  Package,
  PlugsConnected,
  Robot,
  ArrowsClockwise,
  X,
  Storefront,
} from '@phosphor-icons/react'
import { resetDemo, useStore } from '../store/store'
import { SHOP_NAME } from '../store/seed'

const NAV = [
  { to: '/', label: '总览', icon: List, group: null, end: true },
  { to: '/platform/assets', label: '中台 · 资产', icon: Database, group: '数据中台' },
  { to: '/platform/products', label: '中台 · 商品', icon: Package, group: null },
  { to: '/service', label: 'AI 客服', icon: ChatCircleDots, group: '业务能力' },
  { to: '/ops', label: '运营 Agent', icon: Robot, group: null },
  { to: '/materials', label: '素材中心', icon: ImageSquare, group: null },
  { to: '/clips', label: '直播切片', icon: FilmSlate, group: null },
  { to: '/coach', label: '销售考核', icon: GraduationCap, group: null },
  { to: '/finetune', label: '模型微调', icon: Brain, group: null },
  { to: '/connect', label: '连接层', icon: PlugsConnected, group: '对外' },
  { to: '/models', label: '模型配置', icon: GearSix, group: '配置' },
]

// 顶栏面包屑：按当前路由给出「分组 / 页面」
function useCrumb() {
  const { pathname } = useLocation()
  const item = [...NAV]
    .sort((a, b) => b.to.length - a.to.length)
    .find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to)))
  if (!item) return null
  const group = item.group ?? NAV.find((n) => pathname.startsWith(n.to) && n.group)?.group
  return group ? `${group} / ${item.label}` : item.label
}

export default function AppShell() {
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const crumb = useCrumb()
  const reviewCount = useStore(
    (s) => s.assets.filter((a) => a.state === '待人洗' && a.publishedV == null).length
  )

  const sidebar = (
    <nav className="flex flex-col h-full">
      {/* 品牌区 */}
      <div className="h-14 flex items-center gap-2.5 px-5 border-b border-line-1">
        <div className="w-7 h-7 rounded-[6px] flex items-center justify-center shrink-0 bg-accent">
          <Database size={14} weight="bold" className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-semibold text-ink tracking-tight">电商 AI 套件</div>
          <div className="text-[11px] text-caption">操作台原型</div>
        </div>
      </div>

      {/* 导航 */}
      <div className="flex-1 overflow-y-auto py-1.5 pb-4">
        {NAV.map((item) => (
          <div key={item.to}>
            {item.group && <div className="nav-group">{item.group}</div>}
            <NavLink
              to={item.to}
              end={item.end}
              onClick={() => setOpen(false)}
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
            >
              <item.icon size={16} weight="regular" className="shrink-0" />
              <span className="flex-1">{item.label}</span>
              {item.to === '/platform/assets' && reviewCount > 0 && (
                <span className="tabular-nums text-[11px] font-medium bg-amber-100 text-amber-800 border border-amber-200/80 rounded-full h-5 min-w-5 px-1.5 inline-flex items-center justify-center">
                  {reviewCount}
                </span>
              )}
            </NavLink>
          </div>
        ))}
      </div>

      {/* 底部店铺卡 */}
      <div className="p-3 border-t border-line-1">
        <div className="flex items-center gap-2.5 px-2 py-2 rounded-[6px]">
          <div className="w-8 h-8 rounded-[6px] bg-fill-100 border border-line-2 flex items-center justify-center shrink-0">
            <Storefront size={15} className="text-ink-3" />
          </div>
          <div className="min-w-0 leading-tight">
            <div className="text-[13px] font-medium text-ink truncate">{SHOP_NAME}</div>
            <div className="text-[11px] text-caption">种子数据 · 可整包替换</div>
          </div>
        </div>
      </div>
    </nav>
  )

  return (
    <div className="min-h-[100dvh] flex">
      {/* 桌面侧栏 */}
      <aside className="hidden lg:block w-60 shrink-0 bg-fill-50 border-r border-line-1 fixed inset-y-0 left-0 z-30">
        {sidebar}
      </aside>

      {/* 窄屏抽屉 */}
      {open && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-stone-900/35 backdrop-blur-[2px]" onClick={() => setOpen(false)} />
          <aside
            className="absolute inset-y-0 left-0 w-[17rem] bg-fill-50"
            style={{ boxShadow: '0 8px 30px rgb(28 25 23 / 0.18)' }}
          >
            <button
              className="absolute top-4 right-3 p-1.5 text-caption hover:bg-fill-100 rounded-[6px]"
              onClick={() => setOpen(false)}
              aria-label="关闭导航"
            >
              <X size={18} />
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      <div className="flex-1 lg:pl-60 min-w-0 flex flex-col">
        {/* 顶栏：面包屑 + 动作 */}
        <header className="sticky top-0 z-20 h-14 bg-base/92 backdrop-blur border-b border-line-2/80 flex items-center gap-3 px-4 lg:px-6">
          <button
            className="lg:hidden p-2 -ml-1.5 text-ink-2 hover:bg-fill-100 rounded-[6px]"
            onClick={() => setOpen(true)}
            aria-label="打开导航"
          >
            <List size={20} />
          </button>
          {crumb && (
            <div className="text-[13px] text-caption truncate tracking-tight">{crumb}</div>
          )}
          <div className="flex-1" />
          <button
            className="btn-ghost btn-sm"
            onClick={() => {
              if (confirm('重置演示数据？所有登记与发布操作将恢复初始状态。')) {
                resetDemo()
              }
            }}
            title="恢复初始种子数据"
          >
            <ArrowsClockwise size={13} />
            重置演示
          </button>
        </header>

        <main className="flex-1 min-w-0" key={location.pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
