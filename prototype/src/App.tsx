import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import Overview from './pages/Overview'
import AssetsList from './pages/AssetsList'
import AssetDetail from './pages/AssetDetail'
import Products from './pages/Products'
import Service from './pages/Service'
import Ops from './pages/Ops'
import Materials from './pages/Materials'
import Clips from './pages/Clips'
import Coach from './pages/Coach'
import Connect from './pages/Connect'
import Models from './pages/Models'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Overview />} />
          <Route path="/platform/assets" element={<AssetsList />} />
          <Route path="/platform/assets/:id" element={<AssetDetail />} />
          <Route path="/platform/products" element={<Products />} />
          <Route path="/service" element={<Service />} />
          <Route path="/ops" element={<Ops />} />
          <Route path="/materials" element={<Materials />} />
          <Route path="/clips" element={<Clips />} />
          <Route path="/coach" element={<Coach />} />
          <Route path="/finetune" element={<Navigate to="/models" replace />} />
          <Route path="/connect" element={<Connect />} />
          <Route path="/models" element={<Models />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
