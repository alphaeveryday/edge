/* 규칙 하나당 위반/비위반 픽스처 각 1 + 경계 케이스 (ALPHA-738, 명세 §2-1).
 *
 * 실행: node --test src/rules/   (Node 23.6+ 네이티브 TS)
 *
 * 테스트가 지키는 의도: 규칙의 조건이 명세 §2 표와 다르게 느슨해지거나(거짓 양성)
 * 관대해지면(거짓 음성 — 원장이 관대해지는 방향이 상습 오류다) 여기서 깨져야 한다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildReport, evaluate, runbookOf } from './evaluate.ts';
import type { Facts, MinuteSessionFact, RunFact, TaskFact, Violation } from './types.ts';

const NOW = new Date('2026-08-03T16:21:00+09:00');

function emptyFacts(): Facts {
  return {
    runs: [],
    tasks: [],
    datasets: [],
    chain: { feeds: [], stages: [] },
    queues: [],
    outputs: [],
    boundary: { published_without_delivery: 0, delivery_now_nonpublished: 0 },
    etf_ledger: { rows: [] },
    runbook: {},
    meta: { db: '2026-08-03T16:20:00+09:00', aws: '2026-08-03T16:20:00+09:00', today: '2026-08-03' },
  };
}

const run = (o: Partial<RunFact>): RunFact => ({
  id: 'lane:2026-08-03T15:40',
  lane: 'lane',
  kind: 'scheduled',
  trading_date: '2026-08-03',
  ...o,
});

const task = (o: Partial<TaskFact>): TaskFact => ({
  task_key: 'T1',
  run_id: 'lane:2026-08-03T15:40',
  pipeline_type: 'etf-daily',
  stage: 'raw',
  required: true,
  task_outcome: 'FULFILLED',
  ...o,
});

/**
 * 위반 필드 규약(types.ts `RawViolation`)을 **구조로** 검사한다.
 *
 * 지키는 의도: 한 필드가 세 용도로 돌아가는 것을 막는다. 판정 문자열(`STALE`·`TIMED_OUT`)이
 * 다시 `metric` 에 들어오면 화면이 `typeof` 로 용도를 추측해야 하고, 문맥 문장이 `unit` 에
 * 들어오면(수치 없이 단위만) 숫자 열이 문단이 된다. 문구를 검사하지 않는 이유는 문구가 늘
 * 바뀌기 때문이다 — 타입 관계는 안 바뀐다.
 */
function assertContract(v: Violation) {
  /* ⚠️ 이건 빈 `target` 을 막을 뿐 **targetId 표류를 막지 못한다** — 엔진이 늘 `target` 으로
   * 폴백하므로 룰이 `targetId` 를 지워도 여기선 안 깨진다. 그 축은 아래 런북 키 단언이 지킨다. */
  assert.ok(v.targetId, `${v.rule}: targetId 가 비었다`);
  /* `why` 는 규약 이후 문맥의 유일한 운반자다 — 비면 상세·ⓘ 의 '왜'가 통째로 빈다 */
  assert.ok(v.why, `${v.rule}: why 가 비었다 — 문맥을 실을 다른 필드가 없다`);
  assert.ok(
    v.metric === null || typeof v.metric === 'number',
    `${v.rule}: metric 은 수 아니면 null 이다 (받은 값 ${JSON.stringify(v.metric)})`,
  );
  assert.equal(
    v.unit != null,
    v.metric != null,
    `${v.rule}: unit 은 metric 이 있을 때만 있다 (metric=${JSON.stringify(v.metric)} unit=${JSON.stringify(v.unit)})`,
  );
}

function hits(f: Facts, rule: string) {
  const vs = evaluate(f, NOW).violations.filter((v) => v.rule === rule);
  vs.forEach(assertContract);
  return vs;
}

test('R01 계획 슬롯 미기동 — planned+행 없음이면 위반, 행이 있으면 조용', () => {
  const f = emptyFacts();
  f.runs = [run({ planned: true, no_run_row: true }), run({ id: 'ok', planned: true })];
  assert.equal(hits(f, 'R01').length, 1);
  assert.equal(hits(f, 'R01')[0].target, 'lane:2026-08-03T15:40');
});

test('R02 마감 초과 미귀결 — 마감 경과+원장 공백만 위반, 마감 전·귀결됨은 아님', () => {
  const f = emptyFacts();
  f.runs = [
    run({ id: 'late', deadline: '2026-08-03T16:00:00+09:00' }), // 경과+공백 → 위반
    run({ id: 'future', deadline: '2026-08-03T21:40:00+09:00' }), // 마감 전
    run({ id: 'settled', deadline: '2026-08-03T16:00:00+09:00', ledger_status: 'SUCCEEDED' }),
  ];
  assert.deepEqual(hits(f, 'R02').map((v) => v.target), ['late']);
});

test('R03 제어면·원장 불일치 — 양쪽 다 있고 다를 때만, 한쪽 부재는 불일치가 아니다', () => {
  const f = emptyFacts();
  f.runs = [
    run({ id: 'diff', aws_status: 'FAILED', ledger_status: 'RUNNING' }),
    run({ id: 'same', aws_status: 'SUCCEEDED', ledger_status: 'SUCCEEDED' }),
    run({ id: 'one-side', aws_status: 'FAILED' }), // 원장 부재 — R02/R04 소관
  ];
  assert.deepEqual(hits(f, 'R03').map((v) => v.target), ['diff']);
});

