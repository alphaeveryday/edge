/* EDGE Admin — 라우트 트리 (디자인 v0.1 IA + 로그인 ALPHA-616).
 *
 *   /login (공개)                  — 운영자 로그인
 *   RequireSession ▸ AdminLayout  — /(오늘 사건) · /ops/* · /tenants(/:id) · /sources · /grid · /analyses(/:id)
 *
 * 로그인 외 전 표면은 세션 필수(API fail-closed 와 짝) — 미인증·만료는 /login 으로 보낸다.
 * 첫 화면은 오늘 사건이다(ALPHA-738) — 운영자의 첫 질문("오늘 뭐가 깨졌나")이 첫 화면이어야 한다.
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { AdminLayout } from './layouts/AdminLayout';
import { RequireSession } from './layouts/RequireSession';
import { LoginPage } from './pages/LoginPage';
import { IncidentsPage } from './pages/ops/IncidentsPage';
import { IncidentsListPage } from './pages/ops/IncidentsListPage';
import { IncidentDetailPage } from './pages/ops/IncidentDetailPage';
import { RunAxisPage } from './pages/ops/RunAxisPage';
import { ChainPage } from './pages/ops/ChainPage';
import { DatasetPage } from './pages/ops/DatasetPage';
import { TrendPage } from './pages/ops/TrendPage';
import { DeliveryPage } from './pages/ops/DeliveryPage';
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
import { AnalysisSymbolPage } from './pages/AnalysisSymbolPage';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireSession />}>
        <Route element={<AdminLayout />}>
          {/* 규칙 엔진 축(ALPHA-738) — 홈은 사건 목록이고, 각 축은 사이드바의 형제 화면이다.
           * 카드 클릭은 축 화면의 해당 행으로 떨어진다(?focus=…). 이전 레인 원장 요약은 /overview. */}
          <Route path="/" element={<IncidentsPage />} />
          {/* 오늘(요약)과 문제·사건(전체 목록)은 역할이 다른 화면이다 */}
          <Route path="/ops/incidents" element={<IncidentsListPage />} />
          {/* 사건 상세 — 조사의 출발점. 라우트라서 뒤로 가기·딥링크가 그대로 동작한다 */}
          <Route path="/ops/incidents/:vid" element={<IncidentDetailPage />} />
          <Route path="/ops/runs" element={<RunAxisPage />} />
          <Route path="/ops/chain" element={<ChainPage />} />
          <Route path="/ops/datasets" element={<DatasetPage />} />
          <Route path="/ops/trend" element={<TrendPage />} />
          <Route path="/ops/delivery" element={<DeliveryPage />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/tenants" element={<TenantsPage />} />
          <Route path="/tenants/:id" element={<TenantDetailPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/grid" element={<GridPage />} />
          <Route path="/minute" element={<MinutePage />} />
          <Route path="/lineage/news" element={<NewsLineagePage />} />
          <Route path="/impact/holdings" element={<HoldingsImpactPage />} />
          <Route path="/analyses" element={<AnalysesPage />} />
          {/* 종목 상세 — 구체 경로라 :id 보다 먼저 둔다(뒤에 두면 symbol 이 분석 id 로 잡힌다) */}
          <Route path="/analyses/symbol/:market/:code" element={<AnalysisSymbolPage />} />
          <Route path="/analyses/:id" element={<AnalysisDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
