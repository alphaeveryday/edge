/* EDGE Admin — 라우트 트리 (디자인 v0.1 IA + 로그인 ALPHA-616).
 *
 *   /login (공개)                  — 운영자 로그인
 *   RequireSession ▸ AdminLayout  — /(Overview) · /tenants(/:id) · /sources · /grid · /analyses(/:id)
 *
 * 로그인 외 전 표면은 세션 필수(API fail-closed 와 짝) — 미인증·만료는 /login 으로 보낸다.
 * 첫 화면은 Run Overview 다(ALPHA-683) — 운영자의 첫 질문("오늘 정상인가")이 첫 화면이어야 한다.
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { AdminLayout } from './layouts/AdminLayout';
import { RequireSession } from './layouts/RequireSession';
import { LoginPage } from './pages/LoginPage';
import { ConsolePage } from './pages/ConsolePage';
import { OverviewPage } from './pages/OverviewPage';
import { TenantsPage } from './pages/TenantsPage';
import { TenantDetailPage } from './pages/TenantDetailPage';
import { SourcesPage } from './pages/SourcesPage';
import { GridPage } from './pages/GridPage';
import { MinutePage } from './pages/MinutePage';
import { NewsLineagePage } from './pages/NewsLineagePage';
import { HoldingsImpactPage } from './pages/HoldingsImpactPage';
import { AnalysesPage } from './pages/AnalysesPage';
import { AnalysisDetailPage } from './pages/AnalysisDetailPage';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireSession />}>
        <Route element={<AdminLayout />}>
          {/* 홈 = 규칙 엔진 콘솔(ALPHA-738). 이전 홈은 디자인 검수 비교용으로 /overview 에 잔존 */}
          <Route path="/" element={<ConsolePage />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/tenants" element={<TenantsPage />} />
          <Route path="/tenants/:id" element={<TenantDetailPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/grid" element={<GridPage />} />
          <Route path="/minute" element={<MinutePage />} />
          <Route path="/lineage/news" element={<NewsLineagePage />} />
          <Route path="/impact/holdings" element={<HoldingsImpactPage />} />
          <Route path="/analyses" element={<AnalysesPage />} />
          <Route path="/analyses/:id" element={<AnalysisDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
