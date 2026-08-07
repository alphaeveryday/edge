import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary, Icon, Modal, Toaster, toast } from 'ui-kit';
import type { IconName } from 'ui-kit';
import { useAnalyses } from '../domains/analyses/hooks';
import { useLogout, useSession, useUpdateDisplayName } from '../domains/session/hooks';
import { useTenants } from '../domains/tenants/hooks';

/* 좌측 정보 구조 — 그룹(파이프라인·분석 결과) ▸ 영역 ▸ 하위 화면 3단.
 *
 * 영역은 그 질문의 기본 화면을 열고, 하위 화면은 그 영역에 들어와 있을 때만 펼쳐진다.
 * 라우트는 하나도 바꾸지 않는다 — 기존 URL·딥링크가 그대로 살아 있어야 한다(메뉴만 재배치).
 */
interface NavSub {
  path: string;
  label: string;
}
interface NavArea {
  path: string;
  label: string;
  icon: IconName;
  subs?: NavSub[];
}
interface NavGroup {
  group: string;
  areas: NavArea[];
}

/**
 * 정보 구조 시험판(ALPHA-738) — `운영` 그룹을 없애고 개요·문제를 파이프라인 아래로 넣었다.
 *
 * ⚠️ **메뉴에서 뺀 화면의 라우트·컴포넌트는 지우지 않았다**(App.tsx 그대로). 나중에 제거
 * 여부를 결정할 수 있게 되돌리기 쉬운 상태로 둔다 — 지금은 진입 경로만 정리한다.
 *   `/ops/chain`   설명 생성 흐름 — 분석 결과 상세·문제에서 실행으로 가는 길이 우선이다
 *   `/ops/delivery` 전달 경계 — 이 콘솔의 담당 범위 밖(ADR-0026)
 *   `/lineage/news` 뉴스 계보 — 최상위가 아니라 분석 결과의 근거, 데이터의 퍼널로 들어간다
 *   `/overview`·`/sources` 원장 — 조사 마지막 근거 화면(앞선 결정)
 */
const NAV_GROUPS: NavGroup[] = [
  {
    group: '파이프라인',
    areas: [
      { path: '/ops/incidents', label: '문제', icon: 'fileText' },
      {
        path: '/ops/runs',
        label: '실행',
        icon: 'clipboardCheck',
        subs: [
          { path: '/grid', label: '실행 이력' },
          { path: '/minute', label: '현재 실행' },
        ],
      },
      { path: '/ops/trend', label: '추이', icon: 'trendChart' },
    ],
  },
  {
    group: '분석 결과',
    areas: [{ path: '/analyses', label: '가격 변동 분석 목록', icon: 'trendChart' }],
  },
  {
    group: '테넌트 관리',
    areas: [{ path: '/tenants', label: '테넌트 목록', icon: 'building' }],
  },
];

/* 메뉴 항목은 실제 링크(a)다 — 주소가 드러나고 새 탭 열기·가운데 클릭이 그대로 동작한다.
 * 전역 `a:hover { text-decoration: underline }` 만 눌러 두고 색·배경은 .nav-item 이 준다. */
const NAV_LINK: CSSProperties = { textDecoration: 'none' };

/** '/' 는 startsWith 로 모든 경로에 붙는다 — 루트만 정확 일치로 가른다 */
const matchPath = (path: string, target: string) =>
  target === '/' ? path === '/' : path.startsWith(target);
