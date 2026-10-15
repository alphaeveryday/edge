/* 런의 두 관측 축 (ALPHA-738).
 *
 * 지키는 의도:
 *   · DB 원장과 AWS 는 **서로 다른 관측**이다 — 한쪽 값이 다른 열로 새면 안 된다.
 *   · RUNNING vs FAILED 는 모순이 아니라 **투영 불일치**다. 어느 쪽으로도 덮어쓰지 않는다.
 *   · 부재는 한 가지가 아니다 — 런 행 없음 / 원장 상태 미기록 / AWS 관측 없음을 가른다.
 *   · 모르는 enum 을 성공·실패로 접지 않는다.
 *
 * 실행: node --test src/pages/ops/runObservation.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ABSENCE_LABEL,
  awsFailureEvidence,
  awsObservation,
  completenessText,
  isAwsTerminalFailure,
  ledgerObservation,
  reconcile,
  statusView,
  taskState,
} from './runObservation.ts';
import type { RunFact, TaskFact } from '../../rules/types.ts';

const run = (o: Partial<RunFact> = {}): RunFact => ({
  id: 'etf-daily:2026-08-03T15:40',
  lane: 'etf-daily',
  kind: 'scheduled',
  trading_date: '2026-08-03',
  ...o,
});

/* ── 상태 어휘 ── */

test('Step Functions 공식 상태 집합을 전부 안다', () => {
  const expected: [string, string][] = [
    ['RUNNING', '실행 중'],
    ['SUCCEEDED', '성공'],
    ['FAILED', '실패'],
    ['TIMED_OUT', '시간 초과'],
    ['ABORTED', '중단'],
    ['PENDING_REDRIVE', '재구동 대기'],
  ];
  for (const [raw, label] of expected) {
    const v = statusView(raw);
    assert.equal(v.label, label, raw);
    assert.equal(v.raw, raw, '원본 enum 을 항상 들고 다닌다');
    assert.notEqual(v.kind, 'unsupported', raw);
  }
  /* 원장 전용 어휘 */
  assert.equal(statusView('UNKNOWN').label, '알 수 없음');
});

test('미성공 상태를 전부 장애로 칠하지 않는다', () => {
  assert.equal(statusView('FAILED').tone, 'blocked');
  assert.equal(statusView('TIMED_OUT').tone, 'blocked');
  assert.equal(statusView('ABORTED').tone, 'blocked');
  /* 재구동 대기는 복구 대기, 알 수 없음은 판정 근거 부족 — 장애로 그리면 없는 사고가 생긴다 */
  assert.equal(statusView('PENDING_REDRIVE').tone, 'warn');
  assert.equal(statusView('UNKNOWN').tone, 'neutral');
  assert.equal(statusView('RUNNING').tone, 'env');
});

test('모르는 enum 은 성공·실패로 접지 않고 원문을 드러낸다', () => {
  const v = statusView('SOMETHING_NEW');
  assert.equal(v.kind, 'unsupported');
  assert.match(v.label, /미지원 상태/);
  assert.match(v.label, /SOMETHING_NEW/);
  assert.equal(v.raw, 'SOMETHING_NEW');
  assert.notEqual(v.tone, 'active');
  assert.notEqual(v.tone, 'blocked');
  assert.equal(v.terminal, false, 'terminal 로 치면 대조가 "일치/불일치"를 단정하게 된다');
});

/* ── 두 축이 섞이지 않는다 ── */

test('AWS 상태가 원장 열로 복사되지 않는다', () => {
  const r = run({ ledger_status: null, aws_status: 'FAILED', aws_stop: 'x' });
  const led = ledgerObservation(r);
  assert.equal(led.status, null, '원장 열에는 AWS 값이 오지 않는다');
  assert.equal(led.absence, 'ledgerStatusMissing');
  assert.equal(awsObservation(r).status?.raw, 'FAILED');
});

test('원장 상태가 AWS 열로 복사되지 않는다', () => {
  const r = run({ ledger_status: 'SUCCEEDED', aws_status: null });
  assert.equal(awsObservation(r).status, null);
  assert.equal(awsObservation(r).absence, 'awsNotObserved');
  assert.equal(ledgerObservation(r).status?.raw, 'SUCCEEDED');
});

/* ── 부재 구분 ── */

test('no_run_row 와 ledger_status=null 은 다른 사실이다', () => {
  const noRow = ledgerObservation(run({ no_run_row: true, ledger_status: null }));
  const noStatus = ledgerObservation(run({ ledger_status: null }));
  assert.equal(noRow.absence, 'runRowMissing');
  assert.equal(noStatus.absence, 'ledgerStatusMissing');
  assert.notEqual(ABSENCE_LABEL[noRow.absence!], ABSENCE_LABEL[noStatus.absence!]);
  /* status 만 없는 것을 "런 행 없음"으로 승격하지 않는다 */
  assert.notEqual(noStatus.absence, 'runRowMissing');
});

