// 路由表（本刀四路由 + 404）：/login、/platform/assets、/platform/assets/:id、
// /platform/products；/ 重定向资产列表。路径结构按后续八页预留，不预建空入口。

import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import AppShell from './components/AppShell'
import AssetDetailPage from './pages/AssetDetailPage'
import AssetsListPage from './pages/AssetsListPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import ProductsPage from './pages/ProductsPage'

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
          <Route element={<RequireOperator />}>
            <Route path="/" element={<Navigate to="/platform/assets" replace />} />
            <Route path="/platform/assets" element={<AssetsListPage />} />
            <Route path="/platform/assets/:id" element={<AssetDetailPage />} />
            <Route path="/platform/products" element={<ProductsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
