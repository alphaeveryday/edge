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