test('R04 런 실패 — 원장 terminal 실패는 위반', () => {
  const f = emptyFacts();
  f.runs = [run({ id: 'f1', ledger_status: 'TIMED_OUT' }), run({ id: 'ok', ledger_status: 'SUCCEEDED' })];
  assert.deepEqual(hits(f, 'R04').map((v) => v.target), ['f1']);
});

test('R04 경계 — 원장 공백+AWS 실패는 정규(scheduled) 런에 한해서만 걸린다', () => {
  const f = emptyFacts();
  f.runs = [
    run({ id: 'sched', kind: 'scheduled', aws_status: 'FAILED' }),
    run({ id: 'man', kind: 'manual', aws_status: 'FAILED' }),
    run({ id: 'bf', kind: 'backfill', aws_status: 'TIMED_OUT' }),
  ];
  assert.deepEqual(hits(f, 'R04').map((v) => v.target), ['sched']);
});

test('R05 필수 작업 미귀결 — required 미귀결만, 비필수·FULFILLED 는 아님', () => {
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'failed', task_outcome: 'FAILED' }),
    task({ task_key: 'pending', task_outcome: 'PENDING' }),
    task({ task_key: 'optional', required: false, task_outcome: 'FAILED' }),
    task({ task_key: 'done' }),
  ];
  const v = hits(f, 'R05');
  assert.deepEqual(v.map((x) => x.target), ['failed', 'pending']);
  // cause 플래그: FAILED=원인(false), PENDING=파생(true) — R05→R05 간선의 재료
  assert.equal(v[0].cause, false);
  assert.equal(v[1].cause, true);
});

test('R05 경계 — 계획에서 제외된(SKIPPED) 필수 작업은 미귀결이 아니다', () => {
  const f = emptyFacts();
  /* plan 축과 outcome 축은 다른 축이다. SKIPPED 는 outcome 이 null 이라 "FULFILLED 가 아니다"에
   * 걸리지만, 안 한 게 아니라 할 일이 아니었다 — 규칙 명세의 "∧ DUE" 가 그 뜻이다. */
  f.tasks = [
    task({ task_key: 'skipped', plan_status: 'SKIPPED', task_outcome: null }),
    task({ task_key: 'due-pending', plan_status: 'DUE', task_outcome: 'PENDING' }),
  ];
  assert.deepEqual(hits(f, 'R05').map((v) => v.target), ['due-pending']);
});

test('R06 데이터 부분 유실 — INCOMPLETE+failed_records>0 만, 유실 0이면 아님', () => {
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'lost', data_status: 'INCOMPLETE', failed_records: 12 }),
    task({ task_key: 'flagged-only', data_status: 'INCOMPLETE', failed_records: 0 }),
    task({ task_key: 'valid', data_status: 'VALID', failed_records: 3 }),
  ];
  assert.deepEqual(hits(f, 'R06').map((v) => v.target), ['lost']);
});

test('R07 완전성 결손 — received<expected 만 위반', () => {
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'short', completeness_expected: 363, completeness_received: 361 }),
    task({ task_key: 'full', completeness_expected: 33, completeness_received: 33 }),
  ];
  const v = hits(f, 'R07');
  assert.equal(v.length, 1);
  assert.equal(v[0].metric, 2);
});

test('R07 경계 — expected=null(분모 미배선)은 위반이 아니라 평가 대상 아님', () => {
  const f = emptyFacts();
  f.tasks = [task({ task_key: 'unwired', completeness_expected: null, completeness_received: null })];
  assert.equal(hits(f, 'R07').length, 0);
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R07')!;
  assert.equal(rr.evaluated, true); // 규칙 자체는 돌았다 — 조용한 것
  assert.match(rr.note ?? '', /분모 배선 작업 0\/1/);
});

test('R08 신선도 위반 — actual<expected 만, 창 계약·근거 없음은 아님', () => {
  const f = emptyFacts();
  f.datasets = [
    { id: 'stale', contract: true, expected_as_of: '2026-08-03', actual_as_of: '2026-08-01' },
    { id: 'fresh', contract: true, expected_as_of: '2026-08-03', actual_as_of: '2026-08-03' },
    { id: 'window', contract: true, window_contract: true, expected_as_of: '2026-08-03', actual_as_of: '2026-08-01' },
    { id: 'no-actual', contract: true, expected_as_of: '2026-08-03', actual_as_of: null },
  ];
  assert.deepEqual(hits(f, 'R08').map((v) => v.target), ['stale']);
});

test('R08 경계 — actual 근거가 전무하면 evaluated:false (못 돈 것 ≠ 조용한 것)', () => {
  const f = emptyFacts();
  f.datasets = [{ id: 'krx', contract: true, expected_as_of: '2026-08-03', actual_as_of: null }];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R08')!;
  assert.equal(rr.evaluated, false);
});

