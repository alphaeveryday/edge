/* EDGE Console — 라우트 트리 (디자인 v0.2 IA + 로그인 ALPHA-626).
 *
 *   /login (공개)                    — 검수자·관리자 로그인
 *   RequireSession ▸ ConsoleLayout  — /dashboard · /explanations(/:id) · /review(/:id)
 *                                     · /screening · /scope · /users
 *
 * 로그인 외 전 표면은 세션 필수(API fail-closed 와 짝) — 미인증·만료는 /login 으로 보낸다.
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { ConsoleLayout } from './layouts/ConsoleLayout';
import { RequireSession } from './layouts/RequireSession';
import { LoginPage } from './pages/LoginPage';
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
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireSession />}>
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
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
