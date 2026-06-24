/* ApplicationLayout — 앱 상세 셸. 앱 헤더(브레드크럼·이름·환경) + 탭 네비게이션을
 * 제공하고 하위 탭 페이지를 Outlet 으로 렌더한다. appId(=slug)로 앱을 조회한다.
 */
import { NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { EnvBadge, Icon } from '../components';
import { useApplication } from '../domains/applications';

const TABS: [string, string, string][] = [
  ['overview', '앱 개요', 'grid'],
  ['analysis', '분석 설정', 'sliders'],
  ['widget', '위젯', 'code'],
  ['webhooks', '웹훅', 'webhook'],
];

export function ApplicationLayout() {
  const { appId } = useParams();
  const nav = useNavigate();
  const { pathname } = useLocation();
  const { app, loading } = useApplication(appId);

  if (loading) {
    // 로딩 중 가벼운 스켈레톤
    return (
      <div>
        <div className="skel-bar" style={{ height: 46, width: '40%' }} />
      </div>
    );
  }

  if (!app) {
    return (
      <div className="empty-state">
        <div className="empty-ico">
          <Icon n="alert" s={26} />
        </div>
        <div style={{ marginTop: 12, fontSize: 14, fontWeight: 600, color: 'var(--n-700)' }}>
          앱을 찾을 수 없습니다
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* 브레드크럼 */}
      <div
        className="row gap6"
        style={{ fontSize: 12.5, color: 'var(--n-500)', fontWeight: 600, marginBottom: 11 }}
      >
        <span
          style={{ cursor: 'pointer' }}
          onClick={() => nav('/applications')}
        >
          애플리케이션
        </span>
        <Icon n="chevR" s={13} style={{ color: 'var(--n-300)' }} />
        <span style={{ color: 'var(--n-700)' }}>{app.name}</span>
      </div>

      {/* 앱 헤더 */}
      <div className="row spread gap16" style={{ alignItems: 'flex-end', marginBottom: 18 }}>
        <div className="row gap10" style={{ alignItems: 'center', minWidth: 0 }}>
          <div
            className="app-ico"
            style={{ width: 46, height: 46, borderRadius: 12, flex: 'none' }}
          >
            <Icon n={app.type === '모바일' ? 'grid' : 'building2'} s={22} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="row gap10" style={{ alignItems: 'center' }}>
              <h1
                style={{
                  fontSize: 23,
                  fontWeight: 730,
                  letterSpacing: '-.022em',
                  color: 'var(--n-900)',
                }}
              >
                {app.name}
              </h1>
              <EnvBadge env={app.env} />
            </div>
            <div className="mono" style={{ fontSize: 11.5, color: 'var(--n-500)', marginTop: 3 }}>
              {app.id}
            </div>
          </div>
        </div>
        <button className="btn btn-ghost" onClick={() => nav('/applications')}>
          <Icon n="list" s={16} />
          목록
        </button>
      </div>

      {/* 탭 */}
      <div className="tabs">
        {TABS.map(([slug, label, icon]) => (
          <NavLink key={slug} to={slug} className={({ isActive }) => 'tab' + (isActive ? ' on' : '')}>
            <Icon n={icon} s={15} />
            {label}
          </NavLink>
        ))}
      </div>

      <div key={pathname} className="fade-up">
        <Outlet />
      </div>
    </div>
  );
}