test('R09 신선도 판정 불가 — unverifiable 사유가 있을 때만', () => {
  const f = emptyFacts();
  f.datasets = [
    { id: 'krx', unverifiable: '원천이 as-of 를 주지 않는다 — 영구 UNKNOWN(설계 결정)' },
    { id: 'ok', contract: true, expected_as_of: '2026-08-03', actual_as_of: '2026-08-03' },
  ];
  assert.deepEqual(hits(f, 'R09').map((v) => v.target), ['krx']);
});

test('R10 체인 손실 — 인접 감소만 위반, 갈래(src)가 기록된다', () => {
  const f = emptyFacts();
  f.chain = {
    feeds: [
      { id: 'fb', label: '배치 트리거', v: 20, unit: 'ETF', src: 't' },
      { id: 'fi', label: '장중 트리거', v: 65, unit: '건', src: 't' },
    ],
    stages: [
      { id: 'c.obs', label: '관측', batch: 20, intraday: 0, src: 's' },
      { id: 'c.run', label: '런', batch: 16, intraday: 0, src: 's' },
    ],
  };
  const v = hits(f, 'R10');
  /* 앵커·런북 키가 매달리는 축은 표시 문구가 아니라 `targetId` 다 — 라벨을 바꿔도 안 끊긴다 */
  assert.deepEqual(
    v.map((x) => [x.targetId, x.metric]),
    [
      ['batch:c.run', 4],
      ['intraday:c.obs', 65],
    ],
  );
  /* 표에 서는 것은 사람이 읽을 인접 단계다 — `batch:c.run` 을 그대로 그리면 대상이 뭔지 모른다 */
  assert.deepEqual(v.map((x) => x.target), ['관측 → 런', '장중 트리거 → 관측']);
});

test('R10 경계 — blind(관측 불가) 단계는 0으로 세지 않고 비교 축에서 빠진다', () => {
  const f = emptyFacts();
  f.chain = {
    feeds: [
      { id: 'fb', label: '배치', v: 10, unit: 'ETF', src: 't' },
      { id: 'fi', label: '장중', v: 0, unit: '건', src: 't' },
    ],
    stages: [
      { id: 'a', label: 'A', batch: 10, intraday: 0, src: 's' },
      { id: 'blind', label: '소비', blind: true, src: 's' }, // 값 없음 — 관측 채널 부재
      { id: 'b', label: 'B', batch: 10, intraday: 0, src: 's' },
    ],
  };
  assert.equal(hits(f, 'R10').length, 0);
});

test('R11 소비자 부재 — 대기>0·in-flight 0·구독자 0 전부 만족할 때만', () => {
  const f = emptyFacts();
  f.queues = [
    { name: 'orphan', visible: 65, in_flight: 0, dlq: 0, subscribers: [] },
    { name: 'consuming', visible: 65, in_flight: 3, dlq: 0, subscribers: [] },
    { name: 'subscribed', visible: 65, in_flight: 0, dlq: 0, subscribers: ['svc'] },
    { name: 'idle', visible: 0, in_flight: 0, dlq: 0, subscribers: [] },
  ];
  assert.deepEqual(hits(f, 'R11').map((v) => v.target), ['orphan']);
});

test('R11 경계 — 구독 매핑 계측이 아예 없으면 evaluated:false', () => {
  const f = emptyFacts();
  f.queues = [{ name: 'q', visible: 65, in_flight: 0, dlq: 0 }]; // subscribers 필드 자체 부재
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R11')!;
  assert.equal(rr.evaluated, false);
});

test('R12 DLQ 유실 — dlq>0 만 위반, 0은 "봤는데 0"으로 조용', () => {
  const f = emptyFacts();
  f.queues = [
    { name: 'dead', visible: 0, in_flight: 0, dlq: 3, subscribers: ['svc'] },
    { name: 'clean', visible: 0, in_flight: 0, dlq: 0, subscribers: ['svc'] },
  ];
  assert.deepEqual(hits(f, 'R12').map((v) => v.target), ['dead']);
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R12')!;
  assert.equal(rr.evaluated, true);
});

test('R13 산출 이상 — ±25% 이상만, 기준(base) 없으면 평가 대상 아님', () => {
  const f = emptyFacts();
  f.outputs = [
    { id: 'half', label: '게시', today: 16, base: 32, unit: '종' }, // -50%
    { id: 'near', label: '문서', today: 76, base: 100, unit: '건' }, // -24%
    { id: 'nobase', label: '신규', today: 5, base: null, unit: '건' },
  ];
  const v = hits(f, 'R13');
  /* 편차율은 **양이다** — 문자열 `'-50%'` 로 두면 정렬(크기순)과 숫자 열이 이 값을 못 쓴다.
   * 대상은 라벨(사람이 읽을 것), 키는 산출 id(`half`) 로 갈린다. */
  assert.deepEqual(v.map((x) => [x.targetId, x.metric, x.unit]), [['half', -50, '%']]);
  assert.deepEqual(v.map((x) => x.target), ['게시']);
});

