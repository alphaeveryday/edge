/* 헤더의 화면명·뒤로가기를 **경로 하나가** 정한다 (ALPHA-738).
 *
 * ⚠️ **JSX 를 쓰지 않는다.** 이 판정이 `AdminLayout.tsx` 안에 있는 동안은
 * `node --test 'src/**\/*.test.ts'` 가 파일을 아예 안 집어, 경로 우선순위를 뒤바꾸거나
 * 화면명을 지워도 **아무 테스트도 안 깨졌다**. 이 트랙에서 같은 이유로 `notRun`·`minuteFacts`
 * 를 순수 모듈로 내렸다 — 여기도 같은 자리다. 엔티티 이름 조회(테넌트·분석 목록)는 응답이
 * 필요하니 화면에 남기고, **경로가 혼자 답할 수 있는 것만** 여기서 답한다.
 */

/** 화면명이 붙는 경로 접두어. 값이 아니라 **표**라, 화면을 더할 때 여기 한 줄이 정본이다.
 *
 *  ⭐ 표 **안의** 순서는 계약이 아니다 — 어느 접두어도 다른 접두어의 앞부분이 아니어서
 *  어떤 경로든 걸리는 행이 최대 하나다(그 겹침 금지를 테스트가 잰다). 그래서 순서를 뒤집는
 *  변이는 동치이고, 억지로 죽이지 않았다. 겹침이 생기는 순간 `find` 의 순서가 계약이 된다. */
const LIST_TITLES: readonly (readonly [prefix: string, title: string])[] = [
  ['/tenants', '테넌트 목록'],
  ['/sources', '데이터 소스 수집 상태'],
  ['/grid', '파이프라인 실행 이력'],
  ['/minute', '장중 1분 수집'],
  ['/lineage/news', '뉴스 계보'],
  ['/impact/holdings', '구성종목 결손 영향'],
  ['/analyses', '가격 변동 분석 목록'],
] as const;

export const HEADER_LIST_TITLES = LIST_TITLES;

export type HeaderKind =
  | 'tenantDetail'
  | 'symbol'
  | 'analysisDetail'
  | 'list'
  | 'home'
  | 'unknown';

/**
 * 헤더가 알아야 하는 경로의 정체.
 *
 * ⭐ **`backTo` 를 라우트가 직접 들고 다닌다.** "버튼을 띄울까"와 "어디로 보낼까"를 따로 두면
 * 갈래를 더할 때 한쪽만 고쳐지고, 그러면 버튼은 뜨는데 엉뚱한 목록으로 보낸다(안 걸린 갈래가
 * 조용히 기본값을 탄다). 여기 한 자리로 묶으면 **갈릴 자리 자체가 없다** — 그게 이 트랙이
 * 반복해서 배운 "손으로 유지되는 표기는 낡는다 → 집합으로 묶어라" 의 형태다.
 */
export interface HeaderRoute {
  kind: HeaderKind;
  title: string;
  /** 돌아갈 목록. `null` 이면 뒤로가기 버튼이 없다. */
  backTo: string | null;
  /** 이름을 응답에서 찾아야 하는 상세 화면만 갖는다(경로가 못 답하는 부분). */
  entity?: { id: string };
}

/**
 * 접두어가 **경로 조각 경계에서** 끝나는가. `startsWith` 만 쓰면 `/analyses-old` 같은 오타
 * 주소가 "가격 변동 분석 목록"으로 분류돼, 그런 화면이 없는데 헤더가 있는 화면 이름을 말한다.
 * `/analyses` 자신과 `/analyses/…` 만 받는다.
 */
const underPrefix = (path: string, prefix: string): boolean =>
  path === prefix || path.startsWith(`${prefix}/`);

/**
 * 🔴 **순서가 계약이다.** `/analyses/symbol` 은 아래 둘 모두에 걸리는 모양이다: 종목 화면과
 * 분석 상세 정규식(`/analyses/<한 조각>`). 구체적인 것을 먼저 묻지 않으면 종목 화면이
 * **`symbol` 이라는 id 의 분석 상세**로 분류돼, 헤더가 "가격 변동 분석 상세"를 말한다.
 *
 * ⭐ 시장·코드는 **쿼리에 있어서** 여기서 안 읽는다(`analyses/symbols.symbolHref` — 점 든
 * 티커가 CDN SPA fallback 에서 죽지 않게). `useLocation().pathname` 에는 쿼리가 없으므로 이
 * 함수는 경로만으로 답할 수 있는 데까지만 답하고, 종목 이름은 화면이 붙인다.
 */
/**
 * 라우터가 같게 보는 주소를 이 파서도 같게 보게 만든다. 갈리면 **본문은 A 화면인데 헤더는 B
 * 화면 이름**을 말하고, 상세 화면에서는 뒤로가기까지 사라진다.
 *
 * 두 가지를 맞춘다:
 * 1. 끝의 `/` 는 조각이 아니라 표기다 — React Router 는 `/analyses/symbol/` 를 그 화면에 매칭한다.
 * 2. 퍼센트 인코딩을 푼다(`/analyses/%73ymbol` → `/analyses/symbol`). 라우터는 디코딩해 매칭한다.
 *
 * 🔴 **디코딩이 조각 경계를 만들면 그 결과를 버린다.** `%2F` 는 "조각 안의 슬래시"라 풀면
 * 없던 경계가 생기고, 이 파일의 경계 검사(`underPrefix`·`[^/]+`)가 통째로 우회된다 —
 * `/analyses/a%2Fb` 가 두 조각으로 보여 엉뚱한 갈래로 떨어진다. 조각 수가 그대로일 때만 쓴다.
 * 잘못된 인코딩(`%zz`)은 `decodeURIComponent` 가 던지므로 원본을 유지한다.
 */
function normalizePath(raw: string): string {
  const trimmed = raw.length > 1 ? raw.replace(/\/+$/, '') || '/' : raw;
  try {
    const decoded = decodeURIComponent(trimmed);
    return decoded.split('/').length === trimmed.split('/').length ? decoded : trimmed;
  } catch {
    return trimmed;
  }
}

export function headerRoute(raw: string): HeaderRoute {
  const path = normalizePath(raw);

  const tenant = /^\/tenants\/([^/]+)$/.exec(path);
  if (tenant) {
    return {
      kind: 'tenantDetail',
      title: '테넌트 상세',
      backTo: '/tenants',
      entity: { id: tenant[1] },
    };
  }

  /* 종목 상세는 분석 목록으로 돌아간다 — 그 화면 자신의 breadcrumb 첫 항과 같은 곳이어야
   * 한다(갈리면 한 화면의 두 "돌아가기"가 다른 데로 간다). */
  if (path === '/analyses/symbol') {
    return { kind: 'symbol', title: '종목 분석 이력', backTo: '/analyses' };
  }

  const analysis = /^\/analyses\/([^/]+)$/.exec(path);
  if (analysis) {
    return {
      kind: 'analysisDetail',
      title: '가격 변동 분석 상세',
      backTo: '/analyses',
      entity: { id: analysis[1] },
    };
  }

  const listed = LIST_TITLES.find(([prefix]) => underPrefix(path, prefix));
  if (listed) return { kind: 'list', title: listed[1], backTo: null };

  if (path === '/') return { kind: 'home', title: '오늘 운영 현황', backTo: null };
  return { kind: 'unknown', title: '', backTo: null };
}

/** 돌아갈 곳이 있으면 버튼이 있다 — 파생이라 두 판정이 갈릴 수 없다. */
export const showBack = (route: HeaderRoute): boolean => route.backTo !== null;
