/* 규칙 하나당 위반/비위반 픽스처 각 1 + 경계 케이스 (ALPHA-738, 명세 §2-1).
 *
 * 실행: `pnpm test` (super-admin-ui 패키지 루트, Node 23.6+ 네이티브 TS).
 *   ⚠️ `node --test src/rules/` 는 안 된다 — node 가 그 경로를 모듈 파일로 열어 MODULE_NOT_FOUND 다.
 *   실제 명령은 package.json 의 `test` 스크립트(테스트 파일 glob)다.
 *
 * 테스트가 지키는 의도: 규칙의 조건이 명세 §2 표와 다르게 느슨해지거나(거짓 양성)
 * 관대해지면(거짓 음성 — 원장이 관대해지는 방향이 상습 오류다) 여기서 깨져야 한다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildReport, evaluate, ruleOfVid, runbookOf } from './evaluate.ts';
import { EDGES, RULES } from './rules.ts';
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

/**
 * **옵셔널 축을 전부 뺀** 사실 — 실 응답의 최소 형상이다(계약 §「무엇이 실제로 나가는가」:
 * 서버가 `chain`·`queues`·`etf_ledger`·`runbook`·`meta.aws` 를 안 보낸다).
 *
 * 빼는 목록을 각 테스트가 손으로 적으면 축이 하나 늘 때 한 곳만 갱신된다 — 이 트랙에서 반복된
 * 모양이라 자리를 하나로 둔다. `Facts` 에 옵셔널 축이 새로 생기면 여기에 더해라.
 */
function bareFacts(): Facts {
  const f = emptyFacts();
  delete f.chain;
  delete f.queues;
  delete f.etf_ledger;
  delete f.minute;
  delete f.runbook;
  delete f.meta.aws;
  return f;
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

test('R02 — kind 부재를 "정규 런"으로 단정하지 않는다 (모름이 가장 강한 주장을 기본값으로 잡았다)', () => {
  /* `kind` 는 계측이 없어 실 응답에서 통째로 빠진다. 부재를 정규로 접으면 수동 런의 원장 공백이
   * '정규 런 미귀결'로 서고, 운영자가 안 봐도 되는 것을 본다. 넷째 값이 있어야 한다. */
  const f = emptyFacts();
  const late = { deadline: '2026-08-03T16:00:00+09:00' };
  f.runs = [
    run({ id: 'unknown', kind: undefined, ...late }),
    run({ id: 'manual', kind: 'manual', ...late }),
    run({ id: 'sched', kind: 'scheduled', ...late }),
  ];
  f.runs.push(run({ id: 'bf', kind: 'backfill', ...late }));
  const why = new Map(hits(f, 'R02').map((v) => [v.target, v.why]));
  assert.match(why.get('unknown')!, /미기록/, '부재를 아는 값으로 단정했다');
  assert.doesNotMatch(why.get('unknown')!, /정규 런/);
  assert.match(why.get('manual')!, /수동 런/);
  assert.match(why.get('sched')!, /정규 런/);
  /* 네 값이 **넷 다** 달라야 한다 — 하나라도 뭉치면 그 종류가 남의 이름으로 그려진다 */
  assert.match(why.get('bf')!, /백필 런/);
  assert.equal(new Set([...why.values()].map((w) => w.split(' ·')[0])).size, 4);
});

test('R03 — AWS 상태를 가진 런이 하나도 없으면 evaluated:false (한 표면을 못 본 것이지 일치가 아니다)', () => {
  /* 실 응답은 SFN 조회가 붙기 전까지 이 축이 통째로 없다. canRun 이 없으면 R03 이 매일
   * "제어면과 원장이 일치한다"를 주장한다 — 조회 배선 부재가 정상으로 그려진다. */
  const f = emptyFacts();
  f.runs = [run({ id: 'no-aws', ledger_status: 'SUCCEEDED' })];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R03')!;
  assert.equal(rr.evaluated, false);
  assert.equal(rr.notRun, 'axis');
  /* 빈 문자열도 관측이 아니다 — `run()` 이 truthy 로 거르므로 `canRun` 이 `!= null` 이면
   * '평가됨 · 위반 0'("두 표면이 일치한다")이 선다. 두 축이 갈리면 정확히 그 모양이다. */
  f.runs = [run({ id: 'blank-aws', aws_status: '', ledger_status: 'SUCCEEDED' })];
  assert.equal(buildReport(f, NOW).rules.find((r) => r.id === 'R03')!.evaluated, false);
  /* 한 런이라도 관측되면 돈다 — 부분 관측의 크기는 다른 축(meta.awsUnobservedRuns)이 답한다.
   * 다만 **몇 런을 봤는지는 밝혀야 한다**: 안 밝히면 1/40 관측이 "두 표면이 일치한다"로 읽힌다 */
  f.runs.push(run({ id: 'aws', aws_status: 'SUCCEEDED', ledger_status: 'SUCCEEDED' }));
  const partial = buildReport(f, NOW).rules.find((r) => r.id === 'R03')!;
  assert.equal(partial.evaluated, true);
  assert.match(partial.note ?? '', /관측한 런 1\/2/, '부분 관측이 침묵했다');

  /* 전건 관측이면 밝힐 게 없다 — 늘 붙는 주석은 아무도 안 읽는다 */
  f.runs = [run({ id: 'aws', aws_status: 'SUCCEEDED', ledger_status: 'SUCCEEDED' })];
  assert.equal(buildReport(f, NOW).rules.find((r) => r.id === 'R03')!.note, null);
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

test('R04 경계 — 원장 공백+AWS 실패에서 배제는 아는 것(수동·백필)으로만 한다', () => {
  /* ⚠️ 예전에는 `kind === 'scheduled'` 를 **요구**했다. `kind` 는 계측이 없어 실 응답에서 빠지므로
   * 그 분기가 영구 사문화되고, 원장 공백 + AWS 실패가 통째로 안 잡힌다 — P0 거짓 음성이다.
   * 모름은 배제 근거가 아니다: 배제는 수동·백필로 **확인된** 런만. */
  const f = emptyFacts();
  f.runs = [
    run({ id: 'sched', kind: 'scheduled', aws_status: 'FAILED' }),
    run({ id: 'unknown', kind: undefined, aws_status: 'FAILED' }),
    run({ id: 'man', kind: 'manual', aws_status: 'FAILED' }),
    run({ id: 'bf', kind: 'backfill', aws_status: 'TIMED_OUT' }),
  ];
  /* 종료 실패 어휘는 원장과 AWS 가 **같아야** 한다 — AWS 쪽에만 `ABORTED` 가 빠져 있어서
   * 원장 공백 + SFN ABORTED(운영자가 멈췄는데 투영이 안 됨)가 통째로 안 잡혔다. */
  f.runs.push(run({ id: 'aborted', aws_status: 'ABORTED' }));
  const v = hits(f, 'R04');
  assert.deepEqual(v.map((x) => x.target), ['sched', 'unknown', 'aborted']);
  /* 잡되 **종류를 못 읽었다는 것도 같이 말한다** — 안 그러면 수동 런일 수 있는 것이 확정 실패로 선다 */
  assert.match(v[1].why, /미기록/, '모름을 잡아 놓고 그 사실을 안 밝혔다');
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

test('R06 데이터 부분 유실 — 판정은 data_status 가 내린다 (건수는 규모지 조건이 아니다)', () => {
  /* 이 테스트가 `failed_records` 를 **조건**으로 고정하고 있었다("유실 0이면 아님"). 그래서
   * 스텝이 스스로 INCOMPLETE 를 선언했는데 건수를 안 남긴 작업이 위반 0건으로 접혔고, 테스트는
   * 초록이었다 — 내 테스트가 결함을 고정한 자리다. 계약상 `failedRecords` 는 nullable 이고
   * `data_status` 와의 결합 제약이 없으므로, 규모의 부재를 유실의 부재로 읽으면 안 된다.
   *
   * 규모가 없을 때 무엇이 나오는지까지 재야 한다 — 위반만 세면 `metric: undefined` 같은
   * 망가진 형태가 통과한다(필드 규약: 양이 아니면 metric 은 null 이고 판정은 state 로 간다). */
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'lost', data_status: 'INCOMPLETE', failed_records: 12 }),
    task({ task_key: 'flagged-only', data_status: 'INCOMPLETE', failed_records: 0 }),
    task({ task_key: 'no-count', data_status: 'INVALID', failed_records: null }),
    task({ task_key: 'valid', data_status: 'VALID', failed_records: 3 }),
  ];
  const v = hits(f, 'R06');
  assert.deepEqual(v.map((x) => x.target), ['lost', 'flagged-only', 'no-count']);
  /* ⚠️ `flagged-only`(failed_records: 0)의 metric 이 **0이 아니라 null** 인 것이 이 단언의 핵심이다.
   * 생산자는 `received < expected` 만으로도 INCOMPLETE 를 내므로 이 카운터가 0인 INCOMPLETE 는
   * 정상 형상이고, 유실은 완전성 축(R07)에 있다. `metric: 0 · unit: '건'` 으로 그리면
   * "유실 0건인 부분 유실"이라는 자기모순이 표에 선다 — 규모로 쓸 수 있는 건 양수뿐이다. */
  assert.deepEqual(v.map((x) => x.metric), [12, null, null]);
  /* 규모가 없는 위반만 state 를 갖는다 — 둘 다 채우면 화면이 어느 쪽을 그릴지 추측하게 된다 */
  assert.deepEqual(v.map((x) => x.state ?? null), [null, 'INCOMPLETE', 'INVALID']);
  assert.deepEqual(v.map((x) => x.unit ?? null), ['건', null, null]);
  /* 0 과 부재는 사유가 다르다 — 한 문장으로 접으면 "규모를 안 남겼다"가 0건에도 붙는다 */
  assert.match(v[1].why, /0이다/);
  assert.match(v[2].why, /없다/);
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

test('R07 경계 — received=null(집계 없음)은 실측 0이 아니다 (분모 전체가 결손으로 서던 자리)', () => {
  /* `received ?? 0` 이었다. 분모만 배선되고 분자가 아직 안 오는 정상 작업이 **분모 크기 그대로**
   * P0 결손으로 섰고, 그 수는 실측처럼 보였다 — 부재를 값으로 위조하는, 이 모듈이 없애려는 바로
   * 그 혼동이다. 판정에서 빼는 것만으로는 부족하다: 안 밝히면 그 작업의 결손이 "위반 0건"에
   * 흡수돼 보이므로 `note` 가 어느 작업이 빠졌는지 이름을 대야 한다. 둘 다 재지 않으면
   * 침묵을 침묵으로 바꾼 것뿐이다. */
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'numerator-missing', completeness_expected: 100, completeness_received: null }),
    task({ task_key: 'short', completeness_expected: 10, completeness_received: 7 }),
  ];
  const v = hits(f, 'R07');
  assert.deepEqual(v.map((x) => x.target), ['short'], 'null 분자는 결손 100건이 아니다');
  assert.equal(v[0].metric, 3);
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R07')!;
  assert.equal(rr.evaluated, true);
  assert.match(rr.note ?? '', /numerator-missing/, '판정에서 빠진 작업을 note 가 이름으로 밝힌다');
});

test('R05 note — 계획 상태를 모르는 필수 작업을 조용히 DUE 로 세지 않는다', () => {
  /* 필터는 SKIPPED 만 뺀다(모름은 배제 근거가 아니다 — P0 거짓 음성을 피하는 쪽). 그 대가로
   * **plan 축을 모르는 작업이 DUE 로 가정돼 판정된다** — 몇 건이 그런지 안 밝히면 축이 통째로
   * 빠진 응답에서도 아무도 그 사실을 모른다. */
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'known', required: true, plan_status: 'DUE', task_outcome: 'FAILED' }),
    task({ task_key: 'unknown-plan', required: true, plan_status: 'BROKEN', task_outcome: null }),
  ];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R05')!;
  assert.match(rr.note ?? '', /계획 상태를 모르는 필수 작업 1\/2/);
});

test('R08 경계 — 판정 가능한 데이터셋이 하나도 없으면 "전부 신선하다"가 아니라 못 돎이다', () => {
  /* `canRun` 이 `contract && actual_as_of` 만 보고 `run()` 이 `expected_as_of`·창 계약까지
   * 요구해 **둘이 갈렸다**. 갈리면 판정을 하나도 못 했는데 `평가됨 · 위반 0`("전부 신선")이 선다 —
   * R13 에서 두 번 겪고 `judgeable` 로 묶은 그 갈림이다. 두 형상 모두 못 돎이어야 한다. */
  for (const [label, ds] of [
    ['창 계약뿐', { id: 'w', contract: true, window_contract: true, actual_as_of: '2026-08-01', expected_as_of: '2026-08-02' }],
    ['기대일 없음', { id: 'n', contract: true, actual_as_of: '2026-08-01' }],
  ] as const) {
    const f = emptyFacts();
    f.datasets = [ds];
    const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R08')!;
    assert.equal(rr.evaluated, false, `${label}: 판정 가능한 게 없는데 평가됨으로 섰다`);
  }
});

