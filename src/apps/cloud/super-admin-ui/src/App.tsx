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
          {/* 종목 상세 — 시장·코드를 **경로가 아니라 쿼리**로 받는다(`symbols.symbolHref`).
              CloudFront SPA fallback(`spa-rewrite.js`)이 마지막 조각의 점(.)을 정적 파일로 갈라,
              `BRK.B` 류 티커를 경로에 두면 그 종목의 **공유 링크·새로고침만** index.html 을 못
              받는다 — 이 화면이 존재하는 이유가 정확히 공유 가능한 종목 이력이다.
              ⚠️ 사건 딥링크도 같은 이유로 쿼리로 **설계돼 있으나 그 화면은 아직 여기 없다**
              (규칙 엔진 화면 미착지) — 선례는 그 설계지 돌고 있는 라우트가 아니다.
              ⚠️ 읽기 좋으라고 `:id` 앞에 뒀을 뿐 **선언 순서는 계약이 아니다** — react-router 7
              은 정적 조각을 동적 조각보다 높게 랭크해 순서를 뒤집어도 여기로 매칭된다. 순서에
              기대는 계약은 `layouts/headerRoute.ts` 쪽이다(거긴 손으로 쓴 if 체인이라 진짜다). */}
          <Route path="/analyses/symbol" element={<AnalysisSymbolPage />} />
          <Route path="/analyses/:id" element={<AnalysisDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
