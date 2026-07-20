/* AuthLayout — 로그인/초대/비밀번호 찾기 공용 셸 (네이비 사이드 패널 + 폼 영역) */
import { Outlet } from 'react-router-dom';
import { Icon } from '../components';

export function AuthLayout() {
  return (
    <div className="auth-wrap">
      <aside className="auth-aside">
        <div className="row gap10">
          <div className="sb-logo">
            <Icon n="logoFill" s={22} />
          </div>
          <div style={{ lineHeight: 1.1 }}>
            <div style={{ fontSize: 18, fontWeight: 750, letterSpacing: '.06em' }}>EDGE</div>
            <div style={{ fontSize: 10, letterSpacing: '.22em', opacity: 0.6, fontWeight: 600 }}>
              CONSOLE
            </div>
          </div>
        </div>
        <div>
          <h2 style={{ fontSize: 26, fontWeight: 720, lineHeight: 1.35, letterSpacing: '-.02em' }}>
            증권 콘텐츠를
            <br />
            규제 안에서 빠르게.
          </h2>
          <p style={{ marginTop: 14, fontSize: 14, opacity: 0.7, lineHeight: 1.6, maxWidth: 360 }}>
            이벤트 기반 분석을 위젯·API 로 제공하고, 컴플라이언스 검수까지 한 콘솔에서
            관리합니다.
          </p>
        </div>
        <div style={{ fontSize: 12, opacity: 0.5 }}>© 2026 EDGE</div>
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
