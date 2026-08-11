/* ETF 구성종목 최종 완전성 판정 (ALPHA-738).
 *
 * 지키는 의도:
 *   · 결손 상세는 **조건부** 진입이다 — 결손이 없으면 액션이 없고, 적재 중이면 확정하지 않는다.
 *   · 분모가 없으면 "결손 없음"이 아니라 **계산 불가**다(관대해지는 쪽으로 틀리지 않는다).
 *   · 어느 단계에서 탈락했는지 고르지 않는다.
 *
 * 실행: node --test src/domains/sources/holdingsFlow.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { holdingsFlow } from './holdingsFlow.ts';
import type { TaskStatus } from './types.ts';

const task = (o: Partial<TaskStatus> & Pick<TaskStatus, 'taskKey'>): TaskStatus => ({
  stage: 'raw',
  dataset: 'etf_holdings',
  planStatus: 'DUE',
  outcome: 'FULFILLED',
  dataStatus: 'VALID',
  executionStatus: 'SUCCEEDED',
  recordsOut: 33,
  failedRecords: 0,
  completeness: null,
  lastFinishedAt: null,
  expectedAt: null,
  deadlineAt: null,
  missedAt: null,
  fulfilledAt: null,
  skipReason: null,
  outcomeReason: null,
  attempts: [],
  ...o,
});

const collect = (missing: number | null) =>
  task({
    taskKey: 'ETF_HOLDINGS_COLLECTION_KRX',
    completeness: missing === null ? null : { expected: 33, received: 33 - missing, missing },
  });
const load = (o: Partial<TaskStatus> = {}) =>
  task({ taskKey: 'LOAD_ETF_HOLDINGS', stage: 'feature', ...o });

test('결손이 있으면 상세 진입 상태다 — 단계 원인은 고르지 않는다', () => {
  const f = holdingsFlow([collect(4), load()]);
  assert.equal(f.state, 'missing');
  assert.equal(f.completeness?.missing, 4);
  assert.match(f.basis, /수집 완전성 누락 4/);
  assert.match(f.basis, /어느 단계에서 탈락했는지는 단정하지 않는다/);
});

test('적재 결함만 있어도 결손이다 — 수집 완전성만 보지 않는다', () => {
  const byStatus = holdingsFlow([collect(0), load({ dataStatus: 'INCOMPLETE' })]);
  assert.equal(byStatus.state, 'missing');
  const byRecords = holdingsFlow([collect(0), load({ failedRecords: 2 })]);
  assert.equal(byRecords.state, 'missing');
});

test('적재가 안 끝났으면 확정 결손으로 만들지 않는다', () => {
  for (const outcome of ['PENDING', null] as const) {
    const f = holdingsFlow([collect(4), load({ outcome, dataStatus: null })]);
    assert.equal(f.state, 'pending', String(outcome));
    assert.match(f.basis, /확정할 수 없다/);
  }
});

test('분모가 없으면 결손 없음이 아니라 계산 불가다', () => {
  const noCompleteness = holdingsFlow([collect(null), load()]);
  assert.equal(noCompleteness.state, 'unknown');
  assert.match(noCompleteness.basis, /결손 없음이 아니다/);

  const noExpected = holdingsFlow([
    task({ taskKey: 'ETF_HOLDINGS_COLLECTION_KRX', completeness: { expected: null, received: null, missing: null } }),
    load(),
  ]);
  assert.equal(noExpected.state, 'unknown');
});

test('결손이 없으면 진입 액션을 만들지 않는다', () => {
  const f = holdingsFlow([collect(0), load()]);
  assert.equal(f.state, 'none');
});

test('적재가 실패로 끝났으면 수집이 다 왔어도 "모두 적재됐다"가 아니다', () => {
  /* 실패한 적재는 얼마나 틀렸는지를 아예 안 남길 수 있다 — `dataStatus`·`failedRecords` 만
   * 보는 결손 판정은 그때 통과하고, 남는 근거가 수집 완전성뿐이라 결손 없음이 선다.
   * 이 조합(수집 완전 + 적재 FAILED + 결함 수치 없음)이 그 구멍의 정확한 형상이다. */
  const f = holdingsFlow([
    collect(0),
    load({ outcome: 'FAILED', dataStatus: null, failedRecords: null, executionStatus: 'FAILED' }),
  ]);
  assert.equal(f.state, 'unknown', '결손 없음으로 접히면 안 된다');
  assert.match(f.basis, /FAILED/, '무엇 때문에 판정 못 하는지 근거에 남는다');
  /* 결손이라 단정하지도 않는다 — 수집은 다 왔고 어디서 탈락했는지 모른다 */
  assert.notEqual(f.state, 'missing');
});

test('적재 작업이 아예 없으면 적재 여부를 모르는 것이지 완료가 아니다', () => {
  const f = holdingsFlow([collect(0)]);
  assert.equal(f.state, 'unknown');
  assert.match(f.basis, /없다/);
  /* 블록은 선다 — 수집 작업이 있으니 데이터셋 문맥 자체는 존재한다 */
  assert.equal(f.steps.length, 1);
});

test('MISSED·BLOCKED 적재도 성공이 아니다 — FULFILLED 만 결손 없음의 자격이 있다', () => {
  for (const outcome of ['MISSED', 'BLOCKED', 'PENDING'] as const) {
    const f = holdingsFlow([collect(0), load({ outcome, dataStatus: null, failedRecords: null })]);
    assert.notEqual(f.state, 'none', `${outcome} 인데 결손 없음으로 섰다`);
  }
});

test('이 런에 구성종목 작업이 없으면 블록 자체를 세우지 않는다', () => {
  assert.equal(holdingsFlow([task({ taskKey: 'PRICE_COLLECTION_KIS' })]).state, 'absent');
  assert.equal(holdingsFlow([]).state, 'absent');
});

test('흐름은 수집 → 정제 → 적재 순서로, 있는 작업만 낸다', () => {
  const f = holdingsFlow([
    load(),
    collect(1),
    task({ taskKey: 'NORMALIZE_ETF', stage: 'normalize' }),
  ]);
  assert.deepEqual(f.steps.map((s) => s.label), ['수집', '정제', '적재']);
  const partial = holdingsFlow([collect(1), load()]);
  assert.deepEqual(partial.steps.map((s) => s.label), ['수집', '적재']);
});
