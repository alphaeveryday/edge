/* 헤더의 화면명·뒤로가기를 **경로 하나가** 정한다 (ALPHA-738 조각 3).
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

/**
 * 헤더가 알아야 하는 경로의 정체.
 *
 * `entity` 가 있는 갈래는 **대상 하나를 열고 있는 화면**이고, 그게 곧 뒤로가기가 필요한
 * 조건이다 — `showBack` 을 갈래 이름의 OR 목록으로 손수 유지하면 갈래를 더할 때 반드시
 * 한쪽이 빠진다(이 파일이 생기기 전 상태가 그랬다).
 */
export type HeaderRoute =
  | { kind: 'tenantDetail'; title: string; entity: { id: string } }
  | { kind: 'symbol'; title: string; entity: { market: string; code: string } }
  | { kind: 'analysisDetail'; title: string; entity: { id: string } }
  | { kind: 'list'; title: string }
  | { kind: 'home'; title: string }
  | { kind: 'unknown'; title: string };

/**
 * 🔴 **순서가 계약이다.** `/analyses/symbol/KR/069500` 은 아래 셋 모두에 걸릴 수 있는 모양이다:
 * 종목 정규식 · 분석 상세 정규식(조각 수가 달라 실제론 안 걸린다) · `startsWith('/analyses')`.
 * 구체적인 것을 먼저 묻지 않으면 종목 화면이 헤더에서 **목록으로 위장**하고 뒤로가기를 잃는다.
 */
export function headerRoute(path: string): HeaderRoute {
  const tenant = /^\/tenants\/([^/]+)$/.exec(path);
  if (tenant) return { kind: 'tenantDetail', title: '테넌트 상세', entity: { id: tenant[1] } };

  const symbol = /^\/analyses\/symbol\/([^/]+)\/([^/]+)$/.exec(path);
  if (symbol) {
    return {
      kind: 'symbol',
      title: '종목 분석 이력',
      entity: { market: symbol[1], code: symbol[2] },
    };
  }

  const analysis = /^\/analyses\/([^/]+)$/.exec(path);
  if (analysis) {
    return { kind: 'analysisDetail', title: '가격 변동 분석 상세', entity: { id: analysis[1] } };
  }

  const listed = LIST_TITLES.find(([prefix]) => path.startsWith(prefix));
  if (listed) return { kind: 'list', title: listed[1] };

  if (path === '/') return { kind: 'home', title: '오늘 운영 현황' };
  return { kind: 'unknown', title: '' };
}

/** 대상 하나를 열고 있으면 돌아갈 곳이 있다 — 갈래 목록이 아니라 `entity` 유무가 판별한다. */
export const showBack = (route: HeaderRoute): boolean => 'entity' in route;

/**
 * 뒤로가기가 가는 목록. `showBack` 이 참인 갈래마다 **반드시 하나씩** 있어야 한다 —
 * 여기 빠진 갈래는 버튼은 뜨는데 엉뚱한 목록으로 보낸다(안 걸린 갈래가 조용히 기본값을 탄다).
 * 종목 상세는 분석 목록으로 간다: 그 화면 자신의 breadcrumb 과 같은 곳이어야 한다.
 */
export function backTo(route: HeaderRoute): string | null {
  switch (route.kind) {
    case 'tenantDetail':
      return '/tenants';
    case 'analysisDetail':
    case 'symbol':
      return '/analyses';
    default:
      return null;
  }
}
