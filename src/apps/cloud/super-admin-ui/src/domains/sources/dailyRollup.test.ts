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
import { datesOf, groupState, rollup, stateOf } from './dailyRollup.ts';
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

test('기대 실행 수는 원장의 DUE 셀에서 센다 — 주기에서 지어내지 않는다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-03T15:40', '2026-08-03', [
      cell({ taskKey: 'PRICE_COLLECTION_KIS' }),
      cell({ taskKey: 'NORMALIZE_PRICE' }),
      /* 계획에서 빠진 것은 기대에 넣지 않는다 */
      cell({ taskKey: 'LOAD_PRICE_DAILY', planStatus: 'SKIPPED', outcome: null, dataStatus: null }),
    ]),
  ]).get('price_daily|2026-08-03')!;
  assert.equal(r.expected, 2);
  assert.equal(r.counts.skipped, 1);
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
  assert.equal(r.runs.length, 2);
  assert.deepEqual([...new Set(r.runs.map((x) => x.runKey))].sort(), [
    'news:2026-08-03T15:00',
    'news:2026-08-03T15:30',
  ]);
  assert.equal(r.state, '장애');
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

test('그룹 행은 하위 상태를 결정적으로 집계한다', () => {
  assert.equal(groupState(['정상', '주의', '장애']), '장애');
  assert.equal(groupState(['정상', '주의', '실행 중']), '주의');
  assert.equal(groupState(['정상', '실행 중']), '실행 중');
  assert.equal(groupState(['정상', '계획 스킵']), '정상');
  assert.equal(groupState(['계획 없음', '계획 스킵']), '계획 스킵');
  assert.equal(groupState([]), '계획 없음');
});

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
