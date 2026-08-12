/* 실행: node --test src/layouts/headerRoute.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { HEADER_LIST_TITLES, headerRoute, showBack } from './headerRoute.ts';

test('종목 상세가 분석 상세 정규식에 먹히지 않는다 (순서를 뒤집으면 헤더가 남의 이름을 말한다)', () => {
  /* `/analyses/symbol` 은 `/analyses/<한 조각>` 이기도 하다. 종목을 먼저 안 물으면
   * "`symbol` 이라는 id 의 분석 상세"로 분류돼, 헤더가 "가격 변동 분석 상세"를 쓰고
   * 이름 조회도 없는 id 로 돌아 빈 칸이 된다. */
  const r = headerRoute('/analyses/symbol');
  assert.equal(r.kind, 'symbol');
  assert.equal(r.title, '종목 분석 이력');
  assert.equal(r.entity, undefined, '시장·코드는 쿼리에 있다 — 경로 판정이 지어내면 안 된다');
});

test('세 갈래가 같은 접두어를 나눠 쓴다', () => {
  assert.equal(headerRoute('/analyses').kind, 'list');
  assert.equal(headerRoute('/analyses/symbol').kind, 'symbol');
  assert.equal(headerRoute('/analyses/an-123').kind, 'analysisDetail');
  assert.equal(headerRoute('/analyses/an-123').entity?.id, 'an-123');
});

test('접두어는 경로 조각 경계에서만 맞는다 — 없는 화면에 이름을 주지 않는다', () => {
  /* `startsWith` 만 쓰면 `/analyses-old` 가 "가격 변동 분석 목록"이 된다. 라우트가 없어
   * 실제로는 홈으로 리다이렉트되는데 헤더만 있는 화면 이름을 말하는 상태다. */
  for (const p of ['/analyses-old', '/tenantsX', '/gridiron', '/minuteman']) {
    const r = headerRoute(p);
    assert.equal(r.kind, 'unknown', `${p} 는 아는 화면이 아니다`);
    assert.equal(r.title, '');
  }
  /* 반대 방향 — 경계 검사가 정상 경로까지 막으면 전 화면의 헤더가 빈다 */
  assert.equal(headerRoute('/tenants').kind, 'list');
  assert.equal(headerRoute('/tenants/t-1').kind, 'tenantDetail');
  assert.equal(headerRoute('/lineage/news').kind, 'list');
});

test('뒤로가기는 목적지 하나에서 파생된다 — 버튼과 목적지가 갈릴 수 없다', () => {
  const opened: [string, string][] = [
    ['/tenants/t-1', '/tenants'],
    ['/analyses/an-1', '/analyses'],
    ['/analyses/symbol', '/analyses'],
  ];
  for (const [p, dest] of opened) {
    const r = headerRoute(p);
    assert.equal(r.backTo, dest, `${p} 의 돌아갈 목록`);
    assert.equal(showBack(r), true, `${p} 에 뒤로가기가 있어야 한다`);
  }
  /* 반대 방향도 재야 한다 — `showBack` 을 상수 true 로 바꿔도 위만으로는 안 잡힌다 */
  for (const p of ['/', '/tenants', '/analyses', '/sources', '/없는경로']) {
    const r = headerRoute(p);
    assert.equal(r.backTo, null, `${p} 에는 돌아갈 목록이 없어야 한다`);
    assert.equal(showBack(r), false);
  }
});

test('종목 상세의 뒤로가기와 그 화면의 breadcrumb 이 같은 곳을 가리킨다', () => {
  /* `AnalysisSymbolPage` 의 breadcrumb 첫 항이 `/analyses` 다. 둘이 갈리면 같은 화면의
   * 두 "돌아가기"가 다른 데로 간다 — 어느 쪽이 맞는지 운영자가 알 방법이 없다. */
  assert.equal(headerRoute('/analyses/symbol').backTo, '/analyses');
});

test('화면명 표가 기대 목록과 **양방향**으로 맞는다', () => {
  /* ⚠️ 기대값을 `HEADER_LIST_TITLES` 에서 뽑아 순회하면 **행을 지워도 안 걸린다** — 사라진
   * 행은 순회에서 함께 사라져 검사가 조용히 줄어든다(변이로 실증했다). 그래서 기대 목록을
   * 여기 **따로 적고** 두 방향을 다 잰다: 표에서 빠지면 헤더가 빈칸이 되고, 표에만 있으면
   * 아무 화면도 안 쓰는 이름이 남는다. 화면을 더하는 것은 파생이 아니라 결정이라, 두 곳을
   * 함께 고치는 것이 맞다.
   *
   * ⚠️ 이 표는 `AdminLayout` 의 사이드바(`NAV_SECTIONS`)와 **자동으로 묶여 있지 않다** —
   * 그건 `.tsx` 라 `node --test` 가 import 하지 못한다. 그래서 "사이드바와 일치"를 여기서
   * 단언할 수 없고, 하지 않는다(할 수 없는 것을 이름으로 주장하지 않는다). */
  const EXPECTED: Record<string, string> = {
    '/tenants': '테넌트 목록',
    '/sources': '데이터 소스 수집 상태',
    '/grid': '파이프라인 실행 이력',
    '/minute': '장중 1분 수집',
    '/lineage/news': '뉴스 계보',
    '/impact/holdings': '구성종목 결손 영향',
    '/analyses': '가격 변동 분석 목록',
  };
  assert.deepEqual(
    Object.fromEntries(HEADER_LIST_TITLES.map(([p, t]) => [p, t])),
    EXPECTED,
    '화면명 표가 기대 목록과 갈렸다',
  );
  for (const [prefix, title] of Object.entries(EXPECTED)) {
    assert.equal(headerRoute(prefix).title, title, `${prefix} 의 화면명`);
  }
  assert.equal(headerRoute('/').title, '오늘 운영 현황');
});

test('표의 접두어끼리 서로를 가리지 않는다 — 가리면 뒤엣것이 영영 안 뽑힌다', () => {
  /* `find` 는 첫 매칭에서 멈춘다. 새 화면을 더하다 `/analyses` 뒤에 `/analyses/x` 를 두면
   * 조용히 앞엣것이 이긴다 — 순서로 지키는 대신 **겹침 자체를 금지**한다. */
  for (const [a] of HEADER_LIST_TITLES) {
    for (const [b] of HEADER_LIST_TITLES) {
      if (a !== b) assert.ok(!b.startsWith(`${a}/`) && b !== a, `${b} 가 ${a} 에 가려진다`);
    }
  }
});

test('모르는 경로는 이름을 지어내지 않는다', () => {
  const r = headerRoute('/전혀-없는-경로');
  assert.equal(r.kind, 'unknown');
  assert.equal(r.title, '');
});