test('R04 근거 — 원장이 빈 AWS 단독 실패에 빈 컬럼을 근거로 대지 않는다', () => {
  /* 근거는 **어느 표면이 실패를 말했는가**로 갈린다. 원장이 비었는데
   * `orchestration_status` 를 근거로 대면 운영자가 빈 컬럼을 보러 간다. */
  const f = emptyFacts();
  f.runs = [
    run({ id: 'ledger-said', ledger_status: 'FAILED' }),
    run({ id: 'aws-only', ledger_status: null, aws_status: 'FAILED' }),
  ];
  const v = hits(f, 'R04');
  assert.deepEqual(v.map((x) => x.target), ['ledger-said', 'aws-only']);
  assert.match(v[0].evidence, /orchestration_status/);
  assert.match(v[1].evidence, /stepfunctions/);
  assert.doesNotMatch(v[1].evidence, /orchestration_status/);
});

test('R07 경계 — 수가 아닌 분모는 강제 변환되지 않는다 (문자열 "100" 이 실측 결손이 되던 자리)', () => {
  /* 검증 안 된 JSON 의 `"100"` 은 `!= null` 을 통과하고 비교·뺄셈에서 100 으로 강제 변환돼
   * **실측 P0 결손 1건**처럼 보고됐다. note 도 그걸 배선된 분모로 셌다. */
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'coerced', completeness_expected: '100' as unknown as number, completeness_received: 99 }),
  ];
  assert.deepEqual(hits(f, 'R07').map((v) => v.target), []);
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R07')!;
  assert.match(rr.note ?? '', /분모 배선 작업 0\/1/, '수가 아닌 분모를 배선된 것으로 셌다');
});