test('R14 전달 정합 — 게시·미발번은 P0, 시드 유래 비게시 전달은 P2+seed 로 강등', () => {
  const f = emptyFacts();
  f.boundary = {
    published_without_delivery: 2,
    delivery_now_nonpublished: 1,
    seed_note: '로컬 시드(WITHDRAWN)',
  };
  const v = hits(f, 'R14');
  assert.equal(v.length, 2);
  /* 사건 키·흡수 조인이 매달리는 축 — 표시 문구로 갈리면 조인이 문장에 걸린다.
   * `assertContract` 는 이걸 못 잡는다(엔진이 target 으로 폴백해 늘 채워진다) */
  assert.deepEqual(v.map((x) => x.targetId), ['pub_no_delivery', 'delivery_nonpub']);
  assert.equal(v[0].sev, 'P0');
  assert.equal(v[1].sev, 'P2');
  assert.equal(v[1].seed, true);
  f.boundary = { published_without_delivery: 0, delivery_now_nonpublished: 0 };
  assert.equal(hits(f, 'R14').length, 0);

  /* seed_note 가 없는 사실 — 시드가 걷히면 실제로 이 모양이 된다. `why` 는 규약 이후 문맥의
   * 유일한 운반자라 비면 상세·ⓘ 의 '왜'가 통째로 빈다. 이 케이스가 없으면 `assertContract` 의
   * why 단언이 **자기가 막으려는 결함을 못 잡는다**(픽스처·스냅샷 둘 다 seed_note 를 준다). */
  f.boundary = { published_without_delivery: 0, delivery_now_nonpublished: 1 };
  assert.match(hits(f, 'R14')[0].why, /기록이 없다/, '기록 부재를 사유 없음으로 그리지 않는다');
});

test('R15 ETF 분석 실패 — FAILED 행들이 위반 1건으로 집계되고 목록이 남는다', () => {
  const f = emptyFacts();
  f.etf_ledger = {
    rows: [
      { etf: 'a', name: 'A', triggered: true, outcome: 'FAILED', error: 'NaN weight' },
      { etf: 'b', name: 'B', triggered: true, outcome: 'FAILED', error: 'NaN weight' },
      { etf: 'c', name: 'C', triggered: true, outcome: 'FULFILLED' },
    ],
    mock: true,
  };
  const v = hits(f, 'R15');
  assert.equal(v.length, 1);
  assert.equal(v[0].metric, 2);
  /* 룰 단위 런북(`R15`)으로 폴백하더라도 사건 키는 식별자여야 한다 — R14 와 같은 이유 */
  assert.equal(v[0].targetId, 'analyze.failed');
  assert.deepEqual(v[0].list, ['A', 'B']);
  /* 원장이 사유를 안 남긴 실패 — R14 와 같은 이유로 이 갈래를 밟는 픽스처가 있어야 한다.
   * `error: null` 을 "오류가 없다"로 읽으면 실패인데 사유가 빈 채로 화면에 선다. */
  f.etf_ledger = {
    rows: [{ etf: 'd', name: 'D', triggered: true, outcome: 'FAILED', error: null }],
    mock: true,
  };
  assert.match(hits(f, 'R15')[0].why, /기록이 없다/, '사유 부재를 사유 없음으로 그리지 않는다');

  f.etf_ledger = { rows: [{ etf: 'c', name: 'C', triggered: true, outcome: 'FULFILLED' }] };
  assert.equal(hits(f, 'R15').length, 0);
});

test('R15 경계 — per-ETF 원장 계측이 없으면 evaluated:false (계측 없음 ≠ 0)', () => {
  const f = emptyFacts();
  delete f.etf_ledger;
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R15')!;
  assert.equal(rr.evaluated, false);
  assert.equal(rr.violations, 0);
});

test('R16 재시도 소진 — 상한 도달+미귀결만, FULFILLED·정책 미선언은 아님', () => {
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'spent', task_outcome: 'FAILED', attempts: 3, max_retries: 3 }),
    task({ task_key: 'retrying', task_outcome: 'FAILED', attempts: 1, max_retries: 3 }),
    task({ task_key: 'done', attempts: 3, max_retries: 3 }),
    task({ task_key: 'no-policy', task_outcome: 'FAILED', attempts: 5, max_retries: null }),
  ];
  assert.deepEqual(hits(f, 'R16').map((v) => v.target), ['spent']);
});

test('R16 경계 — 정책 필드가 전무하면 evaluated:false (없는 상한을 분모로 그리지 않는다)', () => {
  const f = emptyFacts();
  f.tasks = [task({ task_key: 'no-policy', task_outcome: 'FAILED', attempts: 5, max_retries: null })];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R16')!;
  assert.equal(rr.evaluated, false);
});

test('R16 경계 — max_retries=0 은 상한 0회가 아니라 정책 미선언이다 (분모 0 을 만들지 않는다)', () => {
  const f = emptyFacts();
  /* 원장은 정책 없음을 0 으로 적는다. 0 을 상한으로 읽으면 attempts>=0 이 항상 참이라
   * 모든 미귀결 작업이 "재시도 소진"으로 둔갑하고, 규칙은 못 도는데 돌았다고 주장한다. */
  f.tasks = [task({ task_key: 'no-policy', task_outcome: 'FAILED', attempts: 1, max_retries: 0 })];
  assert.equal(hits(f, 'R16').length, 0);
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R16')!;
  assert.equal(rr.evaluated, false);
});

