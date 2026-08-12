import type { TaskStatus } from './types.ts';

export type TaskTone = 'active' | 'neutral' | 'gated' | 'warn' | 'blocked' | 'env';

const OUTCOME: Record<string, { label: string; tone: TaskTone }> = {
  FULFILLED: { label: '완료', tone: 'active' },
  FAILED: { label: '실패', tone: 'blocked' },
  MISSED: { label: '미실행', tone: 'blocked' },
  BLOCKED: { label: '선행 미충족', tone: 'warn' },
  PENDING: { label: '대기', tone: 'neutral' },
};

const PENDING_BY_ATTEMPT: Record<string, { label: string; tone: TaskTone }> = {
  RUNNING: { label: '실행 중', tone: 'env' },
  FAILED: { label: '시도 실패', tone: 'blocked' },
  TIMED_OUT: { label: '시도 시간초과', tone: 'blocked' },
  SUCCEEDED: { label: '판정 누락', tone: 'warn' },
};

/** outcome과 현재 시도는 별도 축이다. PENDING일 때만 시도 상태가 표시 판정을 보완한다. */
export function taskStatusView(task: TaskStatus): { label: string; tone: TaskTone } {
  if (task.planStatus === 'SKIPPED') return { label: '계획 제외', tone: 'gated' };
  if (task.outcome === null) return { label: '판정 없음', tone: 'neutral' };
  if (task.outcome === 'PENDING' && task.executionStatus !== null) {
    return PENDING_BY_ATTEMPT[task.executionStatus] ?? { label: task.executionStatus, tone: 'neutral' };
  }
  return OUTCOME[task.outcome] ?? { label: task.outcome, tone: 'neutral' };
}

/** task 딥링크가 있으면 일치 행만 낸다. 못 찾았다고 런 전체로 넓히지 않는다. */
export function tasksInFocus(tasks: TaskStatus[], focusTask?: string): TaskStatus[] {
  return focusTask === undefined ? tasks : tasks.filter((task) => task.taskKey === focusTask);
}