test('aws_status=null 을 "AWS 실행 없음"으로 단정하지 않는다', () => {
  const aws = awsObservation(run({ aws_status: null }));
  assert.equal(aws.absence, 'awsNotObserved');
  assert.equal(ABSENCE_LABEL.awsNotObserved, 'AWS 관측 없음');
  assert.doesNotMatch(ABSENCE_LABEL.awsNotObserved, /실행 없음/);
});

/* ── 대조 ── */

test('RUNNING(원장) vs FAILED(AWS) 는 불일치다 — 어느 쪽도 덮어쓰지 않는다', () => {
  const r = run({
    ledger_status: 'RUNNING',
    ledger_updated: '2026-08-03T16:10:36+09:00',
    aws_status: 'FAILED',
    aws_stop: '2026-08-03T16:12:27+09:00',
  });
  const v = reconcile(r);
  assert.equal(v.kind, 'mismatch');
  assert.equal(v.label, '불일치');
  /* 두 관측이 그대로 살아 있어야 한다 */
  assert.equal(ledgerObservation(r).status?.label, '실행 중');
  assert.equal(awsObservation(r).status?.label, '실패');
  /* 원인을 단정하지 않는다 */
  assert.match(v.basis, /원인을 추정하지 않는다/);
});

test('같은 값이면 일치, 한쪽이 없으면 그 사실이 대조 결과다', () => {
  assert.equal(reconcile(run({ ledger_status: 'SUCCEEDED', aws_status: 'SUCCEEDED' })).kind, 'match');
  assert.equal(reconcile(run({ ledger_status: 'TIMED_OUT', aws_status: 'TIMED_OUT' })).kind, 'match');
  assert.equal(reconcile(run({ ledger_status: null, aws_status: 'FAILED' })).kind, 'ledgerStatusMissing');
  assert.equal(reconcile(run({ ledger_status: 'RUNNING', aws_status: null })).kind, 'awsNotObserved');
  assert.equal(reconcile(run({ no_run_row: true })).kind, 'runRowMissing');
  assert.equal(reconcile(run({})).kind, 'noObservation');
});

test('런 미생성이 다른 대조 판정을 앞선다 — 대조할 원장 값 자체가 없다', () => {
  /* 행이 없는데 AWS 값이 있어도 "원장 상태 미기록"이 아니라 "런 미생성"이다 */
  const v = reconcile(run({ no_run_row: true, aws_status: 'FAILED' }));
  assert.equal(v.kind, 'runRowMissing');
});

/* ── AWS 실패 근거 ── */

test('AWS terminal failure 만 근거 영역을 연다', () => {
  for (const s of ['FAILED', 'TIMED_OUT', 'ABORTED']) {
    assert.equal(isAwsTerminalFailure(run({ aws_status: s })), true, s);
  }
  for (const s of ['SUCCEEDED', 'RUNNING', 'PENDING_REDRIVE']) {
    assert.equal(isAwsTerminalFailure(run({ aws_status: s })), false, s);
  }
  assert.equal(isAwsTerminalFailure(run({ aws_status: null })), false);
});

test('없는 실패 상세를 지어내지 않는다 — 상태·종료만 값이 있다', () => {
  const ev = awsFailureEvidence(run({ aws_status: 'FAILED', aws_stop: '2026-08-03T16:12:27+09:00' }));
  const have = ev.filter((x) => x.value !== null).map((x) => x.label);
  assert.deepEqual(have, ['상태', '종료'], '응답에 있는 축만 값을 갖는다');
  /* 나머지는 null 로 남아 화면이 "계측 없음"으로 그린다 — 빈 문자열·추정값을 넣지 않는다 */
  for (const key of ['실패 state', 'error', 'cause', 'execution ARN', 'redrive 상태']) {
    assert.equal(ev.find((x) => x.label === key)!.value, null, key);
  }
});

test('PENDING 작업은 시도 이력이 있으면 미기동 대기로 숨기지 않되 실행 중이라 추정하지 않는다', () => {
  const task = {
    task_key: 'collect', run_id: 'run-1', pipeline_type: 'news', stage: 'raw', required: true,
    task_outcome: 'PENDING', attempts: 1,
  } as TaskFact;
  assert.equal(taskState(task), '미귀결');
  assert.equal(taskState({ ...task, attempts: 0 }), '대기');
});

/* 🔴 `taskState` 는 단언이 PENDING×attempts 두 줄뿐이라 **판정 변이 넷이 살아남았다**
 * (변이 실증, 2026-08-12). 그중 최악은 `data_status === 'INVALID'` 를 지우는 것 — 무효 데이터
 * 작업이 `'성공'` 이 되고 `RunAxisPage` 의 `STATE_TONE.성공 = 'active'` 라 **초록으로 선다**.
 * 이 콘솔이 존재하는 이유가 그 반대라, 같은 계약을 `freshness` 처럼 갈래 전수로 못박는다.
 * ⚠️ 각 갈래를 **서로 다른 라벨**로 단언한다 — 두 갈래가 같은 값을 기대하면 맞바꿔도 안 잡힌다. */
