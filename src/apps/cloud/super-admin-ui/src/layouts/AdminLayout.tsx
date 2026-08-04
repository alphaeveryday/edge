import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary, Icon, Modal, Toaster, toast } from 'ui-kit';
import type { IconName } from 'ui-kit';
import { useAnalyses } from '../domains/analyses/hooks';
import { useLogout, useSession, useUpdateDisplayName } from '../domains/session/hooks';
import { useTenants } from '../domains/tenants/hooks';

interface NavEntry {
  path: string;
  label: string;
  icon: IconName;
}

const NAV_SECTIONS: { section: string; items: NavEntry[] }[] = [
  {
    section: '운영 현황',
    /* 화면명은 답하는 질문이다. 홈은 규칙이 오늘 잡은 사건이고(ALPHA-738), 축 화면들은
     * 그 사건의 근거를 여는 형제 화면이다 — 화면 안에 또 탭을 만들지 않는다. */
    items: [
      { path: '/', label: '오늘 사건', icon: 'alertTriangle' },
      { path: '/ops/runs', label: '런·작업 귀결', icon: 'clipboardCheck' },
      { path: '/ops/chain', label: '설명 생산 체인', icon: 'trendChart' },
      { path: '/ops/datasets', label: '데이터셋 신선도', icon: 'database' },
      { path: '/ops/trend', label: '산출 추이', icon: 'trendChart' },
      { path: '/ops/delivery', label: '전달 경계', icon: 'shield' },
      { path: '/overview', label: '레인 원장 요약', icon: 'dashboard' },
    ],
  },
  {
    section: '테넌트 관리',
    items: [{ path: '/tenants', label: '테넌트 목록', icon: 'building' }],
  },
  {
    section: '가격 변동 분석 관리',
    items: [
      { path: '/sources', label: '데이터 소스 수집 상태', icon: 'database' },
      { path: '/grid', label: '파이프라인 실행 이력', icon: 'dashboard' },
      { path: '/minute', label: '장중 1분 수집', icon: 'database' },
      { path: '/lineage/news', label: '뉴스 계보', icon: 'database' },
      { path: '/impact/holdings', label: '구성종목 결손 영향', icon: 'trendChart' },
      { path: '/analyses', label: '가격 변동 분석 목록', icon: 'trendChart' },
    ],
  },
];

/** EDGE 마크 (시안 로고 — 상승 바 3개) */
function EdgeMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" fill="none" role="img" aria-label="EDGE mark" style={{ flex: 'none' }}>
      <rect x="0.5" y="0.5" width="31" height="31" rx="6.5" fill="#18181b" stroke="rgba(255,255,255,0.18)" />
      <rect x="8" y="17" width="3.4" height="7" rx="1" fill="#a1a1aa" />
      <rect x="14.3" y="12" width="3.4" height="12" rx="1" fill="#d4d4d8" />
      <rect x="20.6" y="8" width="3.4" height="16" rx="1" fill="#2e5aac" />
    </svg>
  );
}

