import assert from 'node:assert/strict';
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