test('🔴 결함 있는 데이터를 초록으로 그리지 않는다 — outcome × data_status 갈래를 전수로 센다', () => {
  /* ⚠️ 기본값에 `data_status: 'VALID'` 를 두지 않는다 — 각 갈래가 자기 축을 명시하게 한다 */
  const t = (o: Partial<TaskFact>): TaskFact =>
    ({
      task_key: 'PRICE_COLLECTION_KIS', run_id: 'etf-daily:2026-08-03T15:40',
      pipeline_type: 'etf-daily', stage: 'raw', dataset: 'price_daily',
      required: true, plan_status: 'DUE', task_outcome: null, ...o,
    }) as TaskFact;

  /* FULFILLED 라도 데이터가 상하면 성공이 아니다. 두 값 **각각**을 단언한다 */
  assert.equal(taskState(t({ task_outcome: 'FULFILLED', data_status: 'INVALID' })), '부분 결손');
  assert.equal(taskState(t({ task_outcome: 'FULFILLED', data_status: 'INCOMPLETE' })), '부분 결손');
  /* 대조군 — 성한 데이터는 성공이어야 한다. 없으면 위 둘은 "전부 부분 결손" 변이로도 통과한다 */
  assert.equal(taskState(t({ task_outcome: 'FULFILLED', data_status: 'VALID' })), '성공');
  assert.equal(taskState(t({ task_outcome: 'FULFILLED', data_status: null })), '성공');

  /* 귀결 어휘 — 넷이 서로 다른 칸이다. 라벨을 맞바꾸면 미기동이 선행 미충족으로 읽힌다 */
  assert.equal(taskState(t({ task_outcome: 'MISSED' })), '미기동');
  assert.equal(taskState(t({ task_outcome: 'BLOCKED' })), '선행 미충족');
  assert.equal(taskState(t({ task_outcome: 'FAILED' })), '실패');
  assert.equal(taskState(t({ task_outcome: 'WEIRD_NEW_VALUE' })), '판정 없음');

  /* 계획 제외는 **outcome 보다 앞선다** — 이 가드를 지우면 SKIPPED 가 '판정 없음' 으로 샌다 */
  assert.equal(taskState(t({ plan_status: 'SKIPPED', task_outcome: null })), '계획 제외');
  assert.equal(taskState(t({ plan_status: 'SKIPPED', task_outcome: 'FAILED' })), '계획 제외');

  /* 타임아웃은 사유 문자열로만 갈린다 — 정규식을 무력화하면 전부 '실패' 로 접힌다 */
  assert.equal(taskState(t({ task_outcome: 'FAILED', outcome_reason: 'States.Timeout TIMED_OUT' })), '타임아웃');
  assert.equal(taskState(t({ task_outcome: 'FAILED', outcome_reason: 'boto ConnectionError' })), '실패');

  /* 갈래가 서로 다른 값을 낸다는 것 자체를 센다 — 하나로 뭉개는 변이를 잡는다 */
  const labels = new Set([
    taskState(t({ task_outcome: 'FULFILLED', data_status: 'VALID' })),
    taskState(t({ task_outcome: 'FULFILLED', data_status: 'INVALID' })),
    taskState(t({ task_outcome: 'MISSED' })),
    taskState(t({ task_outcome: 'BLOCKED' })),
    taskState(t({ task_outcome: 'FAILED' })),
    taskState(t({ plan_status: 'SKIPPED', task_outcome: null })),
  ]);
  assert.equal(labels.size, 6, '서로 다른 사실이 같은 칸으로 접혔다');
});

test('PENDING_REDRIVE 는 terminal 이 아니다 — 복구 대기를 끝난 실패로 세면 대조가 거짓이 된다', () => {
  assert.equal(statusView('PENDING_REDRIVE').terminal, false);
  assert.equal(statusView('PENDING_REDRIVE').kind, 'pendingRedrive');
  /* 대조군 — 진짜 terminal 셋. 없으면 "전부 false" 변이가 통과한다 */
  for (const raw of ['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED']) {
    assert.equal(statusView(raw).terminal, true, raw);
  }
  assert.equal(statusView('RUNNING').terminal, false);
  assert.equal(statusView('UNKNOWN').terminal, false);
});

test('완전성 분자 부재는 0이나 null 문자열이 아니라 집계 부재로 표시한다', () => {
  const task = {
    task_key: 'collect', run_id: 'run-1', pipeline_type: 'news', stage: 'raw', required: true,
    task_outcome: 'FULFILLED', completeness_expected: 100, completeness_received: null,
  } as TaskFact;
  assert.equal(completenessText(task), '집계 없음 / 기대 100');
  assert.equal(completenessText({ ...task, completeness_received: 0 }), '0/100');
  assert.equal(completenessText({ ...task, completeness_expected: null }), null);
});
