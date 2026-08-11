/* 종목별 묶음과 최신 판정 (ALPHA-738).
 *
 * 지키는 의도:
 *   · **완료 시각으로 최신을 정하지 않는다** — 과거 기준의 늦은 완료가 최신 설명을 덮으면 안 된다.
 *   · **최신 시도가 실패해도 이전 유효 설명은 남는다** — 두 축을 따로 들고 다닌다.
 *   · 같은 종목의 장중 분석이 오늘 분석 수로 접힌다(목록이 평평해지지 않는다).
 *
 * 실행: node --test src/domains/analyses/symbols.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { byBasisDesc, groupBySymbol, hasResult, symbolKey } from './symbols.ts';
import type { Analysis } from './types.ts';

const a = (o: Partial<Analysis> & Pick<Analysis, 'id' | 'basisTimeAbs'>): Analysis => ({
  name: 'KODEX 반도체',
  code: '091160',
  market: 'KRX',
  direction: 1,
  changePct: 1,
  status: 'COMPLETED',
  basisTime: o.basisTimeAbs.slice(-5),
  doneTime: '—',
  confidence: null,
  publicationStatus: null,
  result: '설명 본문',
  evidence: [],
  ...o,
});

test('같은 종목의 분석이 한 행으로 접히고 오늘 분석 수가 된다', () => {
  const g = groupBySymbol([
    a({ id: '1', basisTimeAbs: '2026-08-03 10:22' }),
    a({ id: '2', basisTimeAbs: '2026-08-03 14:10' }),
    a({ id: '3', basisTimeAbs: '2026-08-03 15:30' }),
  ]);
  assert.equal(g.length, 1, '종목당 한 행');
  assert.equal(g[0].todayCount, 3);
  assert.equal(g[0].analyses.length, 3, '이력은 보존된다');
});

test('최신은 기준 시각으로 정한다 — 늦게 끝난 과거 기준이 덮지 않는다', () => {
  const g = groupBySymbol([
    /* 14:10 기준인데 16:00 에 완료 — 완료 시각으로 정렬하면 이게 최신이 된다 */
    a({ id: 'old', basisTimeAbs: '2026-08-03 14:10', doneTime: '16:00', result: '옛 기준 설명' }),
    a({ id: 'new', basisTimeAbs: '2026-08-03 15:30', doneTime: '15:41', result: '최신 기준 설명' }),
  ]);
  assert.equal(g[0].latestValid?.id, 'new');
  assert.equal(g[0].latestValid?.result, '최신 기준 설명');
});

test('같은 기준 시각이면 결정적 순서로 가른다 — 무작위가 아니다', () => {
  const list = [a({ id: 'r1', basisTimeAbs: 'T' }), a({ id: 'r2', basisTimeAbs: 'T' })];
  assert.equal(groupBySymbol(list)[0].latestAttempt.id, 'r2');
  assert.equal(groupBySymbol([...list].reverse())[0].latestAttempt.id, 'r2', '입력 순서와 무관');
});

test('최신 시도가 실패해도 이전 유효 설명이 남는다', () => {
  const g = groupBySymbol([
    a({ id: 'ok', basisTimeAbs: '2026-08-03 13:10', doneTime: '13:22', result: '유효 설명' }),
    a({ id: 'fail', basisTimeAbs: '2026-08-03 15:20', status: 'FAILED', result: '' }),
  ]);
  assert.equal(g[0].latestAttempt.id, 'fail');
  assert.equal(g[0].attemptPending, true, '최신 시도가 유효 결과가 아니다');
  assert.equal(g[0].latestValid?.id, 'ok', '이전 유효 설명을 지우지 않는다');
});

test('진행 중인 분석은 유효 결과로 세지 않는다', () => {
  assert.equal(hasResult(a({ id: 'p', basisTimeAbs: 'T', status: 'PENDING', result: '' })), false);
  assert.equal(hasResult(a({ id: 'f', basisTimeAbs: 'T', status: 'FAILED', result: '' })), false);
  /* 상태가 완료여도 본문이 없으면 읽을 설명이 없다 */
  assert.equal(hasResult(a({ id: 'e', basisTimeAbs: 'T', result: '  ' })), false);
  assert.equal(hasResult(a({ id: 'c', basisTimeAbs: 'T' })), true);
});