export function AdminLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const { data: tenants } = useTenants();
  const { data: analyses } = useAnalyses();
  const updateName = useUpdateDisplayName();
  const logout = useLogout();

  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileDraft, setProfileDraft] = useState('');
  const [profileError, setProfileError] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [menuOpen]);

  // ---- 헤더 타이틀/서브 (라우트 → 화면명, 상세는 대상 엔티티 이름) ----
  const path = location.pathname;
  const tenantId = path.match(/^\/tenants\/([^/]+)$/)?.[1];
  const analysisId = path.match(/^\/analyses\/([^/]+)$/)?.[1];
  const tenant = tenantId ? tenants?.find((t) => t.id === tenantId) : undefined;
  const analysis = analysisId ? analyses?.find((a) => a.id === analysisId) : undefined;

  let pageTitle = '';
  let pageSub = '';
  if (tenantId) {
    pageTitle = '테넌트 상세';
    pageSub = tenant?.name ?? '';
  } else if (analysisId) {
    pageTitle = '가격 변동 분석 상세';
    pageSub = analysis ? `${analysis.name} ${analysis.code}` : '';
  } else if (path.startsWith('/tenants')) {
    pageTitle = '테넌트 목록';
  } else if (path.startsWith('/sources')) {
    pageTitle = '데이터 소스 수집 상태';
  } else if (path.startsWith('/grid')) {
    pageTitle = '파이프라인 실행 이력';
  } else if (path.startsWith('/minute')) {
    pageTitle = '장중 1분 수집';
  } else if (path.startsWith('/analyses')) {
    pageTitle = '가격 변동 분석 목록';
  } else if (path.startsWith('/lineage/news')) {
    pageTitle = '뉴스 계보';
  } else if (path.startsWith('/impact/holdings')) {
    pageTitle = '구성종목 결손 영향';
  } else if (path.startsWith('/ops/runs')) {
    pageTitle = '런·작업 귀결';
  } else if (path.startsWith('/ops/chain')) {
    pageTitle = '설명 생산 체인';
  } else if (path.startsWith('/ops/datasets')) {
    pageTitle = '데이터셋 신선도';
  } else if (path.startsWith('/ops/trend')) {
    pageTitle = '산출 추이';
  } else if (path.startsWith('/ops/delivery')) {
    pageTitle = '전달 경계';
  } else if (path.startsWith('/overview')) {
    pageTitle = '레인 원장 요약';
  } else if (path === '/') {
    pageTitle = '오늘 사건';
  }
  const showBack = Boolean(tenantId || analysisId);

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
      <nav
        className="flex flex-col overflow-y-auto"
        style={{ width: 'var(--sidebar-w)', flex: 'none', background: 'var(--bg-nav)' }}
      >
        <div
          className="flex items-center gap-2"
          style={{ padding: '16px 16px 14px', borderBottom: '1px solid rgba(255,255,255,.08)' }}
        >
          <EdgeMark />
          <div className="flex flex-col gap-px">
            <span style={{ color: '#fff', fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em' }}>EDGE</span>
            <span style={{ color: 'var(--gray-500)', fontSize: 10, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              Super Admin
            </span>
          </div>
        </div>

        {NAV_SECTIONS.map(({ section, items }) => (
          <div key={section}>
            <div className="nav-section">{section}</div>
            {items.map((item) => (
              <div
                key={item.path}
                /* '/' 는 startsWith 로 모든 경로에 붙는다 — 루트만 정확 일치로 가른다 */
                className={`nav-item${(item.path === '/' ? path === '/' : path.startsWith(item.path)) ? ' active' : ''}`}
                onClick={() => navigate(item.path)}
              >
                <Icon name={item.icon} className="ic" />
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        ))}

        <div className="flex-1" />

        {/* 프로필 트리거 + 위로 뜨는 메뉴 */}
        <div className="relative">
          {menuOpen && (
            <div
              className="popover absolute z-50 p-1"
              style={{ left: 12, right: 12, bottom: 'calc(100% + 6px)' }}
            >
              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
                <div className="t-sm" style={{ fontWeight: 600, color: 'var(--fg-1)' }}>{session?.name}</div>
                <div className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  {session?.email} · {session?.role}
                </div>
              </div>
              <div
                className="menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  setProfileOpen(true);
                  setProfileDraft(session?.name ?? '');
                  setProfileError(false);
                }}
              >
                <Icon name="settings" size={14} />
                프로필 설정
              </div>
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
          )}
          <div
            className="flex cursor-pointer items-center gap-2"
            style={{ padding: '12px 16px', borderTop: '1px solid rgba(255,255,255,.08)' }}
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
          >
            <div
              className="flex flex-none items-center justify-center rounded-full"
              style={{ width: 24, height: 24, background: 'var(--gray-700)', color: 'var(--gray-300)', fontSize: 10, fontWeight: 600 }}
            >
              {session?.initials ?? ''}
            </div>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate" style={{ color: 'var(--gray-300)', fontSize: 11, fontWeight: 500 }}>
                {session?.name ?? ''}
              </span>
              <span className="truncate" style={{ color: 'var(--gray-600)', fontSize: 10 }}>
                {session?.email ?? ''}
              </span>
            </div>
            <Icon name="chevronsUpDown" size={12} className="flex-none text-(--gray-600)" />
          </div>
        </div>
      </nav>

      {/* ============ 메인 ============ */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex items-center gap-3 px-6"
          style={{
            height: 'var(--header-h)', flex: 'none',
            background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)',
          }}
        >
          {showBack && (
            <button
              className="btn btn-ghost btn-icon"
              aria-label="뒤로"
              onClick={() => navigate(tenantId ? '/tenants' : '/analyses')}
            >
              <Icon name="arrowLeft" className="ic" />
            </button>
          )}
          <div className="flex min-w-0 items-baseline gap-2.5">
            <span className="t-h3 whitespace-nowrap">{pageTitle}</span>
            <span className="t-sm truncate" style={{ color: 'var(--fg-3)' }}>{pageSub}</span>
          </div>
          <div className="flex-1" />
        </header>

        {/* pathname key로 리마운트 — 라우트 이동 시 스크롤 위치가 이전 화면 것을 물려받지 않게 */}
        <main key={path} className="flex-1 overflow-y-auto p-6">
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
            <button className="btn" onClick={() => setProfileOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={saveProfile}>
              저장
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3.5 p-5">
          <div className="flex items-center gap-3 pb-0.5">
            <div
              className="flex flex-none items-center justify-center rounded-full"
              style={{ width: 40, height: 40, background: 'var(--gray-700)', color: 'var(--gray-300)', fontSize: 14, fontWeight: 600 }}
            >
              {session?.initials}
            </div>
            <div className="flex flex-col">
              <span className="t-sm" style={{ fontWeight: 600 }}>{session?.name}</span>
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{session?.role} · EDGE 플랫폼 운영</span>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="t-label">표시 이름</span>
            <label className="field w-full box-border">
              <input
                value={profileDraft}
                onChange={(e) => {
                  setProfileDraft(e.target.value);
                  setProfileError(false);
                }}
              />
            </label>
            {profileError && <span className="t-xs" style={{ color: 'var(--down)' }}>표시 이름을 입력하세요.</span>}
          </div>
          <div className="flex flex-col gap-1">
            <span className="t-label">이메일</span>
            <div
              className="box-border w-full"
              style={{
                padding: '8px 10px', fontSize: 12, color: 'var(--fg-3)',
                background: 'var(--bg-sunken)', border: '1px solid var(--border)',
                borderRadius: 5, cursor: 'not-allowed',
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
