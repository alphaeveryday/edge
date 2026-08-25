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
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { dateOfSlot, datesOf, realtimeDayState, realtimeSessionState, rollup, stateOf } from './dailyRollup.ts';
import type { DayCounts } from './dailyRollup.ts';
import { jobEvidence, leaseEvidence, newsDateJobEvidence } from './minuteView.ts';
import type { GridCell, GridSlot, MinuteSession, MinuteStatus } from './types.ts';

const minuteSession = (sourceGroup: string, values: Partial<MinuteSession> = {}): MinuteSession => ({
  sessionId: `session-${sourceGroup}`, dataset: 'price_minute', sourceGroup, phase: 'ACTIVE',
  universeVersion: 'v1', expectedWindowCount: 390, processedThrough: null,
  contiguousCompleteThrough: null, heartbeatAt: null, leaseExpiresAt: null, leaseExpired: false,
  windows: { due: 0, claimed: 0, valid: 0, validEmpty: 0, incomplete: 0, missing: 0, invalid: 0, overdueNoEvidence: 0 },
  gaps: [], priceJobs: { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 },
  ...values,
});

const minuteStatus = (sessions: MinuteSession[]): MinuteStatus => ({
  date: '2026-08-12', sessions,
  newsJobs: { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 },
});

test('실패 1/3은 주의이고 실패 3/3만 장애다', () => {
  const partial = minuteStatus([
    minuteSession('kis'), minuteSession('toss'), minuteSession('backup', { leaseExpired: true }),
  ]);
  const partialState = realtimeDayState('price_minute', partial.date, partial)!;
  assert.equal(partialState.state, '주의');
  assert.equal(partialState.failedSessions, 1);
  assert.equal(partialState.totalSessions, 3);
  assert.match(partialState.basis, /실패 세션 1 \/ 전체 3/);

  const failed = minuteStatus(['kis', 'toss', 'backup'].map((vendor) => minuteSession(vendor, { phase: 'FAILED' })));
  const failedState = realtimeDayState('price_minute', failed.date, failed)!;
  assert.equal(failedState.state, '장애');
  assert.equal(failedState.failedSessions, 3);
  assert.equal(failedState.totalSessions, 3);
});

test('빈 데이터와 무증거는 세션 생존 실패로 재분류하지 않는다', () => {
  const validEmpty = minuteStatus([minuteSession('kis', { windows: { ...minuteSession('x').windows, validEmpty: 390 } })]);
  assert.equal(realtimeDayState('price_minute', validEmpty.date, validEmpty)?.state, '실행 중');

  const noEvidence = minuteStatus([minuteSession('kis', { windows: { ...minuteSession('x').windows, overdueNoEvidence: 5 } })]);
  assert.equal(realtimeDayState('price_minute', noEvidence.date, noEvidence)?.state, '실행 중');
});

test('생존 판정이 없는 세션도 실패/전체 분모는 숨기지 않는다', () => {
  const terminal = minuteStatus([
    minuteSession('kis', { phase: 'FINALIZED', leaseExpired: null }),
    minuteSession('toss', { phase: 'PLANNED', leaseExpired: null }),
  ]);
  const terminalState = realtimeDayState('price_minute', terminal.date, terminal)!;
  assert.equal(terminalState.state, '상태 미제공');
  assert.equal(terminalState.failedSessions, 0);
  assert.equal(terminalState.totalSessions, 2);
  assert.match(terminalState.basis, /phase=FINALIZED/);
  assert.deepEqual(realtimeDayState('price_minute', terminal.date, minuteStatus([])), {
    state: '상태 미제공', basis: '기록된 세션 없음', failedSessions: 0, totalSessions: 0,
  });
});