/* ── 실시간(1분) 레인 R17~R19 ──
 * 이 축은 스냅샷에 없다 — 화면이 `/sources/minute` 응답을 실을 때만 돈다. 그래서 "축이 없으면
 * evaluated:false" 가 조건 자체만큼 중요하다(못 돈 규칙과 조용한 규칙이 같아 보이면 안 된다). */

const session = (o: Partial<MinuteSessionFact>): MinuteSessionFact => ({
  dataset: 'price_minute',
  sourceGroup: 'KRX',
  phase: 'ACTIVE',
  leaseExpired: false,
  overdueNoEvidence: 0,
  deadJobs: 0,
  ...o,
});
const withMinute = (sessions: MinuteSessionFact[]): Facts => {
  const f = emptyFacts();
  f.minute = { date: '2026-08-03', sessions };
  return f;
};

test('R17~R19 — minute 축이 없으면 evaluated:false (위반 0건이 아니다)', () => {
  const rep = buildReport(emptyFacts(), NOW);
  for (const id of ['R17', 'R18', 'R19']) {
    const rr = rep.rules.find((r) => r.id === id)!;
    assert.equal(rr.evaluated, false, `${id} 는 못 돈 것이지 조용한 것이 아니다`);
  }
});

test('R17 실행 증거 끊김 — 가동 중 lease 만료는 위반, 유효하면 조용', () => {
  assert.equal(hits(withMinute([session({ leaseExpired: true })]), 'R17').length, 1);
  assert.equal(hits(withMinute([session({ leaseExpired: false })]), 'R17').length, 0);
});

test('R17 경계 — 종료 국면의 lease 만료는 위반이 아니다 (매 장 마감 거짓 P0 방지)', () => {
  /* drain 이후 실행체가 떠나는 것은 정상이다. 이걸 위반으로 세면 거래일마다 P0 가 뜨고,
   * 그러면 P0 가 신호가 아니라 소음이 된다 — 관대해지는 쪽이 아니라 엄해지는 쪽 오류다. */
  for (const phase of ['DRAINED', 'QC_RUNNING', 'FINALIZED']) {
    assert.equal(hits(withMinute([session({ phase, leaseExpired: true })]), 'R17').length, 0, phase);
  }
  /* FAILED 는 국면과 무관하게 위반이다 — 세션이 실패로 닫혔다는 사실이다 */
  assert.equal(hits(withMinute([session({ phase: 'FAILED', leaseExpired: false })]), 'R17').length, 1);
});

test('R17 경계 — lease 부재(null)는 만료가 아니다 (기동 증거 없음 ≠ 끊김)', () => {
  assert.equal(hits(withMinute([session({ leaseExpired: null })]), 'R17').length, 0);
});

test('R18 무증거 창 — 임계 5창. 4창은 조용하고 5창부터 위반', () => {
  assert.equal(hits(withMinute([session({ overdueNoEvidence: 4 })]), 'R18').length, 0);
  const v = hits(withMinute([session({ overdueNoEvidence: 5 })]), 'R18');
  assert.equal(v.length, 1);
  assert.equal(v[0].metric, 5);
  assert.equal(v[0].sev, 'P1');
});

test('R19 후속 처리 유실 — DEAD 는 종료 상태라 1건부터 위반', () => {
  assert.equal(hits(withMinute([session({ deadJobs: 0 })]), 'R19').length, 0);
  assert.equal(hits(withMinute([session({ deadJobs: 1 })]), 'R19').length, 1);
});

test('실시간 위반은 그 데이터셋의 세션으로 드릴다운한다 — 런 축으로 보내지 않는다', () => {
  const v = hits(withMinute([session({ dataset: 'news_minute', leaseExpired: true })]), 'R17')[0];
  assert.deepEqual(v.drill, ['dataset', 'ds-news_minute']);
});

test('R17~R19 는 서로 흡수되지 않는다 — 같은 데이터셋이어도 사건 3건이다', () => {
  /* 시간 축이 다르다(지금 끊김 vs 그날 누적). 응답에 해소 시각이 없어 지금 끊김이 그날 공백
   * 전부의 원인이라고 말할 수 없다 — 간선을 그으면 원인을 지어내는 것이다. */
  const ev = evaluate(
    withMinute([session({ leaseExpired: true, overdueNoEvidence: 9, deadJobs: 2 })]),
    NOW,
  );
  assert.equal(ev.incidents.length, 3);
});

/* ── 인과 병합 ── */

