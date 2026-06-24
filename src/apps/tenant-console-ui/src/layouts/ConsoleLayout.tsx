/* ConsoleLayout — 사이드바 + 탑바 셸. 콘솔 영역의 모든 페이지가 이 안에서 렌더된다.
 *
 * NOTE: 사이드바의 org/user 표기는 지금 정적 chrome 이다. 추후 session/org 도메인이
 * 생기면 동일한 repository/hook 패턴으로 교체한다 (페이지처럼 mock 직접 import 금지).
 */
import { useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Avatar, Icon } from '../components';

const ORG = { name: '한빛투자증권', short: '한빛', domain: 'hanbit.co.kr' };
const USER = { name: '김도현', role: 'Owner', initial: '김' };

interface NavEntry {
  label: string;
  icon: string;
  to: string;
}

const NAV: NavEntry[] = [
  { label: '홈 / 대시보드', icon: 'home', to: '/dashboard' },
  { label: '애플리케이션', icon: 'grid', to: '/applications' },
  { label: '사용량', icon: 'activity', to: '/usage' },
  { label: '컴플라이언스', icon: 'shieldCheck', to: '/compliance' },
  { label: '구성원', icon: 'users', to: '/members' },
  { label: '설정', icon: 'settings', to: '/settings' },
];

function Sidebar() {
  const navigate = useNavigate();
  return (
    <aside className="sidebar">
      <div className="sb-brand">
        <div className="sb-logo">
          <Icon n="logoFill" s={22} />
        </div>
        <div style={{ lineHeight: 1.1 }}>
          <div style={{ fontSize: 16, fontWeight: 750, letterSpacing: '.06em' }}>EDGE</div>
          <div style={{ fontSize: 9.5, letterSpacing: '.22em', opacity: 0.6, fontWeight: 600 }}>
            CONSOLE
          </div>
        </div>
      </div>

      <div className="org-switch org-static">
        <div className="org-mark">{ORG.short}</div>
        <div style={{ lineHeight: 1.25, textAlign: 'left', flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 650,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {ORG.name}
          </div>
          <div style={{ fontSize: 11, opacity: 0.6 }}>{ORG.domain}</div>
        </div>
      </div>

      <nav className="sb-nav">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => 'nav-item' + (isActive ? ' act' : '')}
          >
            <Icon n={item.icon} s={18} sw={1.9} />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sb-foot">
        <button className="nav-item" onClick={() => navigate('/settings')}>
          <Avatar initial={USER.initial} size={26} />
          <div style={{ lineHeight: 1.2, textAlign: 'left', flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {USER.name}
            </div>
            <div style={{ fontSize: 10.5, opacity: 0.55 }}>{USER.role}</div>
          </div>
        </button>
        <button className="nav-item logout" onClick={() => navigate('/login')}>
          <Icon n="logout" s={18} sw={1.9} />
          <span>로그아웃</span>
        </button>
      </div>
    </aside>
  );
}

const NOTIFS: [string, string, 'warn' | 'info' | 'ok'][] = [
  ['이상 트래픽 감지', '리서치랩 에러율 1.0% 초과 · 14:02', 'warn'],
  ['면책문구 v4 발행', '컴플라이언스 검토 완료 · 13:58', 'info'],
  ['키 재발급 완료', 'pk_live_77c1…8be', 'ok'],
];

function Topbar() {
  const navigate = useNavigate();
  const [openN, setOpenN] = useState(false);
  return (
    <header className="topbar">
      <div style={{ flex: 1, minWidth: 0 }} />
      <div className="row gap6" style={{ flex: 'none' }}>
        <div style={{ position: 'relative' }}>
          <button className="tb-icon" onClick={() => setOpenN((o) => !o)} title="알림">
            <Icon n="bell" s={18} />
            <span className="tb-badge" />
          </button>
          {openN && (
            <div className="pop" style={{ right: 0, width: 300 }}>
              <div
                style={{
                  padding: '12px 14px',
                  borderBottom: '1px solid var(--n-150)',
                  fontWeight: 650,
                  fontSize: 13,
                }}
              >
                알림
              </div>
              {NOTIFS.map((r, i) => (
                <div key={i} className="pop-row">
                  <span className={'dot-' + r[2]} />
                  <div>
                    <div style={{ fontSize: 12.5, fontWeight: 600 }}>{r[0]}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--n-500)' }}>{r[1]}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <button className="tb-avatar" onClick={() => navigate('/settings')}>
          <Avatar initial={USER.initial} size={30} />
        </button>
      </div>
    </header>
  );
}

export function ConsoleLayout() {
  // key 를 경로로 바꿔 라우트 전환마다 본문을 리마운트(스크롤 상단 복귀 + fade-up).
  const { pathname } = useLocation();
  return (
    <div className="app-grid">
      <Sidebar />
      <div className="app-main">
        <Topbar />
        <main className="app-content" key={pathname}>
          <div className="content-inner fade-up">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
