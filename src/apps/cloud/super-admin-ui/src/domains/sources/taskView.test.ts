import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import type { TaskStatus } from './types.ts';
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
