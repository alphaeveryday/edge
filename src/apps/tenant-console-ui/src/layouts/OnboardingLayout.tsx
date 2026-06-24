/* OnboardingLayout — 신규 조직/구성원 온보딩 셸 (중앙 정렬 스텝 영역) */
import { Outlet } from 'react-router-dom';
import { Icon } from '../components';

export function OnboardingLayout() {
  return (
    <div style={{ minHeight: '100vh', background: 'var(--n-50)' }}>
      <header
        className="row"
        style={{ height: 60, padding: '0 28px', borderBottom: '1px solid var(--n-200)', gap: 11 }}
      >
        <div className="sb-logo" style={{ background: 'var(--p-600)', color: '#fff' }}>
          <Icon n="logoFill" s={20} />
        </div>
        <div style={{ fontSize: 15, fontWeight: 750, letterSpacing: '.06em', color: 'var(--n-900)' }}>
          EDGE <span style={{ fontSize: 11, opacity: 0.5, letterSpacing: '.2em' }}>CONSOLE</span>
        </div>
      </header>
      <main style={{ maxWidth: 760, margin: '0 auto', padding: '40px 24px 64px' }}>
        <Outlet />
      </main>
    </div>
  );
}