test('R13 못 돎 사유 — 산출 축이 비었을 때도 사유가 참이다', () => {
  /* `dep` 은 canRun=false 의 **모든 형상**에서 참이어야 하는데, `outputs: []` 이면 "셈으로
   * 성립하지 않는 값"이 존재하지도 않는다. 문장이 형상 하나를 놓칠 때마다 거짓말이 된다. */
  const f = emptyFacts();
  f.outputs = [];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!;
  assert.equal(rr.evaluated, false);
  assert.match(rr.note ?? '', /산출이 없거나/, `산출 부재 형상을 사유가 안 덮는다: ${rr.note}`);
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

test('R10 경계 — 비교할 점이 두 개 없으면 evaluated:false (인접 감소라는 물음이 성립하지 않는다)', () => {
  /* 실 응답에는 체인 축의 소스가 아예 없다. canRun 이 없으면 R10 이 "손실 없음"을 주장한다. */
  assert.equal(buildReport(emptyFacts(), NOW).rules.find((r) => r.id === 'R10')!.evaluated, false);

  /* 점이 하나뿐인 상태도 못 돎이다 — 인접 쌍이 없다. blind 는 값이 아니라 빠짐이라 점이 아니다 */
  const one = emptyFacts();
  one.chain = {
    feeds: [],
    stages: [
      { id: 'a', label: 'A', batch: 10, src: 's' },
      { id: 'blind', label: '소비', blind: true, batch: 5, src: 's' },
    ],
  };
  assert.equal(buildReport(one, NOW).rules.find((r) => r.id === 'R10')!.evaluated, false);

  /* `null` 도 점이 아니다 — 단계가 있어도 값이 없으면 비교할 게 없다.
   * `!= null` 을 `!== undefined` 로 느슨하게 하면 여기서만 깨진다 */
  const nulls = emptyFacts();
  nulls.chain = {
    feeds: [],
    stages: [
      { id: 'a', label: 'A', batch: null, src: 's' },
      { id: 'b', label: 'B', batch: null, src: 's' },
    ],
  };
  assert.equal(buildReport(nulls, NOW).rules.find((r) => r.id === 'R10')!.evaluated, false);

  /* 한 레인에 두 점이 서면 돈다 — 다른 레인이 비어도 마찬가지다 */
  const two = emptyFacts();
  two.chain = {
    feeds: [],
    stages: [
      { id: 'a', label: 'A', batch: 10, src: 's' },
      { id: 'b', label: 'B', batch: 10, src: 's' },
    ],
  };
  assert.equal(buildReport(two, NOW).rules.find((r) => r.id === 'R10')!.evaluated, true);
});

test('R10 — canRun 이 세는 피드는 `run()` 이 비교 시작점으로 쓰는 그 피드다 (축이 갈리면 P0 가 버려진다)', () => {
  /* `feeds[0]`=배치 · `feeds[1]`=장중이다. `canRun` 이 이 대응을 뒤집으면 배치 레인의 점이
   * 1개로 세어져 **평가됨→못 돎** 이 되고, `run()` 이 낸 P0 손실 위반이 통째로 버려진다.
   * 한 글자 실수인데 규칙 표에는 "못 돎"이라고만 서서 아무도 사라진 P0 를 못 찾는다.
   * 갈래를 **비대칭**으로 만들어야 잡힌다 — 둘 다 채우면 뒤집어도 통과한다. */
  const f = emptyFacts();
  f.chain = {
    feeds: [{ id: 'fb', label: '배치 트리거', v: 100, unit: 'ETF', src: 't' }], // 장중 피드 없음
    stages: [{ id: 'c.run', label: '런', batch: 90, src: 's' }], // 장중 값 없음
  };
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R10')!;
  assert.equal(rr.evaluated, true, 'canRun 이 배치 피드를 못 셌다 — P0 위반이 버려진다');
  const v = hits(f, 'R10');
  assert.deepEqual(v.map((x) => [x.targetId, x.metric]), [['batch:c.run', 10]]);
});

test('R10 — 갈래를 못 가르는 못 돎이라 무엇을 비교했는지 밝힌다 (위반 0건이 "손실 없음"이 아니다)', () => {
  /* `canRun` 은 규칙 단위다. 한 갈래만 도착한 응답(계약상 그 형상이 먼저 온다)에서 나머지
   * 갈래는 점이 0개인데 "평가됨 · 위반 0"에 묻힌다. `&&` 로 조이면 반대로 볼 수 있는 P0 를
   * 버리므로, 판정은 `||` 로 두고 그 사실을 note 로 낸다. */
  const partial = emptyFacts();
  partial.chain = {
    feeds: [],
    stages: [
      { id: 'a', label: 'A', batch: 10, src: 's' },
      { id: 'b', label: 'B', batch: 10, src: 's' },
    ],
  };
  const note = buildReport(partial, NOW).rules.find((r) => r.id === 'R10')!.note;
  assert.match(note ?? '', /배치/);
  assert.match(note ?? '', /나머지 갈래는 점이 2개 미만/);

  /* 두 갈래 다 비교했으면 밝힐 게 없다 — 늘 붙는 주석은 아무도 안 읽는다 */
  const both = emptyFacts();
  both.chain = {
    feeds: [],
    stages: [
      { id: 'a', label: 'A', batch: 10, intraday: 5, src: 's' },
      { id: 'b', label: 'B', batch: 10, intraday: 5, src: 's' },
    ],
  };
  assert.equal(buildReport(both, NOW).rules.find((r) => r.id === 'R10')!.note, null);
});

test('합성 대상 축은 세션만이 아니다 — 체인 단계 id 가 비어도 못 돎이다 (가드가 둘이면 한쪽만 고쳐진다)', () => {
  /* `${src}:${s.id}` 는 `${dataset}/${sourceGroup}` 과 **같은 합성**이다. 처음 조각 가드를
   * 넣을 때 세션 축만 막고 여기를 놓쳤다(리뷰가 잡았다) — 그러면 `batch:` 라는 정상처럼
   * 보이는 사건 키가 나가고, 딥링크·런북 키가 그걸 문다. 두 자리가 같은 함수를 탄다. */
  const f = emptyFacts();
  f.chain = {
    feeds: [],
    stages: [
      { id: 'ok', label: 'A', batch: 10, src: 's' },
      { id: '', label: 'B', batch: 5, src: 's' }, // 단계 id 가 빈 응답
    ],
  };
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R10')!;
  assert.equal(rr.evaluated, false);
  assert.equal(rr.notRun, 'identity', '계측 공백이 아니라 응답의 계약 위반이다');
  assert.equal(rr.violations, 0);
});

test('R10 경계 — 손상된 피드 값은 비교 기준이 못 된다 (거짓 P0 체인 손실이 나던 자리)', () => {
  /* `chainPoints`(canRun)는 유한수만 점으로 세는데 `run()` 은 피드 값을 검사 없이 `prev` 로
   * 실었다 — **단계 쪽만 막고 피드 쪽을 빠뜨린** 갈림이다. 검증 안 된 JSON 의 `"20"`·`Infinity`
   * 가 기준이 되면 `v < prev` 가 강제 변환·무한 비교로 통과해 P0 체인 손실이 지어내진다.
   * `Infinity` 는 `metric: Infinity` 라는 렌더 불가 값까지 낸다. */
  for (const bad of ['20' as unknown as number, Number.POSITIVE_INFINITY, Number.NaN]) {
    const f = emptyFacts();
    f.chain = {
      feeds: [
        { id: 'fb', label: '배치 트리거', v: bad, unit: 'ETF', src: 't' },
        { id: 'fi', label: '장중 트리거', v: 10, unit: '건', src: 't' },
      ],
      stages: [
        { id: 'c.obs', label: '관측', batch: 5, intraday: 10, src: 's' },
        { id: 'c.run', label: '런', batch: 5, intraday: 10, src: 's' },
      ],
    };
    const v = hits(f, 'R10');
    assert.deepEqual(v, [], `손상 피드(${String(bad)})가 체인 손실을 지어냈다: ${JSON.stringify(v.map((x) => [x.targetId, x.metric]))}`);
  }
  /* 멀쩡한 피드는 그대로 잡혀야 한다 — 가드가 규칙을 통째로 죽이면 그건 고친 게 아니다 */
  const ok = emptyFacts();
  ok.chain = {
    feeds: [
      { id: 'fb', label: '배치 트리거', v: 20, unit: 'ETF', src: 't' },
      { id: 'fi', label: '장중 트리거', v: 10, unit: '건', src: 't' },
    ],
    stages: [{ id: 'c.obs', label: '관측', batch: 5, intraday: 10, src: 's' }],
  };
  assert.deepEqual(hits(ok, 'R10').map((x) => x.metric), [15], '멀쩡한 피드까지 버렸다');
});

test('R11 소비자 부재 — 대기>0·in-flight 0·구독자 0 전부 만족할 때만', () => {
  const f = emptyFacts();
  f.queues = [
    { name: 'orphan', visible: 65, in_flight: 0, dlq: 0, subscribers: [] },
    { name: 'consuming', visible: 65, in_flight: 3, dlq: 0, subscribers: [] },
    { name: 'subscribed', visible: 65, in_flight: 0, dlq: 0, subscribers: ['svc'] },
    { name: 'idle', visible: 0, in_flight: 0, dlq: 0, subscribers: [] },
    /* 매핑이 **일부 큐에만** 붙은 응답 — 계약상 정상 형상이다. `(q.subscribers ?? []).length === 0`
     * 이던 때는 이 큐가 '소비자 부재' P0 로 섰다: 아무도 안 센 것을 없다고 단정한 것이다.
     * 이 줄이 없으면 그 접기를 되살려도 전건 초록이다(변이로 확인). */
    { name: 'unmapped', visible: 65, in_flight: 0, dlq: 0 },
  ];
  assert.deepEqual(hits(f, 'R11').map((v) => v.target), ['orphan'], '미계측 큐를 구독자 0으로 단정하지 않는다');
  /* 판정에서 빠졌다는 사실이 어딘가에는 남아야 한다 — 안 그러면 침묵을 침묵으로 바꾼 것뿐이다 */
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R11')!;
  assert.equal(rr.evaluated, true);
  assert.match(rr.note ?? '', /4\/5/, '몇 큐를 실제로 봤는지 note 가 밝힌다');
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
    /* 경계 정확히 ±25% — 계약이 "이상"이라 **걸려야 한다**. 이 두 줄이 없으면 `>= 0.25` 를
     * `> 0.25` 로 바꿔도 전건 초록이라, 임계값이 조용히 한 칸 옮겨간다. 양쪽 부호를 다 둔다. */
    { id: 'edge-down', label: '경계 감소', today: 75, base: 100, unit: '건' }, // -25%
    { id: 'edge-up', label: '경계 증가', today: 125, base: 100, unit: '건' }, // +25%
    { id: 'nobase', label: '신규', today: 5, base: null, unit: '건' },
  ];
  const v = hits(f, 'R13');
  /* 편차율은 **양이다** — 문자열 `'-50%'` 로 두면 정렬(크기순)과 숫자 열이 이 값을 못 쓴다.
   * 대상은 라벨(사람이 읽을 것), 키는 산출 id(`half`) 로 갈린다. */
  assert.deepEqual(
    v.map((x) => [x.targetId, x.metric, x.unit]).sort(),
    [['edge-down', -25, '%'], ['edge-up', 25, '%'], ['half', -50, '%']],
  );
  assert.ok(!v.some((x) => x.targetId === 'near'), '-24% 는 안 걸린다');
});

test('R13 경계 — 셈으로 성립 안 하는 값은 관측이 아니다 (음수 count 를 정상 인증하지 않는다)', () => {
  /* 유한성만 검사하던 때는 `base:-100·today:-100` 이 `평가됨 · 위반 0`("평소와 같다")으로 인증됐고,
   * `base:100·today:-1` 은 −101% 라는 없는 편차를 P1 로 냈다. 산출 다섯은 전부 count 라 음수가
   * 성립하지 않는다 — 손상은 판정에서 빼고 `note` 가 밝힌다. */
  const f = emptyFacts();
  f.outputs = [
    { id: 'neg-both', label: '둘 다 음수', today: -100, base: -100, unit: '건' },
    { id: 'neg-today', label: '오늘만 음수', today: -1, base: 100, unit: '건' },
    /* ⚠️ 기준만 음수이고 오늘 값은 멀쩡한 경우가 **`base > 0` 가드의 유일한 증인**이다.
     * 둘 다 음수인 행은 `today >= 0` 쪽에서도 걸러져, 기준 가드를 `!== 0` 으로 되돌려도
     * 안 잡힌다(변이로 확인). 여기서 음의 분모는 −150% 라는 없는 편차를 만든다. */
    { id: 'neg-base', label: '기준만 음수', today: 50, base: -100, unit: '건' },
    { id: 'real', label: '진짜 감소', today: 50, base: 100, unit: '건' },
  ];
  assert.deepEqual(hits(f, 'R13').map((v) => v.targetId), ['real'], '손상값이 편차로 서면 안 된다');
  /* 라벨 두 개를 손으로 대조하는 대신 **불변식**을 잰다: 판정에서 빠진 산출은 하나도 빠짐없이
   * note 에 이름이 나와야 한다. 갈래를 나열하는 방식이 새 가드를 더할 때마다 구멍을 냈다. */
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!;
  const judged = hits(f, 'R13').map((v) => v.targetId);
  for (const o of f.outputs) {
    if (judged.includes(o.id)) continue;
    assert.match(rr.note ?? '', new RegExp(o.label), `판정에서 빠졌는데 note 에 없다: ${o.label}`);
  }
});

test('R13 note — 관측된 0 과 표본 부재를 같은 사유로 접지 않는다', () => {
  /* 이름이 note 에 뜨는 것만 재면 **사유를 합치는 변이가 안 잡힌다**(확인함) — 그런데 이 둘을
   * 가르는 것이 이 note 의 존재 이유다. 서버 `median()` 은 표본이 전부 0이면 `0.0` 을 주므로
   * `base: 0` 은 관측이고, `base: null` 만 미관측이다. 사유 문장이 달라야 한다. */
  const f = emptyFacts();
  f.outputs = [
    { id: 'zero', label: '평소0', today: 5, base: 0, unit: '건' },
    { id: 'nosample', label: '표본없음', today: 5, base: null, unit: '건' },
    { id: 'real', label: '진짜', today: 50, base: 100, unit: '건' },
  ];
  const note = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!.note ?? '';
  const reasonOf = (label: string) =>
    note.split(' · ').find((seg) => seg.includes(label)) ?? '';
  assert.notEqual(reasonOf('평소0'), '', '평소0 이 note 에 없다');
  assert.notEqual(reasonOf('표본없음'), '', '표본없음 이 note 에 없다');
  assert.notEqual(
    reasonOf('평소0'),
    reasonOf('표본없음'),
    '관측된 0 과 표본 부재가 같은 사유로 접혔다',
  );
  /* "서로 다르기만" 재면 한쪽 문장을 **다른 거짓 문장**으로 바꿔도 통과한다(확인함) —
   * 사유가 실제로 무엇을 말하는지까지 재야 그 변이가 죽는다. */
  assert.match(reasonOf('평소0'), /관측된 0/);
  /* ⚠️ `base: null` 의 사유는 **셋**이고 와이어는 하나로 온다(표본 부재·장 안 서는 날·그 날이
   * 안 끝남 — ALPHA-946). 그래서 이 사유는 갈래를 **나열**하고 어느 하나를 단정하지 않는다.
   * `/표본 없음/` 으로 못 박고 있었는데, 그 문면이면 지난 주말 조회에서 표본이 멀쩡한
   * `o.pub`·`o.trig` 에 거짓 사유가 붙는다. 재는 것은 "기준이 없다고 말하되 원인을 지목하지
   * 않는가" 다. */
  assert.match(reasonOf('표본없음'), /기준이 없다/);
  /* 재는 것은 **나열했는가** 다. "표본 탓으로 단정하지 마라"를 부정형 정규식으로 썼더니
   * 무관한 어구가 섞이기만 해도 빠져나갔다(`기준 표본 부재 중 하나` 가 통과 — 확인함).
   * 서버가 비우는 사유 셋을 **이름으로** 요구하면 어느 하나로 좁히는 변이가 전부 죽는다.
   *
   * ⚠️ **세그먼트가 아니라 사유만 잰다.** `reasonOf` 는 `<라벨> — <사유>` 통째를 주는데,
   * 픽스처 라벨이 `표본없음` 이라 `/표본/` 은 **라벨에 걸려** 늘 통과했다(변이로 발견 —
   * 표본 갈래를 지운 문장이 살아남았다). 단언이 재려는 것 밖의 글자에 걸리면 그 단언은 없다. */
  const causeOf = (label: string) => reasonOf(label).split('—').slice(1).join('—');
  for (const cause of [/표본 부재/, /장 안 서는 날/, /안 끝남/]) {
    assert.match(causeOf('표본없음'), cause, `기준 부재 사유에서 ${cause} 갈래가 사라졌다`);
  }
});

test('R13 못 돎 사유 — 기준은 멀쩡한데 오늘 값만 손상된 경우를 "기준이 없다"고 말하지 않는다', () => {
  /* `canRun` 이 거짓이면 평가기는 `note` 를 **안 부르고** `dep` 을 사유로 낸다. 그래서 `dep` 은
   * canRun=false 의 **모든 형상에서 참**이어야 하는데, 원인 하나(표본 부재·평소 0)만 적어 뒀더니
   * 오늘 값만 깨진 응답에서 거짓말이 됐다 — 기준 100 이 멀쩡한데 "기준이 하나도 없다"고 한다.
   * 문구를 되돌리는 변이가 안 잡히던 자리라 여기서 못 박는다. */
  const f = emptyFacts();
  f.outputs = [{ id: 'only-today-broken', label: '오늘만 손상', today: -1, base: 100, unit: '건' }];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!;
  assert.equal(rr.evaluated, false);
  assert.equal(rr.notRun, 'axis');
  assert.doesNotMatch(
    rr.note ?? '',
    /표본|기준이 하나도 없다|평소가 0/,
    `기준이 멀쩡한데 기준 탓으로 적었다: ${rr.note}`,
  );
});

test('R13 note — 수가 아닌 기준은 "표본 부재"가 아니라 계약 손상이다', () => {
  /* 응답은 런타임 검증을 안 거치고 오므로 `base: "100"`(문자열) 같은 값이 실제로 닿는다.
   * `!Number.isFinite` 하나로 접으면 **writer 결함이 "아직 표본이 없구나"로 읽혀** 아무도 안
   * 고친다 — 미관측과 손상은 다른 사실이다(이 모듈이 가르려는 축 그 자체). */
  const f = emptyFacts();
  f.outputs = [
    { id: 'broken', label: '깨진기준', today: 5, base: '100' as unknown as number, unit: '건' },
    { id: 'nosample', label: '표본없음', today: 5, base: null, unit: '건' },
    { id: 'real', label: '진짜', today: 50, base: 100, unit: '건' },
  ];
  const note = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!.note ?? '';
  const reasonOf = (label: string) => note.split(' · ').find((seg) => seg.includes(label)) ?? '';
  assert.match(reasonOf('깨진기준'), /계약 손상/, '손상을 표본 부재로 접었다');
  // 갈래를 나열하는 사유 — 위 테스트의 주석이 그 이유를 적는다(ALPHA-946)
  assert.match(reasonOf('표본없음'), /기준이 없다/);
  assert.notEqual(reasonOf('깨진기준'), reasonOf('표본없음'));
});

test('R06 경계 — failed_records 가 셈으로 성립 안 하면 "0" 도 "없다" 도 아니다', () => {
  /* 사유가 두 갈래뿐이던 때는 `-1` 이 "0이다"로, `NaN` 이 "없다"로 보고됐다 — 둘 다 거짓 설명이다.
   * DB 에 비음수 CHECK 가 없어 결함 writer 의 값이 여기까지 닿는다. */
  const f = emptyFacts();
  f.tasks = [
    task({ task_key: 'neg', data_status: 'INCOMPLETE', failed_records: -1 }),
    task({ task_key: 'nan', data_status: 'INCOMPLETE', failed_records: Number.NaN }),
  ];
  const v = hits(f, 'R06');
  assert.deepEqual(v.map((x) => x.metric), [null, null]);
  assert.match(v[0].why, /성립하지 않는다/, '음수를 0이라 설명하지 않는다');
  assert.match(v[1].why, /성립하지 않는다/, 'NaN 을 부재라 설명하지 않는다');
});

test('R13 경계 — 기준(base)이 있는 산출이 하나도 없으면 evaluated:false (분포 안이 아니라 분포를 모른다)', () => {
  /* 실 응답은 일별 계열을 주는 데가 없어 이 축이 통째로 빈다. canRun 이 없으면 R13 이
   * "전부 분포 안"이라고 말한다 — 오늘 값이 뭐든. */
  const f = emptyFacts();
  f.outputs = [{ id: 'o', label: '게시', today: 16, base: null, unit: '종' }];
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!;
  assert.equal(rr.evaluated, false);
  assert.equal(rr.notRun, 'axis');
  /* `base: 0` 은 기준이 아니다 — 나눗셈이 성립하지 않아 `run()` 도 거른다. `canRun` 만
   * `!= null` 로 느슨해지면 '평가됨 · 위반 0'("분포 안")이 서고 오늘 값이 뭐든 조용하다. */
  f.outputs = [{ id: 'zero', label: '게시', today: 999, base: 0, unit: '종' }];
  assert.equal(buildReport(f, NOW).rules.find((r) => r.id === 'R13')!.evaluated, false);
  f.outputs.push({ id: 'p', label: '문서', today: 100, base: 100, unit: '건' });
  assert.equal(buildReport(f, NOW).rules.find((r) => r.id === 'R13')!.evaluated, true);
});

test('검증 안 된 응답이 판정을 통과하지 못한다 — `NaN`·타입 밖 값은 "봤고 괜찮다"가 아니다', () => {
  /* facts 응답은 런타임 검증을 안 거치고 온다(타입 선언은 JSON 을 못 막는다). `NaN` 은 `!= null`
   * 을 통과하는데 모든 비교가 거짓이라, 그대로 두면 **판정 대상인 척하면서 아무것도 안 걸리는**
   * 값이 된다 — 이 콘솔이 없애려는 칸 혼동의 가장 조용한 형태다. */
  const nanChain = emptyFacts();
  nanChain.chain = {
    feeds: [],
    stages: [
      { id: 'a', label: 'A', batch: NaN, src: 's' },
      { id: 'b', label: 'B', batch: NaN, src: 's' },
    ],
  };
  assert.equal(buildReport(nanChain, NOW).rules.find((r) => r.id === 'R10')!.evaluated, false, 'NaN 을 점으로 셌다');

  /* `run()` 도 **같은 술어**를 써야 한다 — 수가 아닌 값이 `prev` 로 들어가면 그 뒤 비교가
   * 언제나 거짓이라 canRun 이 센 점(10·5 둘)과 실제 비교가 갈려 손실이 통째로 묻힌다 */
  const mixedChain = emptyFacts();
  mixedChain.chain = {
    feeds: [{ id: 'fb', label: '배치 트리거', v: 10, unit: 'ETF', src: 't' }],
    stages: [
      { id: 'bad', label: '이상', batch: NaN, src: 's' },
      { id: 'c.run', label: '런', batch: 5, src: 's' },
    ],
  };
  assert.deepEqual(
    hits(mixedChain, 'R10').map((v) => [v.targetId, v.metric]),
    [['batch:c.run', 5]],
    '수 아닌 값이 prev 를 오염시켜 10→5 손실을 놓쳤다',
  );

  /* R13 — 기준이 NaN 이면 기준이 아니다. `today` 가 NaN 이면 판정 자체가 성립 안 하므로
   * 위반이 아니라 **note 가 그 산출을 밝혀야** 한다(안 밝히면 봤다고 착각한다) */
  const nanOut = emptyFacts();
  nanOut.outputs = [{ id: 'x', label: '게시', today: 10, base: NaN, unit: '종' }];
  assert.equal(buildReport(nanOut, NOW).rules.find((r) => r.id === 'R13')!.evaluated, false);

  /* 기준은 있는데 오늘 값이 수가 아닌 산출도 **판정 불가**다 — 그것뿐이면 못 돎이지
   * "전부 분포 안"이 아니다. `canRun` 이 기준 축만 보면 여기서 거짓 평가됨이 선다. */
  nanOut.outputs = [{ id: 'y', label: '문서', today: NaN, base: 100, unit: '건' }];
  assert.equal(buildReport(nanOut, NOW).rules.find((r) => r.id === 'R13')!.evaluated, false);

  /* 하나라도 판정 가능하면 돈다 — 대신 빠진 산출을 note 가 이름으로 밝힌다 */
  nanOut.outputs.push({ id: 'ok', label: '정상', today: 100, base: 100, unit: '건' });
  const r13 = buildReport(nanOut, NOW).rules.find((r) => r.id === 'R13')!;
  assert.equal(r13.evaluated, true);
  assert.equal(r13.violations, 0);
  assert.match(r13.note ?? '', /문서/, 'today 가 수가 아닌 산출이 침묵했다');
  assert.doesNotMatch(r13.note ?? '', /정상/, '판정한 산출을 빠졌다고 적었다');

  /* `Infinity` 는 `NaN` 과 반대 방향으로 샌다 — 비교가 언제나 **참**이라 위반을 지어낸다.
   * `metric: Infinity` 인 사건이 목록 맨 위에 서고(정렬이 크기순이다) 아무도 그 수를 못 읽는다. */
  const inf = emptyFacts();
  inf.outputs = [
    { id: 'z', label: '무한', today: Infinity, base: 100, unit: '건' },
    { id: 'ok', label: '정상', today: 100, base: 100, unit: '건' },
  ];
  const infRep = buildReport(inf, NOW);
  assert.equal(infRep.violations.filter((v) => v.rule === 'R13').length, 0, '무한대로 위반을 지어냈다');
  assert.match(infRep.rules.find((r) => r.id === 'R13')!.note ?? '', /무한/, '판정에서 뺐으면 밝혀야 한다');

  /* R19 — `NaN`·`''` 는 모름이다. 전부 모르면 못 돎이지 "0건"이 아니다 */
  const nanDead = withMinute([session({ deadJobs: NaN as unknown as number })]);
  assert.equal(buildReport(nanDead, NOW).rules.find((r) => r.id === 'R19')!.evaluated, false);

  /* 섞이면 돌되 **note 의 축이 필터와 같아야** 한다 — `== null` 로만 세면 NaN 세션이 판정에서
   * 빠진 채 note 에도 안 나와 그 데이터셋을 봤다고 착각한다 */
  const mixedNan = buildReport(
    withMinute([
      session({ deadJobs: 0 }),
      session({ dataset: 'inav_minute', deadJobs: NaN as unknown as number }),
    ]),
    NOW,
  ).rules.find((r) => r.id === 'R19')!;
  assert.equal(mixedNan.evaluated, true);
  assert.match(mixedNan.note ?? '', /inav_minute/, 'NaN 세션이 침묵했다');
});

test('축이 통째로 없어도 평가는 산다 — `canRun` 은 evaluated 와 무관하게 전 규칙에서 불린다', () => {
  /* `canRun` 이 던지면 그 규칙만이 아니라 **평가 전체**가 죽어 19규칙의 사건이 화면에서 사라진다
   * (파이프라인이 깨진 날 콘솔이 통째로 오류 카드가 된다). `note` 진입점을 막은 것과 같은
   * 종류이고, 계약 문서가 `chain`·`outputs` 옵셔널화를 남은 일로 적어 뒀다 — 그날 실제로 온다. */
  const partial = emptyFacts();
  delete (partial as { chain?: unknown }).chain;
  delete (partial as { outputs?: unknown }).outputs;
  const rep = buildReport(partial, NOW);
  assert.equal(rep.rules.length, RULES.length, '축 부재가 평가 전체를 죽였다');
  assert.equal(rep.rules.find((r) => r.id === 'R10')!.evaluated, false);
  assert.equal(rep.rules.find((r) => r.id === 'R13')!.evaluated, false);
  /* 다른 축의 규칙은 그대로 돈다 — 한 축의 부재가 남의 판정을 지우지 않는다 */
  assert.equal(rep.rules.find((r) => r.id === 'R01')!.evaluated, true);
});

test('R14 전달 정합 — 두 방향 다 P0 다. 시드 기록은 표시일 뿐 심각도를 내리지 않는다', () => {
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
  /* 🔴 **시드 기록이 있어도 강등하지 않는다.** 이 값은 **합계**이고 `seed_note` 는 그중 일부를
   * 설명하는 문장일 뿐이다 — "전량 시드"라는 불변식은 타입에도 계약에도 없어서, 시드 1건이 남은
   * 채 진짜 누락 1건이 더해지면 합계 2 를 통째로 P2 로 내린다(리뷰 2라운드). 표시는 남기고
   * 심각도는 규칙 기본값을 쓴다. */
  assert.equal(v[1].sev, 'P0', '시드 기록을 근거로 심각도를 내렸다');
  /* 출처 표시(`seed`)도 안 붙인다 — `buildReport` 가 그걸 `source: 'SEED'` 로 내보내고 화면은
   * "운영 데이터가 아니다"라고 설명한다. 합계 중 일부만 시드일 수 있으니 전체를 그렇게 부를 수
   * 없다. 기록은 사유에 **덧붙여** 나른다: 규칙의 판정이 앞, 기록이 뒤. */
  assert.notEqual(v[1].seed, true, '합계 전체를 시드 출처로 덮었다');
  assert.match(v[1].why, /무효화 통지/, '규칙의 판정 사유가 기록에 밀려났다');
  assert.match(v[1].why, /로컬 시드\(WITHDRAWN\)/, '시드 기록이 사유에서 사라졌다');
  f.boundary = { published_without_delivery: 0, delivery_now_nonpublished: 0 };
  assert.equal(hits(f, 'R14').length, 0);

  /* 🔴 **시드가 걷히면 강등도 걷혀야 한다.** `seed_note` 는 이 수가 로컬 시드 유래라는 사실의
   * 유일한 신호다 — 없는데도 `seed: true`·`sev: 'P2'` 를 붙이면, 실 응답의 "무효화 통지가 안 간
   * 발번"(진짜 P0 정합 위반)이 SEED 칩을 단 P2 로 강등돼 조용해진다.
   *
   * 이 케이스가 `why` 문구만 검사하던 동안은 그 강등을 **거부하지 못했다**(리뷰가 잡았다) —
   * 픽스처·스냅샷이 둘 다 `seed_note` 를 줘서 강등 분기가 늘 참이었기 때문이다. */
  f.boundary = { published_without_delivery: 0, delivery_now_nonpublished: 1 };
  const bare = hits(f, 'R14')[0];
  assert.equal(bare.sev, 'P0', '시드 기록이 없는데 P2 로 강등했다');
  assert.notEqual(bare.seed, true, '시드 기록이 없는데 SEED 로 표시했다');
  /* `why` 는 규약 이후 문맥의 유일한 운반자라 비면 상세·ⓘ 의 '왜'가 통째로 빈다.
   * 문장은 서버가 실제로 세는 것과 같아야 한다 — B1 은 무효화 통지가 안 간 발번만 센다. */
  assert.match(bare.why, /무효화 통지/, '실제로 세는 것과 다른 사유를 쓴다');
  assert.ok(!bare.why.includes('기록:'), '기록이 없는데 기록 문구가 붙었다');
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
  sourceGroup: 'kis',
  phase: 'ACTIVE',
  leaseExpired: false,
  overdueNoEvidence: 0,
  deadJobs: 0,
  ...o,
});
const withMinute = (
  sessions: MinuteSessionFact[],
  deadJobsByDataset: Record<string, number | null> = {},
): Facts => {
  const f = emptyFacts();
  f.minute = { date: '2026-08-03', sessions, deadJobsByDataset };
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

test('R19 — 날짜 축 집계는 벤더마다 복제하지 않는다 (3건이 두 사건으로 서면 6건으로 읽힌다)', () => {
  /* 뉴스 job 은 세션 연결 컬럼이 없어 `(dataset, date)` 집계 하나뿐이다. 벤더 축이 생기기 전에는
   * 대상이 데이터셋이라 겹쳤는데, 벤더를 실으면서 **같은 사실이 벤더 수만큼 독립 사건**이 됐다.
   * 값의 입도가 사건의 입도를 정한다는 규약을 여기서 못박는다. */
  /* 오늘 뉴스 벤더는 `bigkinds` 하나다(`states.py` 의 `SOURCE_GROUPS_BY_DATASET`) — 이 단언이
   * 지키는 것은 **벤더가 늘 때** 조용히 두 배로 세지 않는다는 불변식이다. */
  const f = withMinute(
    [
      session({ dataset: 'news_minute', sourceGroup: 'bigkinds', deadJobs: null }),
      session({ dataset: 'news_minute', sourceGroup: 'future_vendor', deadJobs: null }),
    ],
    /* 값이 **세션 밖**에 하나로 선다 — 세션마다 실려 있던 동안은 벤더 수만큼 복제할 여지가
     * 구조적으로 남아 있었다. 지금은 실을 자리가 하나뿐이라 표현 불가다. */
    { news_minute: 3 },
  );
  const vs = hits(f, 'R19');
  assert.equal(vs.length, 1, '벤더마다 복제됐다 — DEAD 3건이 6건으로 읽힌다');
  assert.equal(vs[0].targetId, 'news_minute', '벤더로 못 가르는 값에 벤더 대상을 붙였다');
  assert.equal(vs[0].metric, 3);
  /* 문구가 아니라 **주장의 방향**을 본다: 불가능("못 가른다")이 아니라 지금 응답의 한계로 말해야
   * 한다 — 원장에는 축이 있다(`news_extraction_job.source_code`). 불가능으로 못박으면 아무도
   * 그 쿼리를 고치지 않는다. */
  assert.match(vs[0].why, /지금 응답이 날짜 축으로만/, '한계의 주체가 응답이 아니라 원장이 됐다');
  assert.doesNotMatch(vs[0].why, /가르지 못한다/, '불가능으로 못박았다 — 원장에는 벤더 축이 있다');

  /* 세션 축인 값(가격)은 그대로 벤더별로 갈린다 — 두 경로가 한 규칙 안에 있다 */
  /* 가격은 {toss, kis} **교체 운용**이라 교체일에 같은 날짜의 세션이 둘이다 — 실제로 가능한
   * 다벤더 상태이고, 그 값(priceJobs)은 세션 축이라 벤더별로 갈리는 게 맞다. */
  const price = withMinute([
    session({ dataset: 'price_minute', sourceGroup: 'kis', deadJobs: 2 }),
    session({ dataset: 'price_minute', sourceGroup: 'toss', deadJobs: 1 }),
  ]);
  assert.deepEqual(hits(price, 'R19').map((v) => v.targetId), [
    'price_minute/kis',
    'price_minute/toss',
  ]);
});

test('합성 대상 축의 조각이 비면 못 돎이다 — 합성 후 문자열만 보는 가드는 `price_minute/` 를 통과시킨다', () => {
  /* 대상 축은 `dataset/sourceGroup` 으로 **합성**된다. 엔진의 빈 축 가드는 합성 결과만 보므로
   * 벤더가 빈 응답은 정상처럼 보이는 사건 키를 낸다 — 위반이 하나면 충돌도 안 나 그대로 나가고,
   * 내일 또 같은 모양이 오면 어제 공유한 링크가 오늘 사건을 연다(충돌 검사로는 못 잡는다). */
  const broken = withMinute([session({ sourceGroup: '', leaseExpired: true, overdueNoEvidence: 9 })]);
  const rep = buildReport(broken, NOW);
  const r17 = rep.rules.find((r) => r.id === 'R17')!;
  assert.equal(r17.evaluated, false);
  /* 계측 공백이 아니라 **응답의 계약 위반**이다 — 같은 칸에 그리면 응답 결함이 "아직 계측이
   * 없구나"로 읽힌다. `axis` 로 서면 이 구분이 사라진다. */
  assert.equal(r17.notRun, 'identity');
  assert.equal(r17.violations, 0);
  /* 조각이 빈 vid 는 **하나도** 안 나간다 — 나가면 딥링크가 그걸 열 수 있게 된다 */
  assert.deepEqual(rep.violations.filter((v) => v.vid.includes('/@')).map((v) => v.vid), []);
  /* 날짜 축으로 대상을 내는 사건은 벤더를 안 쓰므로 조각이 비어도 그대로 돈다 — 규칙 하나만
   * 세운다. (같은 데이터셋의 세션 축 값은 `null` 이다 — 그 원장은 세션에 안 붙어 있다.) */
  const both = withMinute(
    [session({ sourceGroup: '', deadJobs: null, leaseExpired: true })],
    { price_minute: 2 },
  );
  const rep2 = buildReport(both, NOW);
  assert.equal(rep2.rules.find((r) => r.id === 'R17')!.notRun, 'identity');
  const r19 = rep2.rules.find((r) => r.id === 'R19')!;
  assert.equal(r19.evaluated, true);
  assert.equal(r19.violations, 1, '벤더가 빈 세션 때문에 날짜 축 사건까지 사라졌다');

  /* 조각이 **문자열이 아닌** 경우도 같다 — `[]` 는 truthy 라 `Boolean` 검사를 통과하는데
   * join 하면 빈 문자열이 되어 `price_minute/` 가 나간다. 응답은 런타임 검증을 안 거친다. */
  const notString = withMinute([
    session({ sourceGroup: [] as unknown as string, leaseExpired: true }),
  ]);
  assert.equal(buildReport(notString, NOW).rules.find((r) => r.id === 'R17')!.notRun, 'identity');

  /* 대칭 통제 — 데이터셋 조각이 비어도 같다(한쪽만 막으면 나머지 절반이 남는다) */
  const noDataset = withMinute([session({ dataset: '', leaseExpired: true })]);
  assert.equal(buildReport(noDataset, NOW).rules.find((r) => r.id === 'R17')!.notRun, 'identity');

  /* 양성 통제 — 두 조각이 다 있으면 그대로 위반이 선다(가드가 전부를 삼키지 않는다) */
  assert.equal(hits(withMinute([session({ leaseExpired: true })]), 'R17').length, 1);
});

test('R19 — `deadJobs: null`(모름)과 `0`(실측 0)이 판정에서 갈린다 (어댑터가 지킨 구분이 여기서 죽었다)', () => {
  /* 어댑터가 어휘 밖 데이터셋의 job 원장을 `null` 로 낸다(`priceJobs` 로 접으면 행이 없어 0이
   * 되고 "봤고 괜찮다"가 되므로). 그런데 규칙이 `!= null` 로 **건너뛰기만** 하면 그 구분이
   * 판정 층에서 소멸한다 — null 도 0도 똑같이 '평가됨 · 위반 0' 이었다. */
  const unknownOnly = withMinute([session({ dataset: 'inav_minute', deadJobs: null })]);
  const rr = buildReport(unknownOnly, NOW).rules.find((r) => r.id === 'R19')!;
  assert.equal(rr.evaluated, false, '원장을 하나도 못 읽었는데 "봤고 괜찮다"로 섰다');
  assert.equal(rr.notRun, 'axis');

  /* 실측 0 은 다르다 — 봤고 없었다 */
  const zero = buildReport(withMinute([session({ deadJobs: 0 })]), NOW).rules.find((r) => r.id === 'R19')!;
  assert.equal(zero.evaluated, true);
  assert.equal(zero.violations, 0);

  /* 세션이 0건인 것도 실측이다 — 잃을 후속 작업 자체가 없다. 이걸 못 돎으로 세면
   * 실시간 레인이 안 도는 날마다 거짓 `못 돎` 이 뜬다 */
  assert.equal(buildReport(withMinute([]), NOW).rules.find((r) => r.id === 'R19')!.evaluated, true);

  /* 섞인 날은 규칙 단위 못 돎으로 표현할 수 없다 — 어느 데이터셋이 판정에서 빠졌는지 밝힌다 */
  const mixed = buildReport(
    withMinute([session({ deadJobs: 0 }), session({ dataset: 'inav_minute', deadJobs: null })]),
    NOW,
  ).rules.find((r) => r.id === 'R19')!;
  assert.equal(mixed.evaluated, true);
  assert.match(mixed.note ?? '', /inav_minute/, '판정에서 빠진 데이터셋을 안 밝혔다');
});

test('R19 — 날짜 축 맵 자체가 없으면 못 돎이다 (빈 맵으로 접으면 세션 축 재분류가 되살아난다)', () => {
  /* 타입은 필수지만 응답은 런타임 검증을 안 거친다. 빠진 것을 `?? {}` 로 접으면 날짜 축
   * 데이터셋이 조용히 **세션 축으로 재분류**되고(맵에 없으면 세션 축이므로), 벤더가 둘인 날
   * 같은 3건이 두 사건이 된다 — 이 규칙이 막으려던 결함 그 자체다. */
  const f = withMinute([
    session({ dataset: 'news_minute', sourceGroup: 'bigkinds', deadJobs: 3 }),
    session({ dataset: 'news_minute', sourceGroup: 'future_vendor', deadJobs: 3 }),
  ]);
  delete (f.minute as { deadJobsByDataset?: unknown }).deadJobsByDataset;
  const rr = buildReport(f, NOW).rules.find((r) => r.id === 'R19')!;
  assert.equal(rr.evaluated, false, '맵 부재를 빈 맵으로 접었다');
  assert.equal(rr.violations, 0, '세션 축으로 재분류돼 벤더마다 복제된 사건이 나갔다');
});

test('🔴 R19 — 뉴스 세션이 없는 날에도 그날 DEAD 가 사건으로 선다', () => {
  /* 값이 세션에 매달려 있던 동안, 그날 그 데이터셋의 세션이 없으면 값이 실릴 자리가 없어
   * 유실이 통째로 사라졌다(`평가됨 · 위반 0`). 세션이 없는 날은 실제로 있다 — 아침 planner
   * 전 · 비거래일 · **뉴스 계획만 실패한 날**(가격은 세우고 news-worker 는 안 올리는, 코드가
   * 의도적으로 만드는 경로). 하필 그날이 R19 가 가장 시끄러워야 할 날이다. */
  const noSession = withMinute([], { news_minute: 3 });
  const vs = hits(noSession, 'R19');
  assert.equal(vs.length, 1, '세션이 없다고 유실을 못 본 척했다');
  assert.equal(vs[0].targetId, 'news_minute');
  assert.equal(vs[0].metric, 3);
  assert.equal(vs[0].scope, '2026-08-03', '날짜 축 사건인데 시점 범위가 없다');

  /* 가격 세션만 있는 날도 같다 — 뉴스 계획만 실패한 날의 실제 모양이다 */
  const priceOnly = withMinute([session({ deadJobs: 0 })], { news_minute: 3 });
  assert.deepEqual(hits(priceOnly, 'R19').map((v) => v.targetId), ['news_minute']);
});

test('R19 후속 처리 유실 — DEAD 는 종료 상태라 1건부터 위반 (두 축 모두)', () => {
  assert.equal(hits(withMinute([session({ deadJobs: 0 })]), 'R19').length, 0);
  assert.equal(hits(withMinute([session({ deadJobs: 1 })]), 'R19').length, 1);
  /* 날짜 축도 같은 임계다 — 0건은 "봤는데 없었다"이지 위반이 아니다 */
  assert.equal(hits(withMinute([], { news_minute: 0 }), 'R19').length, 0);
  assert.equal(hits(withMinute([], { news_minute: 1 }), 'R19').length, 1);
});

test('R19 — 같은 데이터셋을 두 축으로 두 번 세지 않는다 (세션에 낡은 값이 남아 있어도)', () => {
  /* 맵에 있으면 **날짜 축**이다. 그 데이터셋의 세션이 값을 들고 있어도(응답이 두 자리에 다 실어
   * 주거나 낡은 값이 남았거나) 사건은 하나여야 한다 — 두 번 세면 같은 유실이 두 배로 읽히고,
   * 그게 이 축을 세션에서 떼어낸 이유 자체다. */
  const f = withMinute(
    [session({ dataset: 'news_minute', sourceGroup: 'bigkinds', deadJobs: 5 })],
    { news_minute: 3 },
  );
  const vs = hits(f, 'R19');
  assert.deepEqual(vs.map((v) => [v.targetId, v.metric]), [['news_minute', 3]]);
});

test('R19 — 날짜 축 값이 `null`(모름)이면 그 데이터셋을 note 가 밝힌다', () => {
  /* 맵에 있는데 값이 모름인 경우 — 그 데이터셋은 판정에서 빠진다. 세션 축 모름과 같은 대우를
   * 받아야 한다: 안 밝히면 그 유실이 "0건"에 흡수돼 보인다. */
  const only = buildReport(withMinute([], { news_minute: null }), NOW).rules.find((r) => r.id === 'R19')!;
  assert.equal(only.evaluated, false, '판정할 원장이 하나도 없는데 평가됨으로 섰다');

  const mixed = buildReport(
    withMinute([session({ deadJobs: 0 })], { news_minute: null }),
    NOW,
  ).rules.find((r) => r.id === 'R19')!;
  assert.equal(mixed.evaluated, true);
  assert.match(mixed.note ?? '', /news_minute/, '날짜 축 모름이 침묵했다');
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

test('정렬 1순위 — 심각도가 먼저다 (연쇄가 긴 P1 이 독립 P0 을 앞지르지 않는다)', () => {
  /* 제목이 "심각도 → 연쇄 크기 → 수치 · 심각도 승격"을 다 재는 것처럼 돼 있었는데, 픽스처의
   * R02·R03 은 targetId 가 달라 애초에 병합되지 않아 **연쇄가 0이었다** — `b.size - a.size` 를
   * 지워도 통과했다. 순위는 셋이니 테스트도 셋으로 가른다(이건 1순위만). */
  const f = emptyFacts();
  f.runs = [run({ id: 'open', deadline: '2026-08-03T16:00:00+09:00' })]; // R02 P1
  f.queues = [{ name: 'dead', visible: 0, in_flight: 0, dlq: 9, subscribers: ['svc'] }]; // R12 P0
  const ev = evaluate(f, NOW);
  assert.deepEqual(ev.incidents.map((i) => i.root.rule), ['R12', 'R02']);
});

test('정렬 2순위 — 같은 심각도면 연쇄가 긴 사건이 앞이다 (조치 단위가 큰 쪽을 먼저 본다)', () => {
  /* 앞 테스트가 못 재던 자리다. 실제로 병합되는 간선(R05·R16 → R04)으로 연쇄 3짜리 사건을
   * 세우고, 같은 P0 인 독립 사건과 순서를 다툰다. `b.size - a.size` 를 지우면 깨진다. */
  const f = emptyFacts();
  /* ⚠️ `lonely` 를 **먼저** 넣는다. 뒤에 두면 `b.size - a.size` 를 지워도 안정 정렬이 삽입
   * 순서를 그대로 둬 'chained' 가 여전히 앞이고 테스트가 통과한다(변이로 확인 — 처음 쓴
   * 픽스처가 정확히 그랬다). 비교자가 실제로 순서를 **뒤집어야** 잡힌다. */
  f.runs = [run({ id: 'lonely', ledger_status: 'FAILED' }), run({ id: 'chained', ledger_status: 'FAILED' })];
  f.tasks = [
    task({ task_key: 'T1', run_id: 'chained', task_outcome: 'FAILED', attempts: 3, max_retries: 3 }),
    task({ task_key: 'T2', run_id: 'chained', task_outcome: 'PENDING' }),
  ];
  const ev = evaluate(f, NOW);
  const [first, second] = ev.incidents;
  assert.equal(first.root.targetId, 'chained', `연쇄가 긴 쪽이 앞이어야 한다: ${ev.incidents.map((i) => `${i.root.targetId}(${i.size})`)}`);
  assert.ok(first.size > second.size, '크기 비교자가 실제로 갈랐는지');
  assert.equal(second.root.targetId, 'lonely');
});

test('인과 부모 — 후보가 여럿이면 사실의 배열 순서가 뿌리를 바꾸면 안 된다', () => {
  /* `R10 → R11` 의 간선 조건은 **자식만** 본다(`c.src === 'intraday'`). 그래서 R11 위반이 둘이면
   * 둘 다 부모 후보이고, `violations.find(...)` 는 그중 **먼저 나온 것**을 골랐다 — 위반 순서는
   * 응답의 행 순서에서 오므로 서버 쿼리의 `ORDER BY` 하나가 바뀌면 같은 날 같은 장애의 뿌리가
   * 다른 큐로 옮겨 간다. 운영자는 어제와 다른 원인을 보고, 공유한 딥링크는 다른 사건을 연다.
   *
   * 사실이 진짜 부모를 못 가르는 것은 그대로다 — 잴 수 있는 것은 **같은 사실이면 같은 답**이다.
   * 큐 배열을 뒤집어 두 번 평가하고 뿌리가 같은지 본다. `.sort(vid)` 를 지우면 깨진다. */
  const build = (queues: Facts['queues']): Facts => {
    const f = emptyFacts();
    f.chain = {
      feeds: [
        { id: 'fb', label: '배치 트리거', v: 0, unit: 'ETF', src: 't' },
        { id: 'fi', label: '장중 트리거', v: 65, unit: '건', src: 't' },
      ],
      stages: [{ id: 'c.obs', label: '관측', batch: 0, intraday: 9, src: 's' }],
    };
    f.queues = queues;
    return f;
  };
  const qa = { name: 'q-alpha', visible: 7, in_flight: 0, dlq: 0, subscribers: [] };
  const qb = { name: 'q-beta', visible: 7, in_flight: 0, dlq: 0, subscribers: [] };
  const rootOf = (f: Facts) => {
    const ev = evaluate(f, NOW);
    const child = ev.violations.find((v) => v.rule === 'R10');
    assert.ok(child, '픽스처가 R10 장중 손실을 못 만들었다 — 경계를 재는 게 아니라 no-op 이다');
    const owner = ev.incidents.find((I) => I.members.some((m) => m.v.vid === child!.vid));
    assert.ok(owner, 'R10 이 흡수되지 않았다 — 부모 선택 자체가 안 일어났다');
    return owner!.root.targetId;
  };
  assert.equal(rootOf(build([qa, qb])), rootOf(build([qb, qa])), '큐 순서가 뿌리를 바꾼다');
  /* 순서 무관만 재면 비교자를 **내림차순으로 뒤집어도** 통과한다(변이로 확인) — 그러면 이미
   * 공유된 사건 링크가 전부 다른 부모로 옮겨 간다. 계약은 "재현 가능"이 아니라 "가장 작은 vid" 다. */
  const q = [qa, qb].map((x) => `R11:${x.name}`).sort()[0];
  assert.equal(rootOf(build([qb, qa])), q.slice('R11:'.length), '뿌리는 vid 가 가장 작은 후보다');
});

test('🔴 심각도 승격과 R02→R03 간선은 지금 어떤 사실로도 못 밟는다 (죽은 경로를 테스트가 밝힌다)', () => {
  /* 두 가지가 **구조적으로** 도달 불가다. 픽스처로는 못 보여주니 구조로 단언한다 —
   * 안 적어 두면 "테스트가 있으니 검증됐다"로 읽히고, 실제로 앞 테스트가 그렇게 읽혔다.
   *
   * ① `I.sev` 승격(구성원이 뿌리보다 심각) — 승격은 **부모가 자식보다 약할 때만** 일어나는데
   *    현재 간선 일곱 중 그런 쌍이 하나도 없다. reduce 를 지워도 아무 테스트가 안 깨진다.
   * ② `R02 → R03` — R02 는 `!ledger_status`, R03 은 `ledger_status` 를 요구하고 간선은 **같은
   *    런**(`targetId` 동일)을 요구한다. 한 런이 둘 다일 수 없으니 이 간선은 영영 안 붙는다.
   *    README 가 "인과 간선 7개"라 세는 것 중 하나가 실은 죽어 있다 — 의도를 모르는 채 지우거나
   *    고치지 않고, 여기서 사실만 못 박는다.
   *
   * 둘 중 하나라도 도달 가능해지면 이 테스트가 깨진다 — 그때 **진짜 사례 테스트를 더하라**. */
  const sev: Record<string, string> = Object.fromEntries(RULES.map((R) => [R.id, R.base]));
  const rank: Record<string, number> = { P0: 0, P1: 1, P2: 2 };
  const promotable = EDGES.filter((e) => rank[sev[e.c]] < rank[sev[e.p]]).map((e) => `${e.c}→${e.p}`);
  assert.deepEqual(promotable, [], `승격 가능한 간선이 생겼다 — 승격 사례 테스트를 더하라: ${promotable}`);

  /* 간선을 **지우는** 변이가 안 잡히던 자리다 — 배타성만 재면 목록에서 빠져도 전건 초록이고,
   * README 의 "간선 7개 중 하나가 죽어 있다"가 조용히 6개짜리 사실로 바뀐다. 존재부터 못 박는다. */
  assert.equal(EDGES.length, 7, 'README 가 세는 간선 수와 다르다');
  assert.ok(
    EDGES.some((e) => e.c === 'R02' && e.p === 'R03'),
    'R02→R03 간선이 사라졌다 — 죽은 간선을 지운 것이라면 README 의 "간선 7개"도 함께 고쳐라',
  );
  const r02 = RULES.find((R) => R.id === 'R02')!;
  const r03 = RULES.find((R) => R.id === 'R03')!;
  for (const ledger_status of [undefined, null, '', 'RUNNING']) {
    const f = emptyFacts();
    f.runs = [run({ id: 'X', deadline: '2026-08-03T16:00:00+09:00', ledger_status, aws_status: 'SUCCEEDED' })];
    const both = r02.run(f, { now: NOW }).length > 0 && r03.run(f, { now: NOW }).length > 0;
    assert.equal(both, false, `한 런이 R02·R03 을 동시에 밟았다(${ledger_status}) — 간선이 살아났으니 사례 테스트를 더하라`);
  }
  /* 술어 배타성만 재면 **간선 조건(`when`)을 느슨하게 바꿔 살리는 경로**를 놓친다 — `when` 을
   * `() => true` 로 두면 다른 런의 R02·R03 이 병합돼 간선이 살아나는데 위 루프는 그대로 통과한다.
   * 그러니 `evaluate` 로도 확인한다: 둘 다 있는 사실에서 R02 가 흡수되지 않아야 한다. */
  const f = emptyFacts();
  f.runs = [
    run({ id: 'open', deadline: '2026-08-03T16:00:00+09:00' }), // R02
    run({ id: 'projlag', ledger_status: 'RUNNING', aws_status: 'SUCCEEDED' }), // R03
  ];
  const ev = evaluate(f, NOW);
  const v02 = ev.violations.find((v) => v.rule === 'R02');
  assert.ok(v02, '픽스처가 R02 를 못 만들었다');
  assert.ok(
    ev.incidents.some((I) => I.root.vid === v02!.vid),
    'R02 가 흡수됐다 — R02→R03 간선이 살아났으니 인과 사례 테스트를 더하라',
  );
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

test('스냅샷 회귀 — 동봉 스냅샷은 위반 24 · 사건 15 · P0 6 (레퍼런스 v4 대비 R14 강등 제거분 +1)', async () => {
  const { readFileSync } = await import('node:fs');
  const facts = JSON.parse(
    readFileSync(new URL('./facts-snapshot.json', import.meta.url), 'utf8'),
  ) as Facts;
  const ev = evaluate(facts);
  /* 픽스처가 아니라 **동봉 스냅샷 위에서** 규약을 전수 검사한다 — 픽스처는 룰이 만든 값의
   * 한 갈래만 밟지만 스냅샷은 실제로 걸린 24건 전부를 준다 */
  ev.violations.forEach(assertContract);
  /* 29 → 24 · 20 → 15 는 **회귀가 아니라 정정**이다(ALPHA-946). 이 스냅샷은 `as_of.db` 가
   * `2026-08-03T16:20 KST` 이고 `trade_date` 가 같은 08-03 인 **미완결 당일의 캡처**인데,
   * 산출 다섯에 기준(중앙값)이 실려 있었다. 기준일 후보는 전부 다 지난 하루의 값이라 아직
   * 쌓이는 중인 당일과 같은 축이 아니고, 서버는 이제 그 날의 `base` 를 안 보낸다
   * (`JdbcConsoleFactsRepository.outputs`). 픽스처가 서버가 낼 수 없는 응답을 들고 있으면
   * 그 픽스처가 결함을 정답으로 고정한다.
   * ⇒ R13 이 다섯 건 전부 못 세우고, 그 다섯이 전부 **독립 사건**이었으므로 사건도 5 준다. */
  assert.equal(ev.violations.length, 24);
  assert.equal(ev.incidents.length, 15);
  /* 사건 P0 는 안 움직인다 — R13 은 P1 이다. ⚠️ 그것만으로 "줄어든 것이 R13 뿐"이 **증명되지는
   * 않는다**(그렇게 적었다가 리뷰에 뒤집혔다 — 다른 규칙의 P1 하나가 사라지고 또 하나가 새로
   * 걸려도 총계 셋이 같다). 그 상쇄는 아래 **규칙별 건수**가 잡는다. */
  /* 5 → 6 은 **회귀가 아니라 정정**이다(ALPHA-738 B2a). R14 가 비게시 발번을 `seed_note` 만 보고
   * P2 로 강등하던 것을 없앴다 — 그 값은 **합계**라 "전량 시드"를 가정할 수 없고, 실 응답에서 그
   * 수는 "무효화 통지가 안 간 발번"이라 진짜 P0 다.
   * ⚠️ `seed` 표시도 함께 뺐다(`buildReport` 가 그걸 `source: 'SEED'` 로 내보내 합계 전체를
   * "운영 데이터가 아니다"로 만든다). 시드임을 말하는 것은 이제 `why` 에 덧붙는 기록 문장뿐이고,
   * 화면(`DeliveryPage`)도 같은 이유로 그 행의 SEED 칩을 뺐다 — **TSX 라 단언이 없다**(이 앱에
   * 컴포넌트 테스트가 없다). 칩이 되살아나도 이 스위트는 통과하므로 손으로 봐야 한다. */
  assert.equal(ev.incidents.filter((i) => i.sev === 'P0').length, 6);

  /* ⚠️ **줄어든 자리를 규칙별로 못박는다.** 총계 셋(24·15·6)만 고정하면 **상쇄가 통과한다** —
   * 어느 규칙의 위반 하나가 사라지고 다른 규칙에 하나가 새로 생겨도 총계는 그대로다.
   * 규칙별 건수를 통째로 비교하면 그 상쇄가 읽히는 deepEqual diff 로 드러난다.
   * (R13 이 키에서 **사라진 것** 자체가 이 조각의 착지 증거이기도 하다.) */
  const byRule: Record<string, number> = {};
  for (const v of ev.violations) byRule[v.rule] = (byRule[v.rule] ?? 0) + 1;
  assert.deepEqual(byRule, {
    R01: 1, R02: 2, R03: 1, R04: 1, R05: 3, R06: 4, R07: 1, R08: 1,
    R09: 2, R10: 2, R11: 1, R14: 1, R15: 1, R16: 3,
  }, 'R13 외의 규칙에서도 건수가 움직였다 — 총계가 같아도 이건 회귀다');

  /* R13 은 위반이 아니라 **못 돎**이어야 한다(기준이 없으면 "분포 안"이 아니라 분포를 모른다).
   * 그리고 그 사유가 화면에 실려야 한다 — `evaluate` 는 못 돈 규칙의 `note` 를 안 부르고 `dep` 을 싣는다. */
  const r13 = ev.rules.find((r) => r.id === 'R13')!;
  assert.equal(r13.evaluated, false, 'R13 이 기준 없이도 돌고 있다 — 당일 거짓 P1 이 되살아났다');
  assert.equal(r13.violations, 0);
  assert.ok(r13.note && r13.note.length > 0, '못 돈 R13 이 사유 없이 비어 있다');

  // 뉴스 런 타임아웃 사건이 연쇄 +7 로 병합된다 (명세 §2-2의 예시 그대로)
  const news = ev.incidents.find((i) => i.root.targetId === 'news:2026-08-03T15:30');
  /* `!` 로 넘기면 실패가 다음 줄의 TypeError 로 나와 무엇이 틀렸는지 안 읽힌다 */
  assert.ok(news, '뉴스 런 사건이 사라졌다 — 사건 키 축(targetId)이 바뀌었는지 본다');
  assert.equal(news.size, 8);

  /* **런북 키 회귀 검출기.** 조회는 `${rule}.${targetId}`(`runbookOf`, rules/evaluate.ts) 인데 그걸 지키는 단언이
   * 룰 테스트에도 화면 테스트에도 없었다 — R07 의 `target` 을 사람이 읽을 문구로 다듬으면
   * 테스트는 전건 초록인 채 조치 칸만 조용히 `런북 미등록` 이 된다(리뷰가 변이로 실증).
   * 이 규약이 "target 은 라벨로 바꿔라"라는 압력을 새로 만들었으므로 그 자리에 가드를 둔다.
   *
   * ⚠️ 방향을 조심해야 한다. 계약은 **위반 → 런북**(위반이 나면 그 키로 조회된다)이지 그 역이
   * 아니다. "모든 런북 항목에 지금 살아 있는 위반이 있어야 한다"로 쓰면 아직 안 터진 상황의
   * 조치를 미리 등록하는 정상적인 사용이 거짓 실패가 된다(R12 DLQ·R17 실시간 런북 — §6-9 의
   * 큐에 있는 작업이다). 그래서 "지금 붙는 키의 집합"을 고정한다 — 24·15·6 과 같은 종의
   * 스냅샷 회귀값이다.
   *
   * **안 잡는 것**: 걸린 룰에 오타 난 키를 새로 등록하는 것(`R05.TYPO`)은 애초에 안 붙으므로
   * 집합이 안 변해 통과한다. 그걸 잡으려면 "걸린 룰의 키는 그 룰의 targetId 중 하나여야 한다"로
   * 써야 하는데, 그 형태는 **걸린 룰의 건강한 target 에 런북을 미리 등록**하는 것을 거짓 실패로
   * 만든다(`R05.LOAD_PRICE_DAILY` 류 — 실측). 가드를 두 개 쌓지 않고 하나만 두되,
   * **오탐이 없는 게 아니라 오탐의 방향을 고른 것**이다: 안 걸린 룰의 선등록은 통과시키고,
   * 걸린 위반의 런북 등록은 아래 목록 갱신을 요구한다(24건 중 18건이 `런북 미등록` 이라
   * 그쪽이 더 잦은 편집이다 — 실패는 읽히는 deepEqual diff 다). */
  const produced = new Set(ev.violations.map((v) => `${v.rule}.${v.targetId}`));
  /* 축이 옵셔널이 됐다 — 스냅샷에는 있지만 실 응답에는 없다. `?? {}` 로 접는 것은 여기서만
   * 옳다(이 단언의 대상은 "등록된 런북과 실제 위반이 맞는가"이고, 축 부재는 그 물음의 답이
   * 빈 집합인 상태다). 판정 층에서 같은 폴백을 쓰면 부재와 실측이 섞인다. */
  const matched = Object.keys(facts.runbook ?? {}).filter((k) => k.includes('.') && produced.has(k));
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

test('모든 규칙의 `note` 는 축이 빈 사실에서도 죽지 않는다 — 여기서 죽으면 화면이 아니라 흰 화면이다', () => {
  /* `note` 는 평가기가 `canRun` **밖에서** 부르던 진입점이었다(지금은 돈 규칙에만 부른다).
   * 옵셔널 축을 읽는 `note` 가 하나라도 붙으면 그 축이 빈 응답에서 평가가 통째로 죽고,
   * 19규칙의 사건이 전부 사라진다 — 규칙 하나의 주석 때문에.
   *
   * 규칙 하나를 골라 단언하지 않고 **집합 전체**를 돈다: 손으로 유지되는 목록은 반드시 낡고,
   * 새로 붙는 규칙은 아무도 안 본다. 축이 빈 사실은 `emptyFacts()` 다(옵셔널 축은 아예 뺀다). */
  const bare = bareFacts();
  const withNote = RULES.filter((R) => R.note);
  assert.ok(withNote.length >= 1, 'note 를 가진 규칙이 사라졌다 — 이 단언이 헛돈다');
  for (const R of withNote) {
    assert.doesNotThrow(() => R.note!(bare), `${R.id}: note 가 없는 축을 읽는다`);
  }
});

test('모든 규칙의 `canRun` 은 축이 빈 사실에서도 죽지 않는다 — 여기서 죽으면 19규칙이 통째로 사라진다', () => {
  /* 🔴 `canRun` 은 `evaluated` 와 **무관하게 모든 규칙에 대해 무조건** 불린다. 옵셔널 축을 읽는
   * `canRun` 이 하나라도 죽으면 그 규칙만이 아니라 **평가 전체**가 사라진다 — 화면이 아니라
   * 흰 화면이다. `note` 진입점과 같은 종류이고, 그래서 같은 형태의 집합 순회를 둔다. */
  const bare = bareFacts();
  const withCanRun = RULES.filter((R) => R.canRun);
  assert.ok(withCanRun.length >= 1, 'canRun 을 가진 규칙이 사라졌다 — 이 단언이 헛돈다');
  for (const R of withCanRun) {
    assert.doesNotThrow(() => R.canRun!(bare), `${R.id}: canRun 이 없는 축을 읽는다`);
  }
});

test('옵셔널 축이 없는 응답에서 그 축의 규칙은 `평가됨 · 위반 0` 이 아니라 `못 돎` 이다', () => {
  /* 이 테스트가 지키는 것은 "안 죽는다"가 아니라 **판정의 방향**이다. 축이 없는데 조용히
   * `평가됨 · 위반 0` 이 서면 계측 공백이 "봤고 괜찮다"로 그려진다 — 이 콘솔이 없애려는 칸
   * 혼동 그 자체이고, 널 가드를 `?? []` 로 넣으면 정확히 그 상태가 된다. */
  const ev = evaluate(bareFacts());
  const byId = new Map(ev.rules.map((r) => [r.id, r]));
  for (const id of ['R03', 'R10', 'R11', 'R12', 'R13', 'R15', 'R16']) {
    const r = byId.get(id);
    assert.ok(r, `${id} 가 리포트에 없다`);
    assert.equal(r!.evaluated, false, `${id}: 축이 없는데 평가됐다고 선다`);
    assert.equal(r!.notRun, 'axis', `${id}: 못 돎의 종류가 축 부재가 아니다`);
  }
  /* 축이 없어도 도는 규칙은 그대로 돌아야 한다 — 전부 못 돎으로 만들면 이 단언은 통과하지만
   * 콘솔이 아무것도 판정하지 않는다(반대 방향 오류). */
  assert.equal(byId.get('R01')!.evaluated, true, 'R01 은 원장만으로 돈다');
  assert.equal(byId.get('R14')!.evaluated, true, 'R14 는 원장만으로 돈다');
});

test('축이 빈 응답에서 부재를 값으로 위조하지 않는다 — 런북 조회·리포트 시각·문장', () => {
  /* 여기 셋은 **전부 컴파일러가 안 잡는 자리**다. `runbook` 은 첨자 접근이라 부재면 죽고,
   * `as_of.aws` 는 타입이 문자열이면 아무 시각으로나 메울 수 있고, `why` 의 템플릿 리터럴은
   * `null` 을 `"null"` 로 렌더한다. 단언이 없으면 전부 조용히 통과한다. */
  const bare = bareFacts();

  // ① 런북 축이 없으면 조회는 **죽지 않고** "등록 없음"이다.
  const anyViolation = { rule: 'R05', targetId: 'X' } as Violation;
  assert.doesNotThrow(() => runbookOf(bare, anyViolation), 'runbook 축이 없으면 조회가 죽는다');
  assert.equal(runbookOf(bare, anyViolation), undefined);

  // ② 관측 시각이 없으면 `null` 이다 — DB 시각으로 메우면 관측 안 한 시점을 관측했다고 말한다.
  const rep = buildReport(bare);
  /* 축이 **없으면 키도 없다**(미배선). `null` 로 접으면 "조회했는데 못 봤다"가 되어, 리포트
   * 소비자가 계측 공백과 제어면 장애를 구분하지 못한다. 시각으로 메우는 것은 더 나쁘다. */
  assert.ok(!('aws' in rep.as_of), 'AWS 축 부재를 값으로 만들었다');
  assert.notEqual(rep.as_of.db, null, 'DB 시각은 있어야 한다 — 단언이 헛돌지 않게');
  // 조회 실패(키는 있고 값이 null)는 그 형상 그대로 나가야 한다 — 미배선과 다른 사실이다.
  const failed = bareFacts();
  failed.meta.aws = null;
  const repFailed = buildReport(failed);
  assert.ok('aws' in repFailed.as_of, '조회 실패 형상이 미배선으로 접혔다');
  assert.equal(repFailed.as_of.aws, null);

  // ③ 레인이 없는 계획 슬롯의 문장에 `null` 이 렌더되면 안 된다.
  const f = emptyFacts();
  f.runs = [run({ id: 'lane:2026-08-03T15:40', lane: null, planned: true, no_run_row: true })];
  const why = evaluate(f).violations.find((v) => v.rule === 'R01')!.why;
  assert.ok(!why.includes('null'), `문장에 null 이 렌더됐다: ${why}`);
  assert.ok(why.includes('레인 미상'), `레인 부재 표기가 없다: ${why}`);
});

test('`note` 의 세 갈래는 배타적이다 — 돌아간 규칙에 "미배선" 주석이 붙지 않는다', async () => {
  /* `dep` 은 **못 돈 사유**다. `?? R.dep` 폴백으로 두면 배선돼서 잘 도는 규칙 행에도 "…배선"
   * 주석과 `*` 표가 영구히 붙는다 — R03·R10·R13 에 `dep` 을 넣자마자 실제로 그랬다.
   * 규칙 하나를 짚지 않고 **집합**으로 단언한다: 새 규칙에 `dep` 이 붙어도 여기서 걸린다. */
  const { readFileSync } = await import('node:fs');
  /* 픽스처가 아니라 **동봉 스냅샷**으로 돈다 — 픽스처는 canRun 이 켜진 규칙을 몇 개 안 밟는다 */
  const snap = JSON.parse(
    readFileSync(new URL('./facts-snapshot.json', import.meta.url), 'utf8'),
  ) as Facts;
  const rep = buildReport(snap);
  const depOf = new Map(RULES.map((R) => [R.id, R.dep]));
  const leaked = rep.rules.filter((r) => r.evaluated && r.note != null && r.note === depOf.get(r.id));
  assert.deepEqual(leaked.map((r) => r.id), [], '돈 규칙의 note 에 dep 이 샜다');

  /* 반대 방향 — 못 돈 규칙은 그 사유를 note 로 들고 있어야 한다(리포트 소비자가 읽는다) */
  const bare = buildReport(emptyFacts(), NOW).rules.find((r) => r.id === 'R08')!;
  assert.equal(bare.evaluated, false);
  assert.equal(bare.note, depOf.get('R08'));
});

test('규칙 id 는 유일하다 — 충돌 검사를 규칙 안으로 좁힌 근거이고, 화면이 vid→규칙을 되찾는 축이다', () => {
  /* 이 단언이 없으면 두 곳이 조용히 무너진다. (1) 평가기가 vid 충돌을 **규칙 단위**로만 본다
   * (규칙 간 `seen` 을 지운 논거가 "vid 는 규칙 id 로 시작한다"였다). (2) 화면이
   * `ruleOfVid(vid)` 로 규칙 id 를 잘라 되찾는다(화면 조각이 붙을 자리).
   * id 가 겹치면 전자는 충돌을 못 잡고 후자는 남의 규칙 사유를 그린다. */
  assert.equal(new Set(RULES.map((R) => R.id)).size, RULES.length, '규칙 id 가 겹친다');
  /* 유일성만으로는 접두사 논거가 안 선다: id 에 구분자가 들어가면 `A:` + `t` 가 `B:` + `t'` 와
   * 같아질 수 있다(A='R1', B='R1:x'). 구분자를 안 쓰는 것이 그 가정의 나머지 절반이다. */
  assert.ok(
    RULES.every((R) => !R.id.includes(':')),
    '규칙 id 에 vid 구분자(:)가 들어갔다 — 규칙 접두사가 서로 갈리지 않는다',
  );
});

test('실시간 축을 읽는 규칙과 `axis: minute` 표기 집합이 같다 (손으로 붙이는 표기는 반드시 낡는다)', () => {
  /* 화면은 `axis === 'minute'` 인 규칙에만 조회 상태(대기·실패)를 사유로 붙인다. 그 표기가
   * 규칙마다 손으로 붙는 값이면, 새 실시간 규칙이나 리팩터가 한 줄을 빠뜨리는 순간 그 규칙이
   * **API 장애를 "사실 축 부재"로** 말한다 — 고쳤던 오독의 원상복귀다. 표기와 실제 의존을
   * 집합으로 대조해 그 표류를 잡는다(개별 규칙 이름을 하드코딩하면 그 규칙만 지켜진다). */
  const withoutMinute = emptyFacts();
  const withMinuteAxis: Facts = {
    ...emptyFacts(),
    minute: { date: '2026-08-03', sessions: [session({})], deadJobsByDataset: {} },
  };
  const readsMinute = RULES.filter(
    (R) => R.canRun != null && !R.canRun(withoutMinute) && R.canRun(withMinuteAxis),
  ).map((R) => R.id);
  const marked = RULES.filter((R) => R.axis === 'minute').map((R) => R.id);
  assert.deepEqual(marked, readsMinute, '실시간 축 표기와 실제 의존이 어긋난다');
  assert.ok(readsMinute.length >= 3, '실시간 규칙을 하나도 못 찾았다 — 픽스처가 축을 안 채운다');
});

test('못 돌 수 있는 규칙은 저마다 다른 사유 문장을 갖는다 (`dep` 이 빈 규칙은 사유가 통째로 없다)', () => {
  /* `evaluate` 는 못 돈 규칙의 `note` 를 `R.dep` 하나로만 채운다. `axis` 가 있는 규칙은 화면이
   * 조회 상태를 대신 붙이지만, **`canRun` 이 있고 `axis` 가 없는 규칙**은 `dep` 이 사유의
   * 유일한 운반자다 — 비면 그 규칙은 "못 돌았다"만 말하고 **왜인지는 아무 데도 없다**.
   *
   * 이 불변식은 `Rule.dep` 주석이 규약으로 적고 있었지만 아무도 검사하지 않았고, 실제로 R12 가
   * 어겼다(큐 축은 응답에 아예 없어 dev 에서 매일 못 돈다 — 사유 없이). 주석에 걸린 단언은
   * 죽은 단언이다. 문장이 서로 달라야 한다는 것까지 재야 복붙으로 메우는 회피도 막힌다. */
  const needDep = RULES.filter((R) => R.canRun != null && R.axis == null);
  assert.ok(needDep.length >= 5, '대상 규칙을 못 찾았다 — 필터가 낡았다');
  const blank = needDep.filter((R) => !R.dep).map((R) => R.id);
  assert.deepEqual(blank, [], `못 돈 사유가 빈 규칙: ${blank.join('·')}`);
  const deps = needDep.map((R) => R.dep);
  assert.equal(new Set(deps).size, deps.length, '사유 문장이 겹친다 — 규칙 구분이 사라진다');
});

test('vid 왕복 — 엔진이 낸 모든 vid 에서 규칙 id 를 되찾을 수 있다 (소비자가 구분자를 다시 적지 않는다)', () => {
  /* 이 단언이 없으면 `vidOf` 의 구분자를 바꿔도 나머지 테스트가 전부 초록이다. 그 사이 화면의
   * vid→규칙 되찾기는 **영원히 아무것도 못 찾고**, 못 찾은 것을 "그 규칙은 돌았다(해소)"로
   * 그린다 — 아무것도 안 깨지면서 거짓 음성만 나가는 모양이다. 생산자·소비자를 여기서 묶는다. */
  const f = emptyFacts();
  f.runs = [run({ id: 'news:2026-08-03T15:30', ledger_status: 'TIMED_OUT' })]; // 대상에 콜론
  f.tasks = [task({ task_key: 'LOAD_DOCUMENTS', run_id: 'news:2026-08-03T15:30', task_outcome: 'FAILED' })];
  f.outputs = [{ id: 'o.pub', label: '게시', today: 10, base: 100, unit: '건' }]; // 대상에 점
  f.minute = {
    date: '2026-08-03',
    sessions: [
      session({ dataset: 'news_minute', sourceGroup: 'bigkinds', leaseExpired: true }), // 슬래시 + @범위
    ],
    deadJobsByDataset: {},
  };

  const vs = evaluate(f, NOW).violations;
  assert.ok(vs.length >= 4, '왕복을 재려면 여러 축의 vid 가 있어야 한다');
  for (const v of vs) {
    assert.equal(ruleOfVid(v.vid), v.rule, `되찾기 실패: ${v.vid}`);
  }
  /* vid 가 아닌 문자열은 규칙 id 를 주지 않는다 — 호출자가 "모르는 식별자"로 갈라야 한다 */
  assert.equal(ruleOfVid('R17'), '');
  assert.equal(ruleOfVid(''), '');
});

test('vid 충돌 — 그 규칙만 못 돎으로 세우고 나머지 규칙은 산다', () => {
  /* 도달 경로를 **제약 없는 축**에서 고른다. `tasks` 의 중복은 원장이 막는다
   * (`uq_ops_expected_task_run_key UNIQUE (pipeline_run_id, task_key)`) — 거기서 재현하면
   * DB 가 이미 막는 것을 테스트하는 셈이다. `outputs` 는 엔드포인트가 조립하는 축이라
   * 유일성을 보증하는 제약이 없다(`datasets`·`chain.stages` 도 같다). */
  const f = emptyFacts();
  f.outputs = [
    { id: 'o.pub', label: '게시', today: 10, base: 100, unit: '건' },
    { id: 'o.pub', label: '게시(중복 행)', today: 20, base: 100, unit: '건' },
  ];
  /* 다른 규칙이 낼 위반 — 충돌한 규칙 때문에 **이게 사라지면 안 된다**. 던져서 평가를 통째로
   * 죽이면 파이프라인이 깨진 날 콘솔이 오류 카드 하나가 되고, 정작 볼 사건이 전부 없어진다. */
  f.runs = [run({ id: 'dead', ledger_status: 'TIMED_OUT' })];

  const rep = buildReport(f, NOW);
  const r13 = rep.rules.find((r) => r.id === 'R13')!;
  /* 위반 0건이 아니라 **못 돎** 이다 — 뒤엣것을 버리거나 번호를 붙여 비키면 위치 인덱스가
   * 이름만 바꿔 되살아나므로 그 둘은 답이 아니다. */
  assert.equal(r13.evaluated, false);
  assert.equal(r13.violations, 0);
  /* **못 돎의 종류를 구조로 낸다.** 화면이 문구를 파싱하게 두면 안 되고, 무엇보다 이건
   * 계측 공백(`axis`)이 아니라 **응답의 계약 위반**이다 — 같은 칸에 그리면 "아직 계측이
   * 없구나"로 읽힌다(계약 §「배선 시 함께」가 막으려던 오독이 한 층 아래로 옮겨간다).
   * 화면은 이 필드로 갈라 그리고, 사유는 호버가 아니라 본문으로 낸다. */
  assert.equal(r13.notRun, 'identity');
  assert.match(r13.note ?? '', /사건 식별자 충돌 R13:o\.pub/);
  /* 계측 부재는 같은 `evaluated:false` 여도 종류가 다르다 — 둘이 구분되지 않으면 이 필드가 무의미하다 */
  assert.equal(rep.rules.find((r) => r.id === 'R17')!.notRun, 'axis'); // minute 축 부재
  /* 충돌한 규칙의 위반은 하나도 안 실린다 — 반쯤 실으면 무엇이 빠졌는지 화면이 못 말한다 */
  assert.equal(rep.violations.filter((v) => v.rule === 'R13').length, 0);
  // 나머지는 그대로 산다
  assert.deepEqual(rep.violations.filter((v) => v.rule === 'R04').map((v) => v.target), ['dead']);
});

test('범위가 빈 문자열이면 위반 하나만으로도 못 돎이다 (충돌해야 잡히면 시간 축 표류를 놓친다)', () => {
  /* `TaskFact.run_id` 는 `string` 필수라 `''` 가 타입상 합법이다. `??` 는 `''` 를 통과시키는데
   * vid 조립의 truthy 검사는 '없음'으로 읽는다 — 가드와 사용처가 falsy 를 다르게 읽는 자리다.
   *
   * ⚠️ 이걸 **충돌 검사에 맡기면 안 된다**. 위반이 하나뿐이면 겹치지 않아 `R05:T` 라는 정상처럼
   * 보이는 vid 가 나가고, 내일 다른 런이 또 `''` 로 오면 어제 공유한 링크가 오늘 사건을 연다 —
   * 한 스냅샷 안 충돌이 아니라 **시간 축을 가로지르는** 충돌이라 `seen` 이 영원히 못 잡는다.
   * 그래서 모양 검사여야 한다: 위반 **하나**로 재현한다. */
  const f = emptyFacts();
  f.tasks = [task({ task_key: 'T', run_id: '', task_outcome: 'FAILED' })];
  const r05 = buildReport(f, NOW).rules.find((r) => r.id === 'R05')!;
  assert.equal(r05.evaluated, false, '빈 범위가 정상 vid 로 통과했다 — 내일 다른 런과 겹친다');
  assert.equal(r05.notRun, 'identity');
  assert.match(r05.note ?? '', /빈 문자열/);
});

test('대상 축이 빈 문자열이어도 못 돎이다 (사건 키의 나머지 절반에 같은 구멍이 있었다)', () => {
  /* `targetId: ''` 는 `??` 를 통과해 `R13:` 이라는 정상처럼 보이는 vid 를 만든다. 범위 축만
   * 막으면 같은 뿌리의 구멍이 대상 축에 그대로 남는다 — 위반이 하나면 충돌도 안 난다. */
  const f = emptyFacts();
  f.outputs = [{ id: '', label: '게시', today: 10, base: 100, unit: '건' }];
  const r13 = buildReport(f, NOW).rules.find((r) => r.id === 'R13')!;
  assert.equal(r13.evaluated, false, '빈 대상 축이 정상 vid 로 통과했다');
  assert.equal(r13.notRun, 'identity');
  assert.match(r13.note ?? '', /대상 축/);
});

test('리포트 — 사건 키 축이 root·members·violations 에서 같다 (한쪽만 바꾸면 조인이 끊긴다)', () => {
  /* `root` 만 vid 로 올리고 `members` 를 `{rule, target_id}` 로 두면, 같은 작업이 두 런에
   * 걸린 날 멤버 두 줄이 **글자 하나 안 틀리게 같아진다** — root 만 갈리고 멤버는 합쳐 보인다.
   * 그게 이 변경이 없애려던 상황이다. */
  const f = emptyFacts();
  const runId = 'news:2026-08-03T15:30';
  f.runs = [run({ id: runId, lane: 'news', ledger_status: 'TIMED_OUT' })];
  f.tasks = [task({ task_key: 'LOAD_DOCUMENTS', run_id: runId, task_outcome: 'FAILED' })];

  const rep = buildReport(f, NOW);
  const inc = rep.incidents.find((i) => i.members.length > 0)!;
  const member = inc.members[0];
  assert.equal(member.vid, `R05:LOAD_DOCUMENTS@${runId}`);
  /* 멤버 vid 로 위반 행을 실제로 찾을 수 있어야 한다 — 조인이 성립하는지가 계약이다 */
  const joined = rep.violations.find((v) => v.vid === member.vid);
  assert.ok(joined, '멤버 vid 로 위반 행을 못 찾는다 — 리포트 안에서 조인이 끊겼다');
  assert.equal(joined.absorbed_into, inc.root);
  /* 범위를 값으로도 낸다 — 소비자가 `scope ?? run_id` 를 다시 조립하지 않아야 한다 */
  assert.equal(joined.scope, runId);
});

test('R17 — 같은 데이터셋이라도 벤더가 다르면 다른 세션이다 (sourceGroup 을 버리면 겹친다)', () => {
  /* 실제로 가능한 다벤더 상태는 **가격 레인 교체일**이다 — `price_minute` = {toss, kis} 이고
   * 교체 운용이라 바꾸는 날 같은 날짜에 두 세션 행이 남는다(`states.py` 어휘 정본). */
  const f = withMinute([
    session({ dataset: 'price_minute', sourceGroup: 'kis', leaseExpired: true }),
    session({ dataset: 'price_minute', sourceGroup: 'toss', leaseExpired: true }),
  ]);
  assert.deepEqual(
    hits(f, 'R17').map((v) => v.vid),
    ['R17:price_minute/kis@2026-08-03', 'R17:price_minute/toss@2026-08-03'],
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