test('사건 병합 — 같은 런의 R05·R16 이 R04 카드 하나로 접히고, 흡수된 위반은 남는다', () => {
  const f = emptyFacts();
  const runId = 'news:2026-08-03T15:30';
  f.runs = [run({ id: runId, lane: 'news', ledger_status: 'TIMED_OUT' })];
  f.tasks = [
    task({ task_key: 'LOAD_DOCUMENTS', run_id: runId, task_outcome: 'FAILED', attempts: 3, max_retries: 3 }),
    task({ task_key: 'ASSEMBLE', run_id: runId, task_outcome: 'PENDING' }),
  ];
  const ev = evaluate(f, NOW);
  // 위반: R04 1 + R05 2 + R16 1 = 4건 — 전부 보존된다
  assert.equal(ev.violations.length, 4);
  // 사건: R04 뿌리 하나 (R05 FAILED→R04, R05 PENDING→R05 FAILED→R04, R16→R04)
  assert.equal(ev.incidents.length, 1);
  const I = ev.incidents[0];
  assert.equal(I.root.rule, 'R04');
  assert.equal(I.size, 4);
  // 흡수 위반마다 인과 문구가 붙는다 — 지우면 못 믿을 화면이 된다
  assert.ok(I.members.every((m) => m.why.length > 0));
});

test('사건 병합 경계 — 다른 런의 작업 실패는 그 런 실패에 붙지 않는다(같은 런 ≠ 인과 아님, 간선 조건대로만)', () => {
  const f = emptyFacts();
  f.runs = [run({ id: 'runA', ledger_status: 'FAILED' })];
  f.tasks = [task({ task_key: 'other', run_id: 'runB', task_outcome: 'FAILED' })];
  const ev = evaluate(f, NOW);
  assert.equal(ev.incidents.length, 2); // 병합되지 않는다
});

test('정렬 — 심각도 → 연쇄 크기 → 수치. 사건 심각도는 구성원 최고치로 승격된다', () => {
  const f = emptyFacts();
  // P1 사건(연쇄 2: R02→R03)과 독립 P0(R12) — P0 이 앞이어야 한다
  f.runs = [
    run({ id: 'proj', aws_status: 'SUCCEEDED', ledger_status: 'RUNNING', deadline: '2026-08-03T16:00:00+09:00' }),
  ];
  // ledger_status 가 있으면 R02 안 걸림 — deadline 경과 + 원장 공백 런을 따로
  f.runs.push(run({ id: 'open', deadline: '2026-08-03T16:00:00+09:00' }));
  f.queues = [{ name: 'dead', visible: 0, in_flight: 0, dlq: 9, subscribers: ['svc'] }];
  const ev = evaluate(f, NOW);
  assert.equal(ev.incidents[0].root.rule, 'R12'); // P0 먼저
});

test('정렬 3순위 — 수치는 크기순이다. 부호가 아니라 절댓값으로 잰다', () => {
  /* 편차율이 수로 정규화되면서 음수 metric 이 생겼다. 원값으로 재면 **가장 큰 감소가 맨 아래로**
   * 간다 — 목록이 심각한 것부터라는 전제를 깨는데, 심각도·연쇄 크기가 같아 아무 단언도 안 밟는다. */
  const f = emptyFacts();
  f.outputs = [
    { id: 'small', label: '작은 증가', today: 130, base: 100, unit: '건' }, // +30%
    { id: 'big', label: '큰 감소', today: 50, base: 100, unit: '건' }, // -50%
  ];
  const order = evaluate(f, NOW).incidents.map((i) => i.root.targetId);
  /* 목록 전체를 deepEqual 하면 "빈 사실에서 다른 룰이 안 걸린다"에까지 기대게 된다 —
   * 검사하려는 건 3순위 비교자 하나다. 둘의 상대 순서만 본다(둘 다 있는지 먼저 확인). */
  assert.ok(order.includes('big') && order.includes('small'), `둘 다 걸려야 한다: ${order}`);
  assert.ok(order.indexOf('big') < order.indexOf('small'), `크기순이 아니다: ${order}`);
});

/* ── 리뷰 계약 §5 ── */

test('리포트 — evaluated:false 와 violations:0 이 구분되고, 흡수 위반에 absorbed_into 가 붙는다', () => {
  const f = emptyFacts();
  delete f.etf_ledger; // R15 못 돈다
  f.queues = [{ name: 'clean', visible: 0, in_flight: 0, dlq: 0, subscribers: ['svc'] }]; // R12 돌았는데 0
  const runId = 'news:2026-08-03T15:30';
  f.runs = [run({ id: runId, lane: 'news', ledger_status: 'TIMED_OUT' })];
  f.tasks = [task({ task_key: 'LOAD_DOCUMENTS', run_id: runId, task_outcome: 'FAILED' })];
  const rep = buildReport(f, NOW);
  assert.equal(rep.rules.find((r) => r.id === 'R15')!.evaluated, false);
  assert.equal(rep.rules.find((r) => r.id === 'R12')!.evaluated, true);
  assert.equal(rep.rules.find((r) => r.id === 'R12')!.violations, 0);
  const absorbed = rep.violations.find((v) => v.rule === 'R05')!;
  assert.equal(absorbed.absorbed_into, `R04:${runId}`);
  assert.equal(rep.incidents.length, 1);
  assert.equal(rep.incidents[0].members[0].cause, '런이 죽어 작업이 귀결되지 못했다');
});

