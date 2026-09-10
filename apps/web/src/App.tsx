// 路由表：/（总览，第 23 刀起不再 redirect）、/login、/platform/assets、
// /platform/assets/:id、/platform/products、/material（素材中心，第 17 刀）、
// /clips（直播切片，第 18 刀）、/coach（销售考核，第 19 刀）、/service（客服
// 预览）、/connect（连接层演示，第 23 刀）、/customer（顾客通道，无登录守卫——
// 0021 顾客不登录，不进操作者壳）、404。
// 路径结构按后续八页预留，不预建空入口。

import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import AppShell from './components/AppShell'
import AssetDetailPage from './pages/AssetDetailPage'
import AssetsListPage from './pages/AssetsListPage'
import ClipsPage from './pages/ClipsPage'
import CoachPage from './pages/CoachPage'
import ConnectPage from './pages/ConnectPage'
import CustomerPage from './pages/CustomerPage'
import LoginPage from './pages/LoginPage'
import MaterialPage from './pages/MaterialPage'
import NotFoundPage from './pages/NotFoundPage'
import OpsPage from './pages/OpsPage'
import OverviewPage from './pages/OverviewPage'
import ProductsPage from './pages/ProductsPage'
import ServicePage from './pages/ServicePage'

function RequireOperator() {
  const { operator, bootstrapping } = useAuth()
  const location = useLocation()

  if (bootstrapping) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-[13px] text-ink-3">
        正在确认操作者会话…
      </div>
    )
  }
  if (operator === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          {/* 顾客通道（ADR 0021）：不登录、不走 RequireOperator/AppShell */}
          <Route path="/customer" element={<CustomerPage />} />
          {/* 第 45b 刀：可嵌入小组件的 iframe 落点——与 /customer 同一份客服逻辑，
              只收窄布局（embed）。宿主白名单由服务端判（WIDGET_ALLOWED_ORIGINS）。 */}
          <Route path="/widget" element={<CustomerPage embed />} />
          <Route element={<RequireOperator />}>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/platform/assets" element={<AssetsListPage />} />
            <Route path="/platform/assets/:id" element={<AssetDetailPage />} />
            <Route path="/platform/products" element={<ProductsPage />} />
            <Route path="/connect" element={<ConnectPage />} />
            <Route path="/material" element={<MaterialPage />} />
            <Route path="/clips" element={<ClipsPage />} />
            <Route path="/coach" element={<CoachPage />} />
            <Route path="/ops" element={<OpsPage />} />
            <Route path="/service" element={<ServicePage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
