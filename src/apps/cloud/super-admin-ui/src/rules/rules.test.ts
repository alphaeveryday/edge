/* 규칙 하나당 위반/비위반 픽스처 각 1 + 경계 케이스 (ALPHA-738, 명세 §2-1).
 *
 * 실행: node --test src/rules/   (Node 23.6+ 네이티브 TS)
 *
 * 테스트가 지키는 의도: 규칙의 조건이 명세 §2 표와 다르게 느슨해지거나(거짓 양성)
 * 관대해지면(거짓 음성 — 원장이 관대해지는 방향이 상습 오류다) 여기서 깨져야 한다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildReport, evaluate } from './evaluate.ts';
import type { Facts, RunFact, TaskFact } from './types.ts';

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

function hits(f: Facts, rule: string) {
  return evaluate(f, NOW).violations.filter((v) => v.rule === rule);
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
  assert.deepEqual(
    v.map((x) => [x.target, x.metric]),
    [
      ['batch:c.run', 4],
      ['intraday:c.obs', 65],
    ],
  );
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
  assert.deepEqual(v.map((x) => [x.target, x.metric]), [['half', '-50%']]);
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
  assert.equal(v[0].sev, 'P0');
  assert.equal(v[1].sev, 'P2');
  assert.equal(v[1].seed, true);
  f.boundary = { published_without_delivery: 0, delivery_now_nonpublished: 0 };
  assert.equal(hits(f, 'R14').length, 0);
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
  assert.deepEqual(v[0].list, ['A', 'B']);
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

test('R16 경계 — 정책 필드가 전무하면 evaluated:false (분모 없이 2/3 표기 금지)', () => {
  const f = emptyFacts();
  f.tasks = [task({ task_key: 'no-policy', task_outcome: 'FAILED', attempts: 5, max_retries: null })];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R16')!;
  assert.equal(rr.evaluated, false);
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
  assert.equal(ev.violations.length, 29);
  assert.equal(ev.incidents.length, 20);
  assert.equal(ev.incidents.filter((i) => i.sev === 'P0').length, 5);
  // 뉴스 런 타임아웃 사건이 연쇄 +7 로 병합된다 (명세 §2-2의 예시 그대로)
  const news = ev.incidents.find((i) => i.root.target === 'news:2026-08-03T15:30')!;
  assert.equal(news.size, 8);
});
