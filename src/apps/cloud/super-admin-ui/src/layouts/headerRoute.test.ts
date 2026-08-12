/* 실행: node --test src/layouts/headerRoute.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { HEADER_LIST_TITLES, backTo, headerRoute, showBack } from './headerRoute.ts';

test('종목 상세가 목록 접두어에 먹히지 않는다 (순서를 뒤집으면 헤더가 목록으로 위장한다)', () => {
  const r = headerRoute('/analyses/symbol/KR/069500');
  assert.equal(r.kind, 'symbol');
  assert.equal(r.title, '종목 분석 이력');
  assert.deepEqual(r.kind === 'symbol' ? r.entity : null, { market: 'KR', code: '069500' });
});

test('분석 상세와 종목 상세는 같은 접두어의 **다른** 화면이다', () => {
  assert.equal(headerRoute('/analyses/an-123').kind, 'analysisDetail');
  assert.equal(headerRoute('/analyses/symbol/KR/069500').kind, 'symbol');
  /* 목록은 조각이 하나뿐일 때만이다 — `/analyses` 자신이 상세로 잡히면 목록 화면이 사라진다 */
  assert.equal(headerRoute('/analyses').kind, 'list');
});

test('시장·코드가 경로에서 그대로 나온다 (자리를 맞바꾸면 헤더가 다른 종목을 말한다)', () => {
  const r = headerRoute('/analyses/symbol/US/AAPL');
  assert.deepEqual(r.kind === 'symbol' ? r.entity : null, { market: 'US', code: 'AAPL' });
});

test('대상을 연 화면은 전부 뒤로가기를 준다 — 갈래가 늘어도 목록을 손으로 안 고친다', () => {
  const opened = ['/tenants/t-1', '/analyses/an-1', '/analyses/symbol/KR/069500'];
  for (const p of opened) {
    const r = headerRoute(p);
    assert.ok('entity' in r, `${p} 는 대상 하나를 열고 있다`);
    assert.ok(showBack(r), `${p} 에 뒤로가기가 있어야 한다`);
  }
  /* 반대 방향도 재야 한다 — `showBack` 을 상수 true 로 바꿔도 위만으로는 안 잡힌다 */
  for (const p of ['/', '/tenants', '/analyses', '/sources', '/없는경로']) {
    assert.equal(showBack(headerRoute(p)), false, `${p} 에는 돌아갈 대상이 없다`);
  }
});

test('화면명 표가 사이드바 메뉴와 **양방향**으로 맞는다', () => {
  /* ⚠️ 기대값을 `HEADER_LIST_TITLES` 에서 뽑아 순회하면 **행을 지워도 안 걸린다** — 사라진
   * 행은 순회에서 함께 사라져 검사가 조용히 줄어든다(변이로 실증했다). 그래서 기대 목록을
   * 여기 **따로 적고** 두 방향을 다 잰다: 표에서 빠지면 헤더가 빈칸이 되고, 표에만 있으면
   * 아무 화면도 안 쓰는 이름이 남는다. 화면을 더하는 것은 파생이 아니라 결정이라, 두 곳을
   * 함께 고치는 것이 맞다. */
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
      if (a !== b) assert.ok(!b.startsWith(a), `${b} 가 ${a} 에 가려진다`);
    }
  }
});

test('뒤로가기 버튼이 뜨는 갈래는 전부 갈 곳이 있다 (없으면 기본값으로 새어 엉뚱한 목록에 떨어진다)', () => {
  for (const p of ['/tenants/t-1', '/analyses/an-1', '/analyses/symbol/KR/069500']) {
    const r = headerRoute(p);
    assert.equal(showBack(r), true);
    assert.ok(backTo(r), `${p} 의 돌아갈 목록`);
  }
  /* 반대 방향 — 버튼이 없는 자리에 목적지가 생기면 두 판정이 갈린 것이다 */
  for (const p of ['/', '/analyses', '/sources', '/없는경로']) {
    const r = headerRoute(p);
    assert.equal(showBack(r), false);
    assert.equal(backTo(r), null, `${p} 에는 돌아갈 목록이 없어야 한다`);
  }
});

test('종목 상세의 뒤로가기와 그 화면의 breadcrumb 이 같은 곳을 가리킨다', () => {
  /* `AnalysisSymbolPage` 의 breadcrumb 첫 항이 `/analyses` 다. 둘이 갈리면 같은 화면의
   * 두 "돌아가기"가 다른 데로 간다 — 어느 쪽이 맞는지 운영자가 알 방법이 없다. */
  assert.equal(backTo(headerRoute('/analyses/symbol/KR/069500')), '/analyses');
});

test('모르는 경로는 이름을 지어내지 않는다', () => {
  const r = headerRoute('/전혀-없는-경로');
  assert.equal(r.kind, 'unknown');
  assert.equal(r.title, '');
});
