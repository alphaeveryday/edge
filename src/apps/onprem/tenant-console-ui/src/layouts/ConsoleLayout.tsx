import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary, Icon, Modal, Toaster, toast } from 'ui-kit';
import type { IconName } from 'ui-kit';
import { useFeedStatus, useStatusCounts } from '../domains/explanations/hooks';
import { FEED_DOT_COLOR, FEED_LABEL } from '../domains/explanations';
import { ROLE_LABEL } from '../domains/users';
import { useLogout, useSession, useUpdateDisplayName } from '../domains/session/hooks';

interface NavEntry {
  path: string;
  label: string;
  icon: IconName;
  /** 이 경로들로 시작하면 active (상세 라우트 포함) */
  match: string[];
  /** TENANT_ADMIN 전용 항목 — 그 외 역할에는 노출하지 않는다(permission-matrix.md). */
  adminOnly?: boolean;
}

const NAV_SECTIONS: { section?: string; items: NavEntry[] }[] = [
  { items: [{ path: '/dashboard', label: '대시보드', icon: 'dashboard', match: ['/dashboard'] }] },
  {
    section: '콘텐츠 운영',
    items: [
      { path: '/explanations', label: '가격 변동 설명 관리', icon: 'fileText', match: ['/explanations'] },
      { path: '/review', label: '검수 관리', icon: 'clipboardCheck', match: ['/review'] },
    ],
  },
  {
    section: '정책',
    items: [{ path: '/screening', label: '점검 기준 관리', icon: 'shield', match: ['/screening'] }],
  },
  {
    section: '환경 설정',
    items: [
      { path: '/scope', label: '제공 범위 설정', icon: 'sliders', match: ['/scope'] },
      { path: '/users', label: '사용자 및 권한', icon: 'users', match: ['/users'], adminOnly: true },
    ],
  },
];

const PAGE_TITLES: [prefix: string, title: string][] = [
  ['/dashboard', '대시보드'],
  ['/explanations/', '가격 변동 설명 상세'],
  ['/explanations', '가격 변동 설명 관리'],
  ['/review/', '검수 대기 상세'],
  ['/review', '검수 관리'],
  ['/screening', '점검 기준 관리'],
  ['/scope', '제공 범위 설정'],
  ['/users', '사용자 및 권한'],
];