test('스냅샷 회귀 — 동봉 스냅샷은 위반 29 · 사건 20 · P0 5 (레퍼런스 v4 와 동일해야 한다)', async () => {
  const { readFileSync } = await import('node:fs');
  const facts = JSON.parse(
    readFileSync(new URL('./facts-snapshot.json', import.meta.url), 'utf8'),
  ) as Facts;
  const ev = evaluate(facts);
  /* 픽스처가 아니라 **동봉 스냅샷 위에서** 규약을 전수 검사한다 — 픽스처는 룰이 만든 값의
   * 한 갈래만 밟지만 스냅샷은 실제로 걸린 29건 전부를 준다 */
  ev.violations.forEach(assertContract);
  assert.equal(ev.violations.length, 29);
  assert.equal(ev.incidents.length, 20);
  assert.equal(ev.incidents.filter((i) => i.sev === 'P0').length, 5);
  // 뉴스 런 타임아웃 사건이 연쇄 +7 로 병합된다 (명세 §2-2의 예시 그대로)
  const news = ev.incidents.find((i) => i.root.targetId === 'news:2026-08-03T15:30');
  /* `!` 로 넘기면 실패가 다음 줄의 TypeError 로 나와 무엇이 틀렸는지 안 읽힌다 */
  assert.ok(news, '뉴스 런 사건이 사라졌다 — 사건 키 축(targetId)이 바뀌었는지 본다');
  assert.equal(news.size, 8);

  /* **런북 키 회귀 검출기.** 조회는 `${rule}.${targetId}`(shared.tsx) 인데 그걸 지키는 단언이
   * 룰 테스트에도 화면 테스트에도 없었다 — R07 의 `target` 을 사람이 읽을 문구로 다듬으면
   * 테스트는 전건 초록인 채 조치 칸만 조용히 `런북 미등록` 이 된다(리뷰가 변이로 실증).
   * 이 규약이 "target 은 라벨로 바꿔라"라는 압력을 새로 만들었으므로 그 자리에 가드를 둔다.
   *
   * ⚠️ 방향을 조심해야 한다. 계약은 **위반 → 런북**(위반이 나면 그 키로 조회된다)이지 그 역이
   * 아니다. "모든 런북 항목에 지금 살아 있는 위반이 있어야 한다"로 쓰면 아직 안 터진 상황의
   * 조치를 미리 등록하는 정상적인 사용이 거짓 실패가 된다(R12 DLQ·R17 실시간 런북 — §6-9 의
   * 큐에 있는 작업이다). 그래서 "지금 붙는 키의 집합"을 고정한다 — 29·20·5 와 같은 종의
   * 스냅샷 회귀값이다.
   *
   * **안 잡는 것**: 걸린 룰에 오타 난 키를 새로 등록하는 것(`R05.TYPO`)은 애초에 안 붙으므로
   * 집합이 안 변해 통과한다. 그걸 잡으려면 "걸린 룰의 키는 그 룰의 targetId 중 하나여야 한다"로
   * 써야 하는데, 그 형태는 **걸린 룰의 건강한 target 에 런북을 미리 등록**하는 것을 거짓 실패로
   * 만든다(`R05.LOAD_PRICE_DAILY` 류 — 실측). 가드를 두 개 쌓지 않고 하나만 두되,
   * **오탐이 없는 게 아니라 오탐의 방향을 고른 것**이다: 안 걸린 룰의 선등록은 통과시키고,
   * 걸린 위반의 런북 등록은 아래 목록 갱신을 요구한다(29건 중 21건이 `런북 미등록` 이라
   * 그쪽이 더 잦은 편집이다 — 실패는 읽히는 deepEqual diff 다). */
  const produced = new Set(ev.violations.map((v) => `${v.rule}.${v.targetId}`));
  const matched = Object.keys(facts.runbook).filter((k) => k.includes('.') && produced.has(k));
  assert.deepEqual(matched.sort(), [
    'R05.ASSEMBLE_EVENTS',
    'R05.LOAD_DOCUMENTS',
    'R07.INVESTOR_COLLECTION_KIS',
    'R08.investor_flow',
    'R11.price-explanation-realtime',
    'R16.ASSEMBLE_EVENTS',
  ]);
});

/* ── 사건 식별자(vid) — 위치가 아니라 대상 × 시점 (ALPHA-738 단계 4 선행) ──────────
 * 이 절이 지키는 의도: **정적 스냅샷이 가리고 있던 것**을 픽스처로 드러낸다. 스냅샷은 위반
 * 집합이 안 바뀌고(→ 위치 인덱스 표류를 못 보여준다), 같은 task_key 가 여러 런에 걸린 경우가
 * 없고(→ 키 충돌을 못 보여준다), minute 축이 아예 없다. 실 응답에서는 날마다 반복된다. */

test('vid — 앞 위반이 해소돼도 뒤 위반의 사건 식별자는 그대로다', () => {
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'A', task_outcome: 'FAILED' }),
    task({ task_key: 'B', task_outcome: 'FAILED' }),
  ];
  const before = hits(f, 'R05').find((v) => v.targetId === 'B')!.vid;

  // A 만 해소됐다 — B 에 관한 사실은 아무것도 안 변했다
  f.tasks = [task({ task_key: 'B', task_outcome: 'FAILED' })];
  const after = hits(f, 'R05')[0].vid;

  assert.equal(after, before, '앞 위반이 사라지자 B 의 딥링크가 다른 사건을 가리키게 됐다');
});