test('본문이 블록에만 있어도 유효 결과다 — 빈 text 블록은 본문이 아니다', () => {
  /* `result`=explanation_result.summary · `resultBlocks`=stage_results->final_explanation
   * ->blocks — 같은 행의 다른 컬럼이다. 상세 화면은 블록이 있으면 그걸 고객 산문으로
   * 그리므로, 블록만 보고 판정하는 경로가 실제로 있어야 한다.
   * ⚠️ 실 API 의 `result` 는 절대 비지 않는다(서버가 결측을 안내 문장으로 바꾼다) — 이
   * 케이스는 그 평탄화가 걷히거나 다른 소비자가 붙었을 때의 형상을 고정한다. */
  const blocksOnly = a({
    id: 'b',
    basisTimeAbs: '2026-08-03 15:30',
    result: '',
    resultBlocks: [
      { code: 'WHAT', title: '무슨 일이 있었나', text: '반도체가 올랐다', evidenceRefs: [] },
    ],
  });
  assert.equal(hasResult(blocksOnly), true, '블록이 있으면 읽을 설명이 있다');

  /* 빈 블록 배열은 본문이 아니다 — 있음/없음을 배열 존재로 접으면 안 된다 */
  assert.equal(hasResult(a({ id: 'z', basisTimeAbs: 'T', result: '', resultBlocks: [] })), false);
  /* 길이만 세면 text 가 빈 블록도 본문이 된다 — 서버 파서가 text 를 검증하지 않는다 */
  assert.equal(
    hasResult(
      a({
        id: 'w',
        basisTimeAbs: 'T',
        result: '',
        resultBlocks: [{ code: 'WHAT', title: '무슨 일이', text: '   ', evidenceRefs: [] }],
      }),
    ),
    false,
    '빈 text 블록은 읽을 설명이 아니다',
  );

  /* 그룹 층까지 실제로 닿는지 — 최신이 블록만 가진 완료면 그게 latestValid 고 대기가 아니다 */
  const g = groupBySymbol([
    a({ id: 'old', basisTimeAbs: '2026-08-03 10:00', result: '옛 설명' }),
    blocksOnly,
  ]);
  assert.equal(g[0].latestValid?.id, 'b', '블록만 있는 최신이 유효 설명이다');
  assert.equal(g[0].attemptPending, false, '유효한데 대기로 켜지면 안 된다');
});

test('유효 결과가 하나도 없으면 latestValid 는 null 이다 — 지어내지 않는다', () => {
  const g = groupBySymbol([a({ id: 'p', basisTimeAbs: 'T', status: 'PENDING', result: '' })]);
  assert.equal(g[0].latestValid, null);
  assert.equal(g[0].attemptPending, true);
});

test('종목 키는 시장과 코드를 함께 쓴다 — 다른 시장의 같은 코드가 섞이지 않는다', () => {
  const g = groupBySymbol([
    a({ id: 'k', basisTimeAbs: 'T', market: 'KRX', code: 'X', name: 'K' }),
    a({ id: 'n', basisTimeAbs: 'T', market: 'NASDAQ', code: 'X', name: 'N' }),
  ]);
  assert.equal(g.length, 2);
  assert.deepEqual(g.map((x) => x.key).sort(), ['KRX:X', 'NASDAQ:X']);
  assert.equal(symbolKey({ market: 'KRX', code: 'X' }), 'KRX:X');
});

test('정렬은 기준 시각 내림차순이다', () => {
  const older = a({ id: '1', basisTimeAbs: '2026-08-03 10:00' });
  const newer = a({ id: '2', basisTimeAbs: '2026-08-03 15:00' });
  assert.ok(byBasisDesc(newer, older) < 0, '최신이 앞');
});
