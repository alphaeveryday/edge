import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary, Icon, Modal, Toaster, toast } from 'ui-kit';
import type { IconName } from 'ui-kit';
import { useAnalyses } from '../domains/analyses/hooks';
import { useLogout, useSession, useUpdateDisplayName } from '../domains/session/hooks';
import { useTenants } from '../domains/tenants/hooks';
import { headerRoute, showBack as routeShowsBack } from './headerRoute';
import { EdgeLogo } from '../pages/_shared/EdgeLogo';

interface NavEntry {
  path: string;
  label: string;
  icon: IconName;
}

const NAV_SECTIONS: { section: string; items: NavEntry[] }[] = [
  {
    section: '파이프라인',
    items: [
      { path: '/ops/incidents', label: '문제', icon: 'fileText' },
      { path: '/ops/runs', label: '실행', icon: 'clipboardCheck' },
      { path: '/grid', label: '실행 이력', icon: 'dashboard' },
      { path: '/minute', label: '현재 실행', icon: 'database' },
      { path: '/ops/trend', label: '추이', icon: 'trendChart' },
      /* 구 홈 병존(`rules/README.md` §6) — 규칙 엔진이 "무엇이 걸렸는가"를, 이쪽이 "레인이 지금
       * 어디까지 왔는가"를 답한다. ⚠️ 라우트만 남기고 이 줄을 빼면 **도달 경로가 0** 이 된다
       * (dev 에서는 `/` 가 이 화면이었다) — 병존한다고 적어 둔 문서가 그 순간 거짓이 된다.
       * 라벨은 도착지 제목과 같게 둔다(`headerRoute.ts`). */
      { path: '/overview', label: '레인 원장 요약', icon: 'database' },
    ],
  },
  {
    section: '테넌트 관리',
    items: [{ path: '/tenants', label: '테넌트 목록', icon: 'building' }],
  },
  {
    section: '분석 결과',
    items: [
      { path: '/analyses', label: '가격 변동 분석 목록', icon: 'trendChart' },
    ],
  },
];

export function AdminLayout() {
  const location = useLocation();
  const [search] = useSearchParams();
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
  /* 경로 판정은 `headerRoute.ts` 가 한다(순수 모듈이라 테스트가 붙는다 — 여기 두면 안 잡힌다).
   * 이름 조회만 여기 남는다: 응답이 있어야 답할 수 있어 경로 혼자로는 못 만든다. */
  const path = location.pathname;
  const route = headerRoute(path);
  const pageTitle = route.title;

  let pageSub = '';
  if (route.kind === 'tenantDetail') {
    pageSub = tenants?.find((t) => t.id === route.entity!.id)?.name ?? '';
  } else if (route.kind === 'analysisDetail') {
    const a = analyses?.find((x) => x.id === route.entity!.id);
    pageSub = a ? `${a.name} ${a.code}` : '';
  } else if (route.kind === 'symbol') {
    /* 종목은 시장·코드가 **쿼리**에 있다(점 든 티커가 CDN 에서 죽지 않게 — `symbolHref`).
     * `headerRoute` 는 경로만 보므로 여기서 읽는다. 이름은 목록 응답에서만 오니 못 찾으면
     * 시장·코드로 적는다 — 빈칸으로 두면 헤더가 어느 종목을 보는지 말하지 못한다. */
    const market = search.get('market') ?? '';
    const code = search.get('code') ?? '';
    const a = analyses?.find((x) => x.market === market && x.code === code);
    pageSub = a ? `${a.name} ${a.code}` : `${market} ${code}`.trim();
  }
  const showBack = routeShowsBack(route);

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
          <EdgeLogo height={16} />
          <span style={{ color: 'var(--gray-500)', fontSize: 10, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Super Admin
          </span>
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
              onClick={() => navigate(route.backTo ?? '/')}
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