export function ConsoleLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const logout = useLogout();
  // 배지 데이터의 조회 실패는 조용히 사라지게 두지 않고 '확인 불가' 신호로 표면화한다 (Rule 12).
  const { data: feed, isError: feedUnavailable } = useFeedStatus();
  // 배지는 서버 집계로 센다(ALPHA-914) — 목록이 페이지 단위라 로드된 페이지로는 못 센다.
  const { data: statusCounts, isError: pendingUnavailable } = useStatusCounts();
  const updateName = useUpdateDisplayName();

  const [accountOpen, setAccountOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileDraft, setProfileDraft] = useState('');
  const [profileError, setProfileError] = useState(false);

  useEffect(() => {
    if (!accountOpen) return;
    const close = () => setAccountOpen(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [accountOpen]);

  const pendingCount = statusCounts?.REVIEW_REQUIRED ?? 0;
  const pageTitle = PAGE_TITLES.find(([prefix]) => location.pathname.startsWith(prefix))?.[1] ?? '';
  const userInitial = session?.name?.[0] ?? '?';

  const saveProfile = () => {
    const name = profileDraft.trim();
    if (!name) {
      setProfileError(true);
      return;
    }
    updateName.mutate(name, {
      onSuccess: () => {
        setProfileOpen(false);
        toast('프로필이 저장되었습니다.');
      },
    });
  };

  return (
    <div className="edge-root flex h-screen overflow-hidden" style={{ background: 'var(--bg-app)' }}>
      {/* ============ 사이드바 ============ */}
      <aside
        className="flex flex-col overflow-y-auto"
        style={{ width: 'var(--sidebar-w)', flex: 'none', background: 'var(--bg-nav)' }}
      >
        <div
          className="flex items-center gap-2 px-4"
          style={{ height: 'var(--header-h)', borderBottom: '1px solid rgba(255,255,255,.08)', flex: 'none' }}
        >
          <span
            className="inline-flex items-center justify-center"
            style={{
              width: 20, height: 20, background: 'linear-gradient(135deg,#8dd8f8,#41abe0)', borderRadius: 4,
              fontSize: 8, fontWeight: 800, color: '#fff', flex: 'none',
            }}
          >
            {session?.tenantMark ?? ''}
          </span>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#fff' }}>{session?.tenantName ?? ''}</div>
        </div>
        <nav className="flex-1 pt-2 pb-4">
          {NAV_SECTIONS.map(({ section, items }) => (
            <div key={section ?? 'root'}>
              {section && <div className="nav-section">{section}</div>}
              {items
                .filter((item) => !item.adminOnly || session?.role === 'TENANT_ADMIN')
                .map((item) => {
                const active = item.match.some((m) => location.pathname.startsWith(m));
                return (
                  <div
                    key={item.path}
                    className={`nav-item${active ? ' active' : ''}`}
                    onClick={() => navigate(item.path)}
                  >
                    <Icon name={item.icon} className="ic" />
                    <span className="flex-1">{item.label}</span>
                    {item.path === '/review' && (pendingUnavailable || pendingCount > 0) && (
                      <span
                        className="num inline-flex items-center justify-center"
                        title={pendingUnavailable ? '검수 대기 수를 확인하지 못했습니다' : undefined}
                        style={{
                          fontSize: 10, fontWeight: 700, color: '#fff',
                          background: pendingUnavailable ? 'var(--gray-400)' : 'var(--warn)',
                          borderRadius: 999, minWidth: 16, height: 16, padding: '0 4px',
                        }}
                      >
                        {pendingUnavailable ? '?' : pendingCount}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </nav>
      </aside>

      {/* ============ 메인 ============ */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex items-center gap-3 px-6"
          style={{
            height: 'var(--header-h)', flex: 'none',
            background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)',
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>{pageTitle}</div>
          <div className="flex-1" />
          {(feed || feedUnavailable) && (
            <div
              className="flex items-center gap-1.5 whitespace-nowrap"
              style={{
                height: 24, padding: '0 10px', border: '1px solid var(--border)',
                borderRadius: 999, fontSize: 11, color: 'var(--fg-2)',
              }}
            >
              {/* 재조회 실패 시 stale data 가 남아도 에러 신호가 우선한다 — 낡은 정상 상태를 현재처럼 보이지 않게 */}
              <span className="dot" style={{ background: feed && !feedUnavailable ? FEED_DOT_COLOR[feed.state] : 'var(--gray-400)' }} />
              {feed && !feedUnavailable ? (
                <>
                  <span>수신 {FEED_LABEL[feed.state]}</span>
                  <span style={{ color: 'var(--fg-4)' }}>· 최근 수신 {feed.lastReceivedRelative}</span>
                </>
              ) : (
                <span>수신 상태 확인 불가</span>
              )}
            </div>
          )}
          <div className="relative">
            <div
              className="flex cursor-pointer items-center gap-2 rounded-[5px] px-2 py-1 -mx-2 -my-1"
              style={{ background: accountOpen ? 'var(--bg-hover)' : 'transparent' }}
              onClick={(e) => {
                e.stopPropagation();
                setAccountOpen((v) => !v);
              }}
            >
              <div
                className="flex items-center justify-center rounded-full"
                style={{ width: 26, height: 26, background: 'var(--gray-200)', color: 'var(--fg-2)', fontSize: 11, fontWeight: 600 }}
              >
                {userInitial}
              </div>
              <span style={{ fontSize: 12, color: 'var(--fg-2)' }}>{session?.name ?? ''}</span>
              <Icon name="chevronDown" size={12} strokeWidth={1.8} className="text-(--fg-4)" />
            </div>
            {accountOpen && (
              <div className="popover absolute right-0 z-[120]" style={{ top: 'calc(100% + 8px)', width: 220 }}>
                <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border-faint)' }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{session?.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>{session?.email}</div>
                  <div className="mt-1.5">
                    <span className="chip">{session ? ROLE_LABEL[session.role] : ''}</span>
                  </div>
                </div>
                <div className="p-1">
                  {session?.role === 'TENANT_ADMIN' && (
                    <div className="menu-item" onClick={() => navigate('/users')}>
                      <Icon name="users" size={14} />
                      사용자 및 권한
                    </div>
                  )}
                  <div
                    className="menu-item"
                    onClick={() => {
                      setProfileOpen(true);
                      setProfileDraft(session?.name ?? '');
                      setProfileError(false);
                    }}
                  >
                    <Icon name="settings" size={14} />
                    프로필 설정
                  </div>
                </div>
                <div className="p-1" style={{ borderTop: '1px solid var(--border-faint)' }}>
                  <div
                    className="menu-item danger"
                    onClick={() =>
                      logout.mutate(undefined, {
                        onSuccess: () => {
                          // navigate 먼저, clear 나중 — 순서를 뒤집으면 마운트된 활성 쿼리들이
                          // 일제히 refetch 하며 401 을 만나 '세션 만료' 배너로 오표면된다.
                          navigate('/login');
                          queryClient.clear();
                        },
                      })
                    }
                  >
                    <Icon name="logOut" size={14} />
                    로그아웃
                  </div>
                </div>
              </div>
            )}
          </div>
        </header>

        {/* pathname key로 리마운트 — 라우트 이동 시 스크롤 위치가 이전 화면 것을 물려받지 않게 */}
        <main key={location.pathname} className="flex-1 overflow-y-auto p-6">
          {/* 렌더 예외를 흰 화면 대신 안내 카드로 — key 리마운트가 라우트 전환 시 리셋한다 */}
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      {/* ============ 프로필 설정 모달 ============ */}
      <Modal
        open={profileOpen}
        title="프로필 설정"
        width={420}
        onClose={() => setProfileOpen(false)}
        footer={
          <>
            <button className="btn btn-ghost" onClick={() => setProfileOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={saveProfile}>
              저장
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3.5 p-5">
          <div className="flex items-center gap-3">
            <div
              className="flex items-center justify-center rounded-full"
              style={{ width: 40, height: 40, background: 'var(--gray-200)', color: 'var(--fg-2)', fontSize: 15, fontWeight: 600, flex: 'none' }}
            >
              {userInitial}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{session?.name}</div>
              <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>
                {session?.role} · {session?.tenantName}
              </div>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="t-label">표시 이름</span>
            <label className="field">
              <input
                value={profileDraft}
                onChange={(e) => {
                  setProfileDraft(e.target.value);
                  setProfileError(false);
                }}
              />
            </label>
            {profileError && <span style={{ fontSize: 11, color: 'var(--down)' }}>표시 이름을 입력하세요.</span>}
          </div>
          <div className="flex flex-col gap-1">
            <span className="t-label">이메일</span>
            <div
              className="flex items-center"
              style={{
                height: 32, padding: '0 10px', background: 'var(--bg-sunken)',
                border: '1px solid var(--border)', borderRadius: 5,
                fontSize: 12, color: 'var(--fg-3)', cursor: 'not-allowed',
              }}
            >
              {session?.email}
            </div>
          </div>
        </div>
      </Modal>

      <Toaster />
    </div>
  );
}
