import type { Attempt, QualityDiagnosticIssue, QualityDiagnostics, TaskStatus } from './types.ts';

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

/** 성공 시도도 품질 진단이 있으면 상세를 열어 INCOMPLETE의 이유를 숨기지 않는다. */
export function attemptNeedsDetail(attempt: Attempt): boolean {
  return (
    attempt.executionStatus !== 'SUCCEEDED' ||
    attempt.exitCode !== 0 ||
    attempt.failureReason !== null ||
    attempt.recordSource === 'RECONCILER_BACKFILL' ||
    attempt.qualityDiagnostics != null
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isQualityIssue(value: unknown): value is QualityDiagnosticIssue {
  if (!isObject(value) || !isObject(value.sample)) return false;
  return (
    isNonEmptyString(value.reason) &&
    isNonEmptyString(value.role) &&
    isNonEmptyString(value.expression) &&
    isNonNegativeInteger(value.count) &&
    value.count > 0 &&
    isNonEmptyString(value.sample.articleId) &&
    isNonEmptyString(value.sample.title)
  );
}

/** DB/API의 JSON 경계에서 내부 구조를 검증해 비정상 진단 하나가 원장 전체를 가리지 않게 한다. */
export function parseQualityDiagnostics(value: unknown): QualityDiagnostics | null {
  if (
    !isObject(value) ||
    value.schema !== 'news_resolution_v1' ||
    (value.scope !== 'assertion_arguments' && value.scope !== 'anchorless_events') ||
    !isObject(value.metrics) ||
    !Array.isArray(value.issues) ||
    value.issues.length > 10 ||
    !value.issues.every(isQualityIssue)
  ) {
    return null;
  }

  const requiredMetrics = value.scope === 'assertion_arguments'
    ? ['total', 'resolved', 'unresolved']
    : ['events', 'anchorless', 'unresolvedArguments'];
  const metrics = value.metrics;
  if (!requiredMetrics.every((key) => isNonNegativeInteger(metrics[key]))) return null;

  return value as unknown as QualityDiagnostics;
}

export function qualityDiagnosticsSummary(value: QualityDiagnostics): string {
  const m = value.metrics;
  if (value.scope === 'assertion_arguments') {
    return `인자 ${m.total ?? '—'} · 해소 ${m.resolved ?? '—'} · 미해소 ${m.unresolved ?? '—'}`;
  }
  if (value.scope === 'anchorless_events') {
    return `이벤트 ${m.events ?? '—'} · 기준 종목 없음 ${m.anchorless ?? '—'} · 미해소 인자 ${m.unresolvedArguments ?? '—'}`;
  }
  return value.scope;
}

const REASON_LABEL: Record<string, string> = {
  instrument_not_found: '종목 없음',
  instrument_ambiguous: '종목 모호',
  registry_miss: '레지스트리 없음',
  concept_rejected: '개념 생성 거부',
  arguments_missing: '인자 없음',
};

export function qualityIssueText(issue: QualityDiagnosticIssue): string {
  const reason = REASON_LABEL[issue.reason] ?? issue.reason;
  return `${reason} · ${issue.role} · ${issue.expression} · ${issue.count}건 · ${issue.sample.articleId} · ${issue.sample.title}`;
}