test('실시간 상세는 실패 분모와 window·lease·job 근거를 함께 표시한다', () => {
  const source = readFileSync(new URL('../../pages/GridPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /실패 세션 \{live\.failedSessions\} \/ 전체 \{live\.totalSessions\}/);
  assert.match(source, /<th>창 증거<\/th>[\s\S]*<th>lease 근거<\/th>[\s\S]*<th>job 근거<\/th>/);
  assert.match(source, /날짜 job\(세션 귀속 아님\)/, '날짜 job을 벤더 세션 근거로 위장하지 않는다');
  assert.match(source, /job 축 미제공/, 'job 축 없는 데이터셋에 뉴스 날짜 job을 붙이지 않는다');
  assert.equal(source.match(/날짜 job\(세션 귀속 아님\)/g)?.length, 1, '날짜 job은 벤더마다 복제하지 않는다');
  assert.match(source, /일부 벤더 실행체 실패는 주의, 전체 벤더 실패만 장애/);
  assert.match(source, /실행체 생존\(실행 중 · 주의 · 장애\)/);
  assert.equal(source.match(/newsDateJobEvidence\(minuteDetail\)/g)?.length, 1, '날짜 job 근거는 표 밖에서 한 번만 그린다');
});

test('뉴스 날짜 job 조회 상태를 실패·대기와 구분한다', () => {
  assert.equal(newsDateJobEvidence({ kind: 'error' }), '조회 실패');
  assert.equal(newsDateJobEvidence({ kind: 'stale' }), '다른 날짜 응답 · 선택 날짜 조회 대기');
  assert.equal(newsDateJobEvidence({ kind: 'loading' }), '조회 중');
  assert.equal(newsDateJobEvidence(undefined), '상태 미제공');
});

test('job 포함 관계와 종료 세션의 원시 lease 근거를 보존한다', () => {
  assert.match(
    jobEvidence({ waiting: 0, claimed: 4, claimedExpired: 4, succeeded: 1, dead: 0 }),
    /처리 중 0 · 유효 lease 없음 4/,
  );
  const closed = minuteSession('kis', {
    phase: 'FINALIZED', leaseExpired: true, leaseExpiresAt: '2026-08-12T15:30:00+09:00',
  });
  assert.match(leaseEvidence(closed), /phase=FINALIZED[\s\S]*lease 만료[\s\S]*2026-08-12T15:30:00\+09:00/);
});

test('실시간 상세는 벤더 세션마다 자기 실행체 상태를 갖는다', () => {
  const minute = {
    date: '2026-08-12',
    sessions: [
      { dataset: 'price_minute', phase: 'ACTIVE', leaseExpired: false },
      { dataset: 'price_minute', phase: 'ACTIVE', leaseExpired: true },
    ],
  } as MinuteStatus;
  assert.deepEqual(minute.sessions.map((s) => realtimeSessionState(s).state), ['실행 중', '장애']);
});

/* 🔴 이 PR 이 `'실행 중'` 에서 `'대기'` 를 떼어 냈는데 격자가 그 새 상태를 `'계획 없음'` 과
 * **같은 클래스**로 그렸다 — DOM 이 완전히 같아서 "계획은 있고 기한 전"(정상)과 "계획 행 자체가
 * 없다"(계획 결손 후보)가 구분 불가였고, 범례에는 똑같이 생긴 박스 둘이 다른 라벨로 나란히 섰다.
 * `stateOf` 는 테스트가 있었지만 **렌더 축은 아무도 안 봤다**(변이 실증, 2026-08-12). */
test('🔴 격자에서 `대기` 와 `계획 없음` 은 다른 박스다 — 합치면 계획 결손이 정상으로 보인다', () => {
  const source = readFileSync(new URL('../../pages/GridPage.tsx', import.meta.url), 'utf8');
  const cls = (state: string) =>
    source.match(new RegExp(`'?${state}'?\\s*:\\s*'(gd-s-[a-z]+)'`))?.[1];
  const wait = cls('대기');
  const none = cls('계획 없음');
  assert.ok(wait && none, 'STATE_CLASS 에서 두 상태를 못 찾았다 — 이 테스트가 낡았다');
  assert.notEqual(wait, none, '두 상태가 같은 박스로 그려진다');
  /* 그리고 그 클래스가 실제로 **다르게 그려져야** 한다. 이름만 갈라 놓고 선언이 같으면
   * (또는 비어 있으면) 화면은 그대로 구분 불가다 — 첫 수정이 둘 다 `background: #fff` 라
   * 1px 테두리 스타일만 달랐고, 그 상태로도 이 테스트가 통과했다. 선언 본문을 비교한다. */
  const css = readFileSync(new URL('../../styles/grid.css', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  const body = (c: string) => css.match(new RegExp(`\\.${c}\\s*\\{([^}]*)\\}`))?.[1]?.trim() ?? '';
  assert.ok(body(wait!).length > 0, `.${wait} 선언이 비었다 — 기본 박스로 접힌다`);
  assert.ok(body(none!).length > 0, `.${none} 선언이 비었다`);
  assert.notEqual(body(wait!), body(none!), '두 클래스가 같은 선언이라 화면에서 같은 박스다');
  /* 대기는 **채움**으로 갈린다 — 테두리 스타일 하나에만 기대면 격자에서 안 읽힌다 */
  assert.match(css, /\.gd-s-wait::after\s*\{/, '대기 박스의 채움 표식이 사라졌다');
  /* 운영자 설명에도 그 갈래가 있어야 한다 — 박스만 갈라 놓고 안 적으면 못 읽는다 */
  assert.match(source, /'대기 —/, 'STATUS_TIP 에 대기 항목이 없다');
});

const cell = (o: Partial<GridCell> & Pick<GridCell, 'taskKey'>): GridCell => ({
  stage: 'raw',
  planStatus: 'DUE',
  outcome: 'FULFILLED',
  dataStatus: 'UNKNOWN',
  recordsOut: 1,
  unsupportedRecords: null,
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

test('지원 제외는 실행 상세에 보존하지만 주의·유실 집계에는 넣지 않는다', () => {
  const r = rollup([
    slot('etf-daily:2026-08-24T15:40', '2026-08-24', [
      cell({
        taskKey: 'LOAD_ETF_HOLDINGS',
        dataStatus: 'VALID',
        recordsOut: 958,
        unsupportedRecords: 42,
        failedRecords: 0,
      }),
    ]),
  ]).get('etf_holdings|2026-08-24')!;

  assert.equal(r.state, '정상');
  assert.equal(r.counts.failedRecords, 0);
  assert.equal(r.executions[0].tasks[0].unsupportedRecords, 42);
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

test('도는 시도가 없는 PENDING은 실행 중이 아니라 대기다', () => {
  assert.equal(stateOf(counts({ pending: 1 }), 1), '대기');
  assert.equal(stateOf(counts({ running: 1, pending: 1 }), 2), '실행 중');
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

/* ── `dateOfSlot` 의 **우선순위** — 실행 상세 링크의 조회 창이 이 규칙에 달려 있다 (축 E) ──
 *
 * 위 두 테스트는 `datesOf` 를 거쳐 재는데, 거기서는 두 날짜가 **같은 픽스처**라 우선순위가
 * 뒤집혀도 통과한다. 갈리는 입력을 직접 넣어야 규칙이 잡힌다.
 *
 * ⚠️ **이 검사를 브라우저 하네스로 밀지 마라.** 한 번 그렇게 했다가 스텁의 한 응답만 갈라 놓아,
 * 같은 런 키가 화면마다 다른 거래일을 갖는 **원장에 존재할 수 없는 상태**를 만들었다. 그러면
 * 가드들이 서로 모순된 링크를 동시에 통과시킨다(리뷰 3라운드가 단언 방향을, 4라운드가 그
 * 모순을 잡았다). `dateOfSlot` 은 JSX 가 없는 순수 함수라 **여기서 직접 잴 수 있다** — 픽스처를
 * 비틀 이유가 없다.
 *
 * ⚠️ 오늘 이 갈림을 만드는 writer 는 없다(`ops/planner.py` 가 `trading_date` 와 run_key 를 같은
 * `slot` 에서 뽑고, `create_pipeline_run` 의 다른 호출자는 테스트뿐이다). 그래도 지운 채 두지
 * 않는 이유: 타입이 허용하고(`tradingDate: string | null`), 서버의 `COALESCE` 도 둘이 다를 수
 * 있다는 전제 위에 있으며, 무엇보다 **우선순위 자체가 코드로 존재하는 규칙**이라 근거 없이
 * 뒤집히면 조용히 창 밖 링크가 된다. 관측된 상태가 아니라 **선언된 계약**을 지키는 검사다. */
test('dateOfSlot 은 tradingDate 를 우선한다 — run_key 의 슬롯 날짜가 아니라', () => {
  assert.equal(
    dateOfSlot({ tradingDate: '2026-07-31', runKey: 'etf-daily:2026-08-03T15:40' }),
    '2026-07-31',
  );
});

test('dateOfSlot 은 tradingDate 가 없을 때만 run_key 로 떨어진다', () => {
  assert.equal(dateOfSlot({ tradingDate: null, runKey: 'etf-daily:2026-08-03T15:40' }), '2026-08-03');
});

/* ⚠️ **`dateOfSlot({ runKey })` 를 여기서 검사하지 않는 이유: 그건 이제 컴파일되지 않는다.**
 * `tradingDate` 를 필수로 둔 것이 가드다 — 거래일을 쥔 호출부가 부분 객체로 축을 흘리면 tsc 가
 * 잡는다. 거래일을 모르는 자리는 `{ tradingDate: null }` 을 명시한다(리뷰 5·6라운드). */

test('빈 문자열 tradingDate 는 부재로 접는다 — 값으로 내보내지 않는다', () => {
  /* 🔴 `??` 로 쓰면 `''` 가 그대로 나가고, `runHref` 의 falsy 필터가 그걸 지워 링크가 조용히
   * "날짜 없음"으로 퇴행한다(= 상세가 최신 하루만 조회). 가드와 바인딩이 falsy 를 다르게
   * 읽던 자리라, 여기서 못을 박는다. */
  assert.equal(dateOfSlot({ tradingDate: '', runKey: 'etf-daily:2026-08-03T15:40' }), '2026-08-03');
});

test('날짜를 못 읽으면 null 이다 — 아무 날이나 지어내지 않는다', () => {
  /* 호출부는 이 null 로 **링크를 만들지 않는다**. 여기서 오늘 날짜 같은 걸 돌려주면 상세가
   * 엉뚱한 창을 열고, 그 부재가 "안 물어봤다"로 읽힌다. */
  assert.equal(dateOfSlot({ tradingDate: null, runKey: 'etf-daily:no-slot-date' }), null);
  assert.equal(dateOfSlot({ tradingDate: '', runKey: '' }), null);
});

test('모양만 맞으면 그대로 낸다 — 달력 실재성은 여기서 안 본다(일부러)', () => {
  /* 🔴 `2026-02-30` 은 없는 날이고, 이 값이 `?date=` 로 나가면 서버가 400 을 낸다. 그래도
   * 여기서 `null` 로 접지 않는다: 이 함수의 다른 소비자(`rollup`·`datesOf`)는 날짜 없는 슬롯을
   * **건너뛰므로** 그 런이 실행 이력에서 통째로 사라진다. 손으로 친 URL 의 400 은 fail-loud,
   * 실재 런의 실종은 fail-silent 다. 소비자 둘이 반대 방향을 원해 여기서 정하면 한쪽이 진다
   * (10라운드에 걸렀다가 11라운드에 되돌렸다 — 다시 제안되면 이 테스트가 근거다). */
  assert.equal(
    dateOfSlot({ tradingDate: null, runKey: 'etf-daily:2026-02-30T15:40' }),
    '2026-02-30',
  );
});

test('기동 실패한 런은 그 레인의 데이터셋 행에 남는다 — "계획 없음"과 반대 사실이다', () => {
  /* 기동 실패 슬롯은 작업 행이 없다. 작업에서만 시작하면 이 슬롯이 롤업에서 통째로
   * 사라지는데, `datesOf()` 는 그 날짜를 넣으므로 격자에 열은 서고 그 레인 행만
   * "계획 없음"으로 그려진다 — 계획이 없던 게 아니라 **런이 못 떴다**는 반대 사실이다.
   * 귀속은 카탈로그가 아는 레인→데이터셋으로만 간다(지어내지 않는다). */
  const slot: GridSlot = {
    runKey: 'news:2026-07-30T15:30',
    launchStatus: 'LAUNCH_FAILED',
    orchestrationStatus: null,
    tradingDate: '2026-07-30',
    tasks: [],
  };
  const map = rollup([slot]);
  const row = map.get('stock_news|2026-07-30');
  assert.ok(row, '뉴스 레인의 데이터셋 행이 서야 한다');
  assert.equal(row!.state, '장애', '계획 없음이 아니다');
  assert.equal(row!.executions.length, 1);
  assert.equal(row!.executions[0].notLaunched, true);
  /* 원장에 DUE 증거가 없으므로 기대 실행 수는 지어내지 않는다 */
  assert.equal(row!.expected, 0);

  /* 다른 레인은 안 건드린다 — 뉴스 런이 못 떴다고 시장 데이터셋이 장애가 되지 않는다 */
  assert.equal(map.get('price_daily|2026-07-30'), undefined);
});

test('작업이 없어도 기동은 된 슬롯은 행을 만들지 않는다 — 없는 실패를 지어내지 않는다', () => {
  const launched: GridSlot = {
    runKey: 'news:2026-07-30T15:30',
    launchStatus: 'LAUNCHED',
    orchestrationStatus: 'SUCCEEDED',
    tradingDate: '2026-07-30',
    tasks: [],
  };
  assert.equal(rollup([launched]).size, 0);
});
