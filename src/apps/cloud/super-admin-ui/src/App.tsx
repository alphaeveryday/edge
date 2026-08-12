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
import { OverviewPage } from './pages/OverviewPage';
import { TenantsPage } from './pages/TenantsPage';
import { TenantDetailPage } from './pages/TenantDetailPage';
import { SourcesPage } from './pages/SourcesPage';
import { GridPage } from './pages/GridPage';
import { MinutePage } from './pages/MinutePage';
import { NewsLineagePage } from './pages/NewsLineagePage';
import { HoldingsImpactPage } from './pages/HoldingsImpactPage';
import { AnalysesPage } from './pages/AnalysesPage';
import { AnalysisSymbolPage } from './pages/AnalysisSymbolPage';
import { AnalysisDetailPage } from './pages/AnalysisDetailPage';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireSession />}>
        <Route element={<AdminLayout />}>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/tenants" element={<TenantsPage />} />
          <Route path="/tenants/:id" element={<TenantDetailPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/grid" element={<GridPage />} />
          <Route path="/minute" element={<MinutePage />} />
          <Route path="/lineage/news" element={<NewsLineagePage />} />
          <Route path="/impact/holdings" element={<HoldingsImpactPage />} />
          <Route path="/analyses" element={<AnalysesPage />} />
          {/* 종목 상세 — 구체 경로라 `:id` 보다 **먼저** 둔다. 뒤에 두면 `symbol` 이 분석 id 로
              잡혀 상세 화면이 "해당 분석 건을 찾을 수 없습니다"를 띄운다.
              시장·코드는 **경로가 아니라 쿼리**로 받는다(`symbols.symbolHref`) — CloudFront SPA
              fallback 이 마지막 조각의 점(.)을 정적 파일로 갈라, `BRK.B` 류 티커의 공유 링크·
              새로고침만 index.html 을 못 받는다. 사건 딥링크가 같은 이유로 쿼리다. */}
          <Route path="/analyses/symbol" element={<AnalysisSymbolPage />} />
          <Route path="/analyses/:id" element={<AnalysisDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
