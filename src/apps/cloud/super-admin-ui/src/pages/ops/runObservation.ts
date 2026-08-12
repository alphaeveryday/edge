/* 런의 두 관측 축 — DB 원장 투영 · AWS 실행 상태 (ALPHA-738).
 *
 * **둘은 서로 다른 관측값이고 어느 쪽도 다른 쪽을 덮어쓰지 않는다.**
 *   DB 원장 투영 — 파이프라인 운영 원장(ops_pipeline_run 등)에 마지막으로 기록된 실행 상태
 *   AWS 실행 상태 — Step Functions 제어면에서 조회한 실행 상태
 * 값이 다르면 투영 지연이거나 원장 기록 누락일 수 있는데, **이 모듈은 원인을 추정하지 않고**
 * "불일치"라는 사실만 만든다.
 *
 * 부재도 한 가지가 아니다. 0 · 없음 · 미기록 · 관측 없음을 합치면 관대해지는 쪽으로 틀린다:
 *   런 행 없음        — no_run_row 가 true 라고 **명시**된 경우에만
 *   원장 상태 미기록  — 행 존재 여부를 모르는데 status 만 없다(행이 없다고 단정하지 않는다)
 *   AWS 관측 없음     — 제어면 값을 못 받았다(실행이 없었다는 뜻이 아니다)
 *
 * 모르는 enum 을 성공·실패로 접지 않는다 — 원문을 보존하고 크게 드러낸다(fail loud).
 */
import type { RunFact, TaskFact } from '../../rules/types';

/** ui-kit BadgeTone 과 같은 어휘. 순수 모듈이 UI 를 import 하지 않도록 재선언한다 */
export type ViewTone = 'active' | 'neutral' | 'gated' | 'warn' | 'blocked' | 'env';

export type TaskState =
  | '성공'
  | '부분 결손'
  | '대기'
  | '미귀결'
  | '실패'
  | '타임아웃'
  | '미기동'
  | '선행 미충족'
  | '계획 제외'
  | '판정 없음';

const TIMEOUT_REASON = /TIMED_OUT|TIMEOUT/i;

/** Console facts에는 시도 상태가 없다. 시도 이력의 존재와 현재 실행 상태를 혼동하지 않는다. */
export function taskState(t: TaskFact): TaskState {
  if (t.plan_status === 'SKIPPED') return '계획 제외';
  switch (t.task_outcome) {
    case 'FULFILLED':
      return t.data_status === 'INCOMPLETE' || t.data_status === 'INVALID' ? '부분 결손' : '성공';
    case 'FAILED':
      return TIMEOUT_REASON.test(String(t.outcome_reason ?? '')) ? '타임아웃' : '실패';
    case 'MISSED':
      return '미기동';
    case 'BLOCKED':
      return '선행 미충족';
    case 'PENDING':
      return (t.attempts ?? 0) > 0 ? '미귀결' : '대기';
    default:
      return '판정 없음';
  }
}

export type StatusKind =
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timedOut'
  | 'aborted'
  | 'pendingRedrive'
  | 'unknown'
  | 'unsupported';

export interface StatusView {
  kind: StatusKind;
  /** 화면에 쓰는 한국어 라벨. 미지원 값은 원문을 품는다 */
  label: string;
  /** 원본 enum — 툴팁·상세에서 그대로 확인할 수 있어야 한다 */
  raw: string;
  tone: ViewTone;
  /** terminal 귀결인가 — 대조 판정에서 "둘 다 끝났는데 다르다"를 가리는 데 쓴다 */
  terminal: boolean;
}

/**
 * 상태 어휘. AWS Step Functions 의 공식 집합(RUNNING · SUCCEEDED · FAILED · TIMED_OUT ·
 * ABORTED · PENDING_REDRIVE)과 원장 전용 UNKNOWN 을 함께 안다.
 *
 * ⚠️ 미성공 상태를 전부 blocked 로 칠하지 않는다 — PENDING_REDRIVE 는 복구 대기이고,
 * UNKNOWN 은 판정 근거 부족이라 장애로 그리면 없는 사고가 생긴다.
 */
