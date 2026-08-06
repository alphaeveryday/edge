/* 일 단위 롤업 테스트 (ALPHA-738).
 *
 * 지키는 의도:
 *   · 빈 데이터(VALID_EMPTY)와 무증거(MISSED)는 끝까지 다른 사실이다 — 합쳐 실패로 만들지 않는다.
 *   · 기한 전 대기(PENDING)를 실패·누락으로 판정하지 않는다.
 *   · 기대 실행 수는 주기에서 지어내지 않고 원장의 DUE 셀에서 센다.
 *   · 같은 날짜의 여러 런이 하나의 박스로 접힌다.
 *
 * 실행: node --test src/domains/sources/dailyRollup.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { datesOf, rollup, stateOf } from './dailyRollup.ts';
import type { DayCounts } from './dailyRollup.ts';
import type { GridCell, GridSlot } from './types.ts';

const cell = (o: Partial<GridCell> & Pick<GridCell, 'taskKey'>): GridCell => ({
  stage: 'raw',
  planStatus: 'DUE',
  outcome: 'FULFILLED',
  dataStatus: 'UNKNOWN',
  recordsOut: 1,
  failedRecords: 0,
  skipReason: null,
  outcomeReason: null,
  running: false,
  ...o,
});

const slot = (runKey: string, tradingDate: string, tasks: GridCell[]): GridSlot => ({
  runKey,
  launchStatus: 'LAUNCHED',
  orchestrationStatus: 'SUCCEEDED',
  tradingDate,
  tasks,
});

const counts = (o: Partial<DayCounts> = {}): DayCounts => ({
  fulfilled: 0,
  emptyEvidence: 0,
  failed: 0,
  incomplete: 0,
  invalid: 0,
  noEvidence: 0,
  pending: 0,
  running: 0,
  skipped: 0,
  failedRecords: 0,
  ...o,
});

/* ── 핵심 계약 ── */

test('빈 데이터와 무증거는 다른 칸에 센다 — 합쳐 실패로 만들지 않는다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS', dataStatus: 'VALID_EMPTY' }),
      cell({ taskKey: 'NORMALIZE_PRICE', outcome: 'MISSED', dataStatus: null }),
    ]),
  ]).get('price_daily|2026-08-03')!;
  assert.equal(r.counts.emptyEvidence, 1);
  assert.equal(r.counts.noEvidence, 1);
  assert.equal(r.counts.failed, 0, '무증거를 failed 에 합치지 않는다');
  /* 무증거는 장애지만, 빈 데이터는 그 판정에 기여하지 않는다 */
  assert.equal(r.state, '장애');
});

test('빈 데이터만 있으면 정상이다 — 돌았고 데이터가 없었다는 증거다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS', dataStatus: 'VALID_EMPTY' }),
    ]),
  ]).get('price_daily|2026-08-03')!;
  assert.equal(r.state, '정상');
  assert.equal(r.counts.emptyEvidence, 1);
});

test('기한 전 대기는 실패·누락이 아니라 실행 중이다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS', outcome: 'PENDING', dataStatus: null }),
      cell({ taskKey: 'NORMALIZE_PRICE', outcome: 'PENDING', dataStatus: null, running: true }),
    ]),
  ]).get('price_daily|2026-08-03')!;
  assert.equal(r.counts.pending, 1);
  assert.equal(r.counts.running, 1);
  assert.equal(r.counts.failed + r.counts.noEvidence, 0);
  assert.equal(r.state, '실행 중');
});

test('기대 실행은 계획이 있던 실행 인스턴스 수다 — DUE 셀 수가 아니다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS' }),
      cell({ taskKey: 'NORMALIZE_PRICE' }),
      /* 계획에서 빠진 것은 기대에 넣지 않는다 */
      cell({ taskKey: 'LOAD_PRICE_DAILY', planStatus: 'SKIPPED', outcome: null, dataStatus: null }),
    ]),
  ]).get('price_daily|2026-08-03')!;
  /* 예전에는 DUE 셀마다 +1 해서 2가 나왔다 — 실행은 한 번인데 기대가 2로 부풀던 자리다 */
  assert.equal(r.expected, 1);
  assert.equal(r.executions.length, 1);
  assert.equal(r.counts.skipped, 1, '작업 축 카운트는 그대로 남는다');
});

test('계획(DUE)이 하나도 없는 실행은 기대 실행에 세지 않는다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-03T16:20', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS', planStatus: 'SKIPPED', outcome: null, dataStatus: null }),
    ]),
  ]).get('price_daily|2026-08-03')!;
  assert.equal(r.executions.length, 1, '관측된 실행은 있다');
  assert.equal(r.expected, 0, '계획된 실행은 아니다');
  assert.equal(r.state, '계획 스킵');
});

test('같은 날짜의 여러 런이 박스 하나로 접힌다', () => {
  const map = rollup([
    slot('news:2026-08-03T15:00', '2026-08-03', [cell({ taskKey: 'NEWS_COLLECTION_BIGKINDS' })]),
    slot('news:2026-08-03T15:30', '2026-08-03', [
      cell({ taskKey: 'LOAD_DOCUMENTS', outcome: 'FAILED', dataStatus: null }),
    ]),
  ]);
  assert.equal(map.size, 1, '데이터셋×날짜 하나');
  const r = map.get('stock_news|2026-08-03')!;
  /* 다른 런은 별도 실행으로 남는다 — 날짜로 합치지 않는다 */
  assert.equal(r.executions.length, 2);
  assert.deepEqual(r.executions.map((e) => e.runKey).sort(), [
    'news:2026-08-03T15:00',
    'news:2026-08-03T15:30',
  ]);
  assert.equal(r.state, '장애');
});

