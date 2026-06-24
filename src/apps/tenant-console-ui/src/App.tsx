/* EDGE Console — 라우트 트리 (레이아웃 라우트 + 중첩 라우트).
 *
 * IA
 *   AuthLayout         /login · /invite/:token · /forgot-password
 *   OnboardingLayout   /onboarding/*
 *   ConsoleLayout      /dashboard · /applications · /usage · /compliance · /members · /settings
 *     └ ApplicationLayout  /applications/:appId/{overview|analysis|widget|webhooks}
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { AuthLayout } from './layouts/AuthLayout';
import { OnboardingLayout } from './layouts/OnboardingLayout';
import { ConsoleLayout } from './layouts/ConsoleLayout';
import { ApplicationLayout } from './layouts/ApplicationLayout';

import { LoginPage } from './pages/auth/LoginPage';
import { InvitePage } from './pages/auth/InvitePage';
import { ForgotPasswordPage } from './pages/auth/ForgotPasswordPage';

import { DashboardPage } from './pages/DashboardPage';
import { ApplicationsPage } from './pages/ApplicationsPage';
import { UsagePage } from './pages/UsagePage';
import { CompliancePage } from './pages/CompliancePage';
import { MembersPage } from './pages/MembersPage';
import { SettingsPage } from './pages/SettingsPage';
import { OnboardingPage } from './pages/consolePages';

import { OverviewTab } from './pages/application/OverviewTab';
import { AnalysisTab } from './pages/application/AnalysisTab';
import { WidgetTab } from './pages/application/WidgetTab';
import { WebhooksTab } from './pages/application/WebhooksTab';

export function App() {
  return (
    <Routes>
      {/* 인증 */}
      <Route element={<AuthLayout />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/invite/:token" element={<InvitePage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      </Route>

      {/* 온보딩 */}
      <Route path="/onboarding" element={<OnboardingLayout />}>
        <Route index element={<OnboardingPage />} />
      </Route>

      {/* 콘솔 */}
      <Route element={<ConsoleLayout />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/applications" element={<ApplicationsPage />} />
        <Route path="/usage" element={<UsagePage />} />
        <Route path="/compliance" element={<CompliancePage />} />
        <Route path="/members" element={<MembersPage />} />
        <Route path="/settings" element={<SettingsPage />} />

        {/* 앱 상세 (탭) */}
        <Route path="/applications/:appId" element={<ApplicationLayout />}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<OverviewTab />} />
          <Route path="analysis" element={<AnalysisTab />} />
          <Route path="widget" element={<WidgetTab />} />
          <Route path="webhooks" element={<WebhooksTab />} />
        </Route>
      </Route>

      {/* 기본 진입 */}
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