const KNOWN: Record<string, Omit<StatusView, 'raw'>> = {
  RUNNING: { kind: 'running', label: '실행 중', tone: 'env', terminal: false },
  SUCCEEDED: { kind: 'succeeded', label: '성공', tone: 'active', terminal: true },
  FAILED: { kind: 'failed', label: '실패', tone: 'blocked', terminal: true },
  TIMED_OUT: { kind: 'timedOut', label: '시간 초과', tone: 'blocked', terminal: true },
  ABORTED: { kind: 'aborted', label: '중단', tone: 'blocked', terminal: true },
  /* 재구동 대기 — 실패로 끝났지만 redrive 를 기다리는 상태다. 장애가 아니라 복구 대기다 */
  PENDING_REDRIVE: { kind: 'pendingRedrive', label: '재구동 대기', tone: 'warn', terminal: false },
  /* 원장 전용 — 판정 근거가 부족하다는 뜻이지 정상이라는 뜻이 아니다 */
  UNKNOWN: { kind: 'unknown', label: '알 수 없음', tone: 'neutral', terminal: false },
};

export function statusView(raw: string): StatusView {
  const known = KNOWN[raw];
  if (known) return { ...known, raw };
  /* 새 enum 이 배포되면 조용히 성공·실패로 접지 않고 원문을 그대로 드러낸다 */
  return { kind: 'unsupported', label: `미지원 상태 · ${raw}`, raw, tone: 'warn', terminal: false };
}

/** 축이 값을 못 준 이유. 서로 다른 사실이라 한 단어로 합치지 않는다 */
export type AbsenceKind = 'runRowMissing' | 'ledgerStatusMissing' | 'awsNotObserved';

export const ABSENCE_LABEL: Record<AbsenceKind, string> = {
  runRowMissing: '런 행 없음',
  ledgerStatusMissing: '원장 상태 미기록',
  awsNotObserved: 'AWS 관측 없음',
};

export const ABSENCE_MEANING: Record<AbsenceKind, string> = {
  runRowMissing:
    '예정 슬롯은 있는데 ops_pipeline_run 행이 생성되지 않았다(no_run_row 로 명시된 경우에만 쓴다).',
  ledgerStatusMissing:
    '원장 상태 값이 없다. 행 자체가 없는지는 이 응답이 말해 주지 않으므로 "원장 없음"이라고 단정하지 않는다.',
  awsNotObserved:
    '제어면 상태를 받지 못했다. **실행이 없었다는 뜻이 아니다** — 실행 부재를 확정할 필드가 응답에 없다.',
};

export interface Observation {
  /** 관측값이 있으면 status, 없으면 absence. 둘 중 하나만 채워진다 */
  status: StatusView | null;
  absence: AbsenceKind | null;
  /** 그 관측의 시각(ISO). 없으면 null — 지어내지 않는다 */
  at: string | null;
}

/** DB 원장 투영 — AWS 값을 여기 복사하지 않는다 */
export function ledgerObservation(r: RunFact): Observation {
  if (r.no_run_row === true) {
    return { status: null, absence: 'runRowMissing', at: r.ledger_updated ?? null };
  }
  if (r.ledger_status == null) {
    return { status: null, absence: 'ledgerStatusMissing', at: r.ledger_updated ?? null };
  }
  return { status: statusView(r.ledger_status), absence: null, at: r.ledger_updated ?? null };
}

/** AWS 실행 상태 — 원장 값을 여기 복사하지 않는다 */
export function awsObservation(r: RunFact): Observation {
  if (r.aws_status == null) {
    return { status: null, absence: 'awsNotObserved', at: r.aws_stop ?? null };
  }
  return { status: statusView(r.aws_status), absence: null, at: r.aws_stop ?? null };
}