/** 이 영역 안에 있는가 — 기본 화면이든 하위 화면이든 */
const inArea = (path: string, a: NavArea) =>
  matchPath(path, a.path) || (a.subs ?? []).some((s) => matchPath(path, s.path));

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
    pageTitle = '원장 근거';
  } else if (path.startsWith('/grid')) {
    pageTitle = '파이프라인 실행 이력';
  } else if (path.startsWith('/minute')) {
    /* 지금 가동 중인 것 전체를 묻는다 — 도는 배치 + 활성 수집 세션. URL 은 호환 유지 */
    pageTitle = '현재 실행';
  } else if (path.startsWith('/analyses')) {
    pageTitle = '가격 변동 분석 목록';
  } else if (path.startsWith('/lineage/news')) {
    pageTitle = '뉴스 계보';
  } else if (path.startsWith('/impact/holdings')) {
    pageTitle = '구성종목 결손 상세';
  } else if (/^\/ops\/incidents\/.+/.test(path)) {
    pageTitle = '사건 상세';
  } else if (path.startsWith('/ops/incidents')) {
    pageTitle = '파이프라인 문제';
  } else if (path.startsWith('/ops/runs')) {
    pageTitle = '런·작업 귀결';
  } else if (path.startsWith('/ops/chain')) {
    pageTitle = '설명 생성 흐름';
  } else if (path.startsWith('/ops/datasets')) {
    pageTitle = '데이터셋 신선도';
  } else if (path.startsWith('/ops/trend')) {
    /* 산출량만이 아니라 완전성·지연·결손까지 본다 — 내비의 '추이' 명칭은 그대로 */
    pageTitle = '산출·품질 추이';
  } else if (path.startsWith('/ops/delivery')) {
    pageTitle = 'Cloud 게시·발번 경계';
  } else if (path.startsWith('/overview')) {
    pageTitle = '레인 원장 요약';
  } else if (path.startsWith('/ops/summary')) {
    /* 메뉴에서 내린 옛 개요 — 라우트만 살려 둔다 */
    pageTitle = '파이프라인 개요';
  }

  /* 상세 화면은 돌아갈 목록이 정해져 있다 — 브라우저 뒤로가기에만 맡기면 링크로 바로 들어온
   * 사람에게는 돌아갈 곳이 없다. */
  const backTo = tenantId
    ? '/tenants'
    : analysisId
      ? '/analyses'
      : /^\/ops\/incidents\/.+/.test(path)
        ? '/ops/incidents'
        : /^\/ops\/runs\/.+/.test(path)
          ? '/ops/runs'
          : null;

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

        {NAV_GROUPS.map((g) => {
          return (
            <div key={g.group}>
              {/* 그룹 제목은 **비상호작용 heading** 이다 — 접기를 없앴으므로 button 도,
               * chevron 도, aria-expanded 도 두지 않는다(없는 조작을 약속하지 않는다).
               * 선택 여부와 무관하게 항상 선명하고, 현재 메뉴 강조는 .nav-item.active 소관이다. */}
              <h2 className="nav-section">{g.group}</h2>

              {g.areas.map((area) => {
                  const onOwnScreen = matchPath(path, area.path);
                  const inside = inArea(path, area);
                  return (
                    <div key={area.path}>
                      {/* 영역 자체가 현재 화면이면 활성, 하위 화면에 들어와 있으면 "이 영역 안"만 표시 */}
                      <Link
                        to={area.path}
                        className={`nav-item${onOwnScreen ? ' active' : ''}`}
                        style={{
                          ...NAV_LINK,
                          ...(inside && !onOwnScreen ? { color: 'var(--gray-200)' } : null),
                        }}
                        aria-current={onOwnScreen ? 'page' : undefined}
                      >
                        <Icon name={area.icon} className="ic" />
                        <span>{area.label}</span>
                      </Link>
                      {/* 하위 화면도 항상 보인다 — 접기를 없앴으므로 "그 영역에 들어와 있을 때만"
                       * 나타나는 조건도 두지 않는다(위계가 한눈에 보여야 한다). 길어지면
                       * 사이드바가 스크롤된다. */}
                      {area.subs?.map((sub) => {
                          const current = matchPath(path, sub.path);
                          return (
                            <Link
                              key={sub.path}
                              to={sub.path}
                              className={`nav-item nav-sub${current ? ' active' : ''}`}
                              style={{ ...NAV_LINK, paddingLeft: 32 }}
                              aria-current={current ? 'page' : undefined}
                            >
                              <span>{sub.label}</span>
                            </Link>
                          );
                        })}
                    </div>
                  );
              })}
            </div>
          );
        })}

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
          {backTo && (
            <button
              className="btn btn-ghost btn-icon"
              aria-label="뒤로"
              onClick={() => navigate(backTo)}
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
