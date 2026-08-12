import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import type { TaskOutcome, TaskStatus } from './types.ts';
import { tasksInFocus, taskStatusView } from './taskView.ts';

const task = (o: Partial<TaskStatus> = {}): TaskStatus => ({
  stage: 'raw', taskKey: 'collect', dataset: 'price_daily', planStatus: 'DUE', outcome: 'PENDING',
  dataStatus: null, executionStatus: null, recordsOut: null, failedRecords: null,
  lastFinishedAt: null, expectedAt: null, deadlineAt: null, missedAt: null, fulfilledAt: null, skipReason: null, outcomeReason: null,
  completeness: null, attempts: [], ...o,
});

test('PENDING 표시는 시도 축을 보존한다 — 흐름과 작업 행이 같은 판정을 공유한다', () => {
  assert.equal(taskStatusView(task({ executionStatus: 'RUNNING' })).label, '실행 중');
  assert.equal(taskStatusView(task({ executionStatus: 'FAILED' })).label, '시도 실패');
  assert.equal(taskStatusView(task({ executionStatus: 'TIMED_OUT' })).label, '시도 시간초과');
  assert.equal(taskStatusView(task({ executionStatus: 'SUCCEEDED' })).label, '판정 누락');
  assert.equal(taskStatusView(task()).label, '대기');
});

/* 🔴 이 파일은 PENDING×시도 다섯 갈래만 재고 있었고, 그 결과 `OUTCOME` 표와 앞의 두 가드가
 * 통째로 무방비였다 — **`outcome === null` 을 초록 '완료' 로 바꿔도 전건 초록**이었다
 * (변이 실증, 2026-08-12). 원장이 귀결을 안 쓴 작업이 실 원장 화면에서 완료로 서는 형태다. */
test('🔴 판정이 없거나 결함이면 초록을 쓰지 않는다 — outcome 어휘를 전수로 센다', () => {
  /* 톤이 판정이다. 라벨만 재면 `tone` 을 active 로 바꾸는 변이가 그대로 산다 */
  assert.deepEqual(taskStatusView(task({ outcome: null })), { label: '판정 없음', tone: 'neutral' });
  assert.deepEqual(taskStatusView(task({ outcome: 'FULFILLED' })), { label: '완료', tone: 'active' });
  assert.deepEqual(taskStatusView(task({ outcome: 'FAILED' })), { label: '실패', tone: 'blocked' });
  assert.deepEqual(taskStatusView(task({ outcome: 'MISSED' })), { label: '미실행', tone: 'blocked' });
  assert.deepEqual(taskStatusView(task({ outcome: 'BLOCKED' })), { label: '선행 미충족', tone: 'warn' });
  assert.deepEqual(taskStatusView(task({ outcome: 'PENDING' })), { label: '대기', tone: 'neutral' });

  /* 계획 제외는 **outcome 보다 앞선다** — 가드를 지우면 SKIPPED 가 그 outcome 으로 읽힌다 */
  assert.deepEqual(
    taskStatusView(task({ planStatus: 'SKIPPED', outcome: 'FAILED' })),
    { label: '계획 제외', tone: 'gated' },
  );

  /* 모르는 어휘는 원문을 그대로 — 초록으로도 빨강으로도 접지 않는다(화면은 살려 둔다).
   * ⚠️ 타입상 불가라 캐스팅한다: 어휘의 소유는 data-pipeline 이고 원장이 새 값을 내려도
   * 이 타입은 배포 시점까지 안 늘어난다. 그 갈래를 안 재면 폴백이 죽어도 아무도 모른다. */
  const UNKNOWN = 'WEIRD' as TaskOutcome;
  assert.deepEqual(taskStatusView(task({ outcome: UNKNOWN })), { label: 'WEIRD', tone: 'neutral' });

  /* `active` 를 쓰는 갈래는 **완료 하나뿐**이다 — 이 단언이 "초록으로 새는" 변이 전체를 잡는다 */
  const green = ([...(['FULFILLED', 'FAILED', 'MISSED', 'BLOCKED', 'PENDING'] as const), UNKNOWN])
    .filter((o) => taskStatusView(task({ outcome: o })).tone === 'active');
  assert.deepEqual(green, ['FULFILLED']);
  assert.notEqual(taskStatusView(task({ outcome: null })).tone, 'active');
  assert.notEqual(taskStatusView(task({ planStatus: 'SKIPPED' })).tone, 'active');
});

test('지목한 작업이 없으면 런 전체로 넓히지 않는다', () => {
  const tasks = [task({ taskKey: 'A' }), task({ taskKey: 'B' })];
  assert.deepEqual(tasksInFocus(tasks, 'missing'), []);
  assert.deepEqual(tasksInFocus(tasks).map((t) => t.taskKey), ['A', 'B']);
});

test('실시간 원장 화면은 재조회 오류만으로 직전 세션 근거를 지우지 않는다', () => {
  const source = readFileSync(new URL('../../pages/SourcesPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /if \(isError && !data\) return <LoadError/, '캐시 data가 있어도 오류 화면으로 바뀐다');
  assert.match(source, /실시간 원장 재조회에 실패했습니다 — 직전 실측을 유지합니다/);
});