export type ReconcileKind =
  | 'match'
  | 'mismatch'
  | 'ledgerStatusMissing'
  | 'awsNotObserved'
  | 'runRowMissing'
  | 'noObservation';

export interface Reconcile {
  kind: ReconcileKind;
  label: string;
  tone: ViewTone;
  /** 판정 근거 한 줄 — 원인이 아니라 무엇을 비교했는가다 */
  basis: string;
}

/**
 * 두 관측의 대조. **원인을 추정하지 않는다** — 같은가 다른가, 혹은 한쪽이 없는가까지다.
 * 색만으로 갈리지 않게 화면은 이 label 을 텍스트 배지로 낸다.
 */
export function reconcile(r: RunFact): Reconcile {
  const led = ledgerObservation(r);
  const aws = awsObservation(r);

  if (led.absence === 'runRowMissing') {
    return {
      kind: 'runRowMissing',
      label: '런 미생성',
      tone: 'blocked',
      basis: '예정 슬롯은 있는데 런 행이 생성되지 않았다 — 대조할 원장 상태 자체가 없다.',
    };
  }
  if (led.status && aws.status) {
    if (led.status.raw === aws.status.raw) {
      return {
        kind: 'match',
        label: '일치',
        tone: 'active',
        basis: `두 관측이 같은 값이다(${led.status.raw}).`,
      };
    }
    return {
      kind: 'mismatch',
      label: '불일치',
      tone: 'warn',
      basis: `원장 ${led.status.raw} vs AWS ${aws.status.raw} — 어느 쪽으로도 덮어쓰지 않는다. 투영 지연이나 기록 누락일 수 있고 이 화면은 원인을 추정하지 않는다.`,
    };
  }
  if (!led.status && aws.status) {
    return {
      kind: 'ledgerStatusMissing',
      label: '원장 상태 미기록',
      tone: 'warn',
      basis: `AWS 는 ${aws.status.raw} 인데 원장 상태 값이 없다. 런 행이 없다고 단정하지 않는다.`,
    };
  }
  if (led.status && !aws.status) {
    return {
      kind: 'awsNotObserved',
      label: 'AWS 관측 없음',
      tone: 'neutral',
      basis: `원장은 ${led.status.raw} 인데 제어면 값을 받지 못했다. 실행이 없었다는 뜻이 아니다.`,
    };
  }
  return {
    kind: 'noObservation',
    label: '계측 없음',
    tone: 'neutral',
    basis: '두 축 모두 값을 주지 않았다 — 0 도 정상도 아니다.',
  };
}

/** 제어면이 terminal failure 로 끝났는가 — AWS 실패 근거 영역을 세울 조건 */
export function isAwsTerminalFailure(r: RunFact): boolean {
  const s = awsObservation(r).status;
  return s !== null && (s.kind === 'failed' || s.kind === 'timedOut' || s.kind === 'aborted');
}

/**
 * AWS 실패 근거로 **실제 제공되는 필드만** 남긴 목록. 값이 없는 축은 지어내지 않고
 * `계측 없음`으로 표시하도록 null 을 그대로 돌려준다.
 *
 * 현재 응답에 있는 것은 상태와 종료 시각뿐이다 — execution ARN · 실패 state · error · cause ·
 * redrive 상태는 어느 필드에도 없다. 그래서 하단 작업 중 하나를 원인으로 지목하지 않는다.
 */
export function awsFailureEvidence(r: RunFact): { label: string; value: string | null }[] {
  const s = awsObservation(r).status;
  return [
    { label: '상태', value: s?.raw ?? null },
    { label: '종료', value: r.aws_stop ?? null },
    { label: '실패 state', value: null },
    { label: 'error', value: null },
    { label: 'cause', value: null },
    { label: 'execution ARN', value: null },
    { label: 'redrive 상태', value: null },
  ];
}