test('vid — 같은 작업 키가 두 런에 걸려도 사건이 갈린다 (런까지 실어야 갈린다)', () => {
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'LOAD_DOCUMENTS', run_id: 'news:2026-08-03T15:00', task_outcome: 'FAILED' }),
    task({ task_key: 'LOAD_DOCUMENTS', run_id: 'news:2026-08-03T15:30', task_outcome: 'FAILED' }),
  ];
  /* 개수만 세면 순번으로 갈려도 통과한다 — 무엇으로 갈렸는지를 값으로 못박는다 */
  assert.deepEqual(
    hits(f, 'R05').map((v) => v.vid),
    [
      'R05:LOAD_DOCUMENTS@news:2026-08-03T15:00',
      'R05:LOAD_DOCUMENTS@news:2026-08-03T15:30',
    ],
  );
});

test('vid 충돌 — 대상 축이 위반을 못 가르면 조용히 넘기지 않고 죽는다', () => {
  /* 도달 경로를 **제약 없는 축**에서 고른다. `tasks` 의 중복은 원장이 막는다
   * (`uq_ops_expected_task_run_key UNIQUE (pipeline_run_id, task_key)`) — 거기서 재현하면
   * DB 가 이미 막는 것을 테스트하는 셈이다. `outputs` 는 엔드포인트가 조립하는 축이라
   * 유일성을 보증하는 제약이 없다(`datasets`·`chain.stages` 도 같다). */
  const f = emptyFacts();
  f.outputs = [
    { id: 'o.pub', label: '게시', today: 10, base: 100, unit: '건' },
    { id: 'o.pub', label: '게시(중복 행)', today: 20, base: 100, unit: '건' },
  ];
  /* 뒤엣것을 버리거나 번호를 붙여 비키면 위치 인덱스가 이름만 바꿔 되살아난다. */
  assert.throws(() => evaluate(f, NOW), /사건 식별자 충돌: R13:o\.pub/);
});

test('vid 충돌 — 런이 빈 문자열이면 범위가 없는 것으로 읽혀 병합된다 (와이어의 falsy 함정)', () => {
  /* `TaskFact.run_id` 는 `string` 필수라 `''` 가 타입상 합법이다. 범위가 `''` 면
   * `scope ?? runId` 는 값을 주지만 `vid` 조립의 truthy 검사가 '없음'으로 읽어 두 위반이
   * 같은 키가 된다 — 가드와 사용처가 falsy 를 다르게 읽는 그 자리다. 삼키지 않고 죽어야 한다. */
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'T', run_id: '', task_outcome: 'FAILED' }),
    task({ task_key: 'T', run_id: '', task_outcome: 'PENDING' }),
  ];
  assert.throws(() => evaluate(f, NOW), /사건 식별자 충돌: R05:T/);
});

test('R17 — 같은 데이터셋이라도 벤더가 다르면 다른 세션이다 (sourceGroup 을 버리면 겹친다)', () => {
  const f = withMinute([
    session({ dataset: 'news_minute', sourceGroup: 'bigkinds', leaseExpired: true }),
    session({ dataset: 'news_minute', sourceGroup: 'naver', leaseExpired: true }),
  ]);
  assert.deepEqual(
    hits(f, 'R17').map((v) => v.vid),
    ['R17:news_minute/bigkinds@2026-08-03', 'R17:news_minute/naver@2026-08-03'],
  );
});

test('R17 — 런북 키에는 날짜가 없다 (날짜가 섞이면 어떤 조치도 등록 못 한다)', () => {
  /* 런북은 "이 대상이 고장나면 이렇게 조치한다"라 날짜와 무관하다. 세션 identity 의 날짜를
   * `targetId` 에 넣으면 키(`${rule}.${targetId}`)가 매일 달라져 **영구히 매칭 불가**가 된다.
   * 그래서 날짜는 `scope`(사건 키 전용)로 가고 `targetId` 는 대상만 담는다. */
  const f = withMinute([session({ dataset: 'news_minute', sourceGroup: 'bigkinds', leaseExpired: true })]);
  const v = hits(f, 'R17')[0];
  assert.equal(v.targetId, 'news_minute/bigkinds');
  assert.doesNotMatch(v.targetId, /\d{4}-\d{2}-\d{2}/, 'targetId 에 날짜가 들어갔다 — 런북 키가 매일 바뀐다');
  /* 그리고 그 키로 등록한 런북이 실제로 잡혀야 한다 — 부재 검사만으로는 폴백에 가려진다 */
  f.runbook = { 'R17.news_minute/bigkinds': { cmd: 'restart-session bigkinds' } };
  assert.equal(runbookOf(f, hits(f, 'R17')[0])?.cmd, 'restart-session bigkinds');
});

test('R17 — 사건 키는 날짜에 고정된다 (어제 공유한 링크가 오늘 세션을 열면 안 된다)', () => {
  const d1 = withMinute([session({ leaseExpired: true })]);
  const d2 = withMinute([session({ leaseExpired: true })]);
  d2.minute!.date = '2026-08-04';
  assert.notEqual(hits(d1, 'R17')[0].vid, hits(d2, 'R17')[0].vid);
});
