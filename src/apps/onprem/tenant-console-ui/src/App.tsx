/* EDGE Console — 라우트 트리 (디자인 v0.2 IA).
 *
 *   ConsoleLayout   /dashboard · /explanations(/:id) · /review(/:id)
 *                   · /screening · /scope · /users
 *
 * 인증·온보딩 화면은 시안 미수령으로 이번 IA에 없다 — 진입은 대시보드로 직행 (ALPHA-486 범위 밖).
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { ConsoleLayout } from './layouts/ConsoleLayout';
import { DashboardPage } from './pages/DashboardPage';
import { ExplanationsPage } from './pages/ExplanationsPage';
import { ExplanationDetailPage } from './pages/ExplanationDetailPage';
import { ReviewPage } from './pages/ReviewPage';
import { ReviewDetailPage } from './pages/ReviewDetailPage';
import { ScreeningPage } from './pages/ScreeningPage';
import { ScopePage } from './pages/ScopePage';
import { UsersPage } from './pages/UsersPage';

export function App() {
  return (
    <Routes>
      <Route element={<ConsoleLayout />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/explanations" element={<ExplanationsPage />} />
        <Route path="/explanations/:id" element={<ExplanationDetailPage />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/review/:id" element={<ReviewDetailPage />} />
        <Route path="/screening" element={<ScreeningPage />} />
        <Route path="/scope" element={<ScopePage />} />
        <Route path="/users" element={<UsersPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
