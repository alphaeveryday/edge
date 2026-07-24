/* EDGE Admin — 라우트 트리 (디자인 v0.1 IA).
 *
 *   AdminLayout   /tenants(/:id) · /sources · /analyses(/:id)
 *
 * 운영자 인증 화면은 시안에 없다 — 진입은 테넌트 목록 직행 (ALPHA-487 범위 밖).
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { AdminLayout } from './layouts/AdminLayout';
import { TenantsPage } from './pages/TenantsPage';
import { TenantDetailPage } from './pages/TenantDetailPage';
import { SourcesPage } from './pages/SourcesPage';
import { AnalysesPage } from './pages/AnalysesPage';
import { AnalysisDetailPage } from './pages/AnalysisDetailPage';

export function App() {
  return (
    <Routes>
      <Route element={<AdminLayout />}>
        <Route path="/tenants" element={<TenantsPage />} />
        <Route path="/tenants/:id" element={<TenantDetailPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/analyses" element={<AnalysesPage />} />
        <Route path="/analyses/:id" element={<AnalysisDetailPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/tenants" replace />} />
    </Routes>
  );
}
