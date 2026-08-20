import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { MinuteSession, MinuteStatus } from './types.ts';
import {
  minuteDetailData,
  minuteStatusQueryKey,
  resolveMinuteDetail,
  shouldFetchMinuteDetail,
} from './minuteHistory.ts';

const session = (overrides: Partial<MinuteSession> = {}): MinuteSession => ({
  sessionId: 's1',
  dataset: 'price_minute',
  sourceGroup: 'kis',
  phase: 'ACTIVE',
  universeVersion: 'v1',
  expectedWindowCount: 2,
  processedThrough: null,
  contiguousCompleteThrough: null,
  heartbeatAt: null,
  leaseExpiresAt: null,
  leaseExpired: false,
  windows: {
    due: 0,
    claimed: 0,
    valid: 1,
    validEmpty: 1,
    incomplete: 0,
    missing: 0,
    invalid: 0,
    overdueNoEvidence: 0,
  },
  gaps: [],
  priceJobs: { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 },
  ...overrides,
});

const status = (date: string, sessions: MinuteSession[]): MinuteStatus => ({
  date,
  sessions,
  newsJobs: { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 },
});

test('날짜가 다르면 query cache key도 달라 과거 상세가 오늘 응답을 재사용하지 않는다', () => {
  assert.notDeepEqual(minuteStatusQueryKey('2026-08-18'), minuteStatusQueryKey('2026-08-19'));
});

test('오늘 상세는 이미 받은 같은 날짜 실측을 쓰고 과거 날짜만 지연 조회한다', () => {
  assert.equal(shouldFetchMinuteDetail('2026-08-19', '2026-08-19', false), false);
  assert.equal(shouldFetchMinuteDetail('2026-08-18', '2026-08-19', false), true);
  assert.equal(shouldFetchMinuteDetail(undefined, '2026-08-19', false), false);
  assert.equal(shouldFetchMinuteDetail('2026-08-18', '2026-08-19', true), false);
});

test('날짜 전환 뒤 최신 응답과 과거 명시 캐시가 같은 날짜면 최신 응답을 우선한다', () => {
  const dated = status('2026-08-20', [session({ sessionId: 'dated-cache' })]);
  const latest = status('2026-08-20', [session({ sessionId: 'latest' })]);
  assert.equal(minuteDetailData('2026-08-20', latest, 20, dated, 10), latest);
  assert.equal(minuteDetailData('2026-08-20', latest, 20, dated, 30), dated);
  assert.equal(minuteDetailData('2026-08-19', latest, 20, dated, 30), dated);
});

test('선택 날짜 응답에서 선택 데이터셋의 과거 벤더 세션만 표시한다', () => {
  const selected = session({ sessionId: 'selected' });
  const other = session({ sessionId: 'other', dataset: 'news_minute', sourceGroup: 'bigkinds' });
  const state = resolveMinuteDetail(
    '2026-08-18',
    'price_minute',
    status('2026-08-18', [selected, other]),
    false,
    false,
  );

  assert.equal(state.kind, 'ready');
  if (state.kind === 'ready') assert.deepEqual(state.sessions.map((item) => item.sessionId), ['selected']);
});

test('matching 응답의 빈 세션만 실제 세션 부재로 취급한다', () => {
  const state = resolveMinuteDetail('2026-08-18', 'price_minute', status('2026-08-18', []), false, false);
  assert.equal(state.kind, 'ready');
  if (state.kind === 'ready') assert.deepEqual(state.sessions, []);
});

test('다른 날짜의 늦은 응답은 선택 날짜의 빈 세션으로 합성하지 않는다', () => {
  assert.deepEqual(
    resolveMinuteDetail('2026-08-18', 'price_minute', status('2026-08-19', [session()]), false, false),
    { kind: 'stale' },
  );
  assert.deepEqual(
    resolveMinuteDetail('2026-08-18', 'price_minute', status('2026-08-19', [session()]), false, true),
    { kind: 'error' },
    '다른 날짜 응답만 남은 채 선택 날짜 조회가 실패하면 대기로 위장하지 않는다',
  );
});

test('초기 조회 실패는 부재가 아니며, 같은 날짜의 직전 실측은 갱신 실패 뒤에도 유지한다', () => {
  assert.deepEqual(resolveMinuteDetail('2026-08-18', 'price_minute', undefined, false, true), {
    kind: 'error',
  });

  const previous = status('2026-08-18', [session()]);
  const retained = resolveMinuteDetail('2026-08-18', 'price_minute', previous, false, true);
  assert.equal(retained.kind, 'ready');
  if (retained.kind === 'ready') {
    assert.equal(retained.refreshFailed, true);
    assert.equal(retained.minute, previous);
  }
});

test('응답 대기는 세션 부재와 별도 상태다', () => {
  assert.deepEqual(resolveMinuteDetail('2026-08-18', 'price_minute', undefined, true, false), {
    kind: 'loading',
  });
});