/* ── 실행 인스턴스 축 — 작업을 실행으로 세지 않는다 ── */

test('한 런의 작업 3개는 실행 1회다 — 기대 실행이 작업 수로 부풀지 않는다', () => {
  const map = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS', outcome: 'FAILED', dataStatus: null }),
      cell({ taskKey: 'NORMALIZE_PRICE', outcome: 'BLOCKED', dataStatus: null }),
      cell({ taskKey: 'LOAD_PRICE_DAILY', outcome: 'PENDING', dataStatus: null }),
    ]),
    /* 같은 날 재실행 — 작업 1개짜리 별도 런 */
    slot('etf-daily:2026-08-03T16:20', '2026-08-03', [cell({ taskKey: 'PRICE_COLLECTION_KIS' })]),
  ]);
  const r = map.get('price_daily|2026-08-03')!;
  assert.equal(r.executions.length, 2, '실행은 2회다(작업 4개가 아니다)');
  assert.equal(r.expected, 2, '기대 실행도 2 — DUE 셀 수(4)가 아니다');
  /* 작업 축은 따로 남는다 — 실행 축과 섞지 않는다 */
  assert.equal(r.counts.failed, 2); // FAILED + BLOCKED
  assert.equal(r.executions[0].tasks.length, 3, '15:40 실행을 펼치면 작업 3개');
  assert.equal(r.executions[1].tasks.length, 1);
});

test('실행마다 자기 상태를 갖는다 — 일별 장애가 어느 실행의 것인지 알 수 있다', () => {
  const map = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS', outcome: 'FAILED', dataStatus: null }),
    ]),
    slot('etf-daily:2026-08-03T16:20', '2026-08-03', [cell({ taskKey: 'PRICE_COLLECTION_KIS' })]),
  ]);
  const r = map.get('price_daily|2026-08-03')!;
  assert.equal(r.state, '장애', '하루는 최악 실행을 따른다');
  assert.deepEqual(
    r.executions.map((e) => [e.runKey.slice(-5), e.state]),
    [
      ['15:40', '장애'],
      ['16:20', '정상'],
    ],
  );
});

test('10회 실행 × 작업 3개가 30건으로 평탄화되지 않는다', () => {
  const slots = Array.from({ length: 10 }, (_, i) =>
    slot(`etf-daily:2026-08-03T${String(10 + i).padStart(2, '0')}:00`, '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS' }),
      cell({ taskKey: 'NORMALIZE_PRICE' }),
      cell({ taskKey: 'LOAD_PRICE_DAILY' }),
    ]),
  );
  const r = rollup(slots).get('price_daily|2026-08-03')!;
  assert.equal(r.executions.length, 10, '기본 목록은 실행 10건');
  assert.equal(r.expected, 10);
  assert.equal(
    r.executions.reduce((a, e) => a + e.tasks.length, 0),
    30,
    '작업 30개는 실행을 펼쳐야 나온다',
  );
});

test('카탈로그에 없는 작업은 어느 데이터셋에도 배정하지 않는다', () => {
  const map = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [cell({ taskKey: 'UNKNOWN_TASK_XYZ' })]),
  ]);
  assert.equal(map.size, 0);
});

/* ── 상태 판정 ── */

test('상태 우선순위 — 장애 > 주의 > 실행 중 > 정상', () => {
  assert.equal(stateOf(counts({ fulfilled: 1, failed: 1 }), 2), '장애');
  assert.equal(stateOf(counts({ fulfilled: 1, noEvidence: 1 }), 2), '장애');
  assert.equal(stateOf(counts({ fulfilled: 1, incomplete: 1 }), 2), '주의');
  assert.equal(stateOf(counts({ fulfilled: 1, failedRecords: 3 }), 2), '주의');
  assert.equal(stateOf(counts({ fulfilled: 1, running: 1 }), 2), '실행 중');
  assert.equal(stateOf(counts({ fulfilled: 2 }), 2), '정상');
});

test('셀이 없으면 계획 없음, 전부 스킵이면 계획 스킵', () => {
  assert.equal(stateOf(counts(), 0), '계획 없음');
  assert.equal(stateOf(counts({ skipped: 3 }), 3), '계획 스킵');
  /* 일부만 스킵이면 나머지로 판정한다 */
  assert.equal(stateOf(counts({ skipped: 1, fulfilled: 1 }), 2), '정상');
});

/* 그룹 롤업 테스트는 함수와 함께 제거했다 — 시장·뉴스·장중은 제어 단위가 아니라서
 * 그 층위의 상태는 원장에 근거가 없다(행은 데이터셋이 직접 선다). */

/* ── 날짜 축 ── */

test('날짜 축은 슬롯이 준 날짜만 쓴다 — 없는 날을 만들지 않는다', () => {
  const dates = datesOf([
    slot('news:2026-08-03T15:30', '2026-08-03', []),
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', []),
    slot('etf-daily:2026-07-31T15:40', '2026-07-31', []),
  ]);
  assert.deepEqual(dates, ['2026-07-31', '2026-08-03']);
});

test('tradingDate 가 없으면 run_key 에서 날짜를 읽는다(비거래일 런)', () => {
  const dates = datesOf([
    { runKey: 'etf-daily:2026-08-01T15:40', launchStatus: null, orchestrationStatus: null, tradingDate: null, tasks: [] },
  ]);
  assert.deepEqual(dates, ['2026-08-01']);
});
