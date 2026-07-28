/* sources 도메인 — 데이터 소스 수집 상태. 운영 원장(ops_*) 최신 런을 그대로 받는다(ALPHA-514). */

/** 원래 실행 대상이었나. 원장 plan_status 그대로. */
export type PlanStatus =
  | 'DUE' // 이 런에서 실행 대상
  | 'SKIPPED'; // 계획 단계에서 제외(비거래일 등) — 안 한 게 아니라 할 일이 아니었다

/**
 * 논리 작업의 최종 귀결. 원장 task_outcome 그대로.
 * SKIPPED 작업은 이 값이 null 이다 — plan 축과 outcome 축은 다른 축이라 합치지 않는다.
 */
export type TaskOutcome =
  | 'PENDING' // 아직 귀결 없음
  | 'FULFILLED' // 실행돼서 끝났다
  | 'FAILED' // 실행됐는데 실패
  | 'BLOCKED' // 선행이 안 돼서 진입 못 함
  | 'MISSED'; // 예정됐는데 시작조차 안 됨

/**
 * 산출 데이터의 상태. 실행 성패와 **또 다른 축**이다 — 실행이 FULFILLED 여도 데이터는
 * INCOMPLETE 일 수 있다. 이 축을 안 보여주면 불완전한 산출이 화면에서 온전히 초록으로 보인다.
 */
export type DataStatus = 'UNKNOWN' | 'VALID' | 'VALID_EMPTY' | 'INCOMPLETE' | 'INVALID';

/**
 * 마지막 **시도**의 물리 상태. outcome 은 wrapper 가 끝날 때 쓰므로 실행 중엔 PENDING 이다 —
 * 이 축이 없으면 "돌고 있다"와 "아직 시작도 안 했다"가 화면에서 같은 값이 된다.
 */
export type ExecutionStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT';

/** Planner 의 SFN 기동 결과. **기동 실패는 orchestration 이 영영 null** 이라 이 축이 따로 필요하다. */
export type LaunchStatus =
  | 'PLANNING'
  | 'LAUNCHED'
  | 'LAUNCH_FAILED'
  | 'LAUNCH_CONFLICT'
  | 'LAUNCH_UNKNOWN';

/** SFN 실행 전체의 귀결. 개별 작업 성패와 **다르다** — 런은 실패인데 작업 대부분이 성공일 수 있다. */
export type OrchestrationStatus =
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'TIMED_OUT'
  | 'ABORTED'
  | 'UNKNOWN';

export interface PipelineRun {
  /** 슬롯 멱등키(예: etf-daily:2026-07-27T15:40) — 하루 여러 런을 시각으로 가른다 */
  runKey: string;
  launchStatus: LaunchStatus | null;
  orchestrationStatus: OrchestrationStatus | null;
  /** 대상 거래일. SKIPPED 판정의 근거라 함께 보여준다 */
  tradingDate: string | null;
}

/** 원장 기록 출처. 사후 복구를 정상 계측과 뭉개면 "원장이 스스로 메운 행"이 관측된 실행처럼 보인다. */
export type RecordSource = 'WRAPPER' | 'RECONCILER_BACKFILL';

/** 물리 실행 시도 하나. 재시도가 있으면 여러 건이고, 시각 오름차순이라 **마지막이 최신**이다. */
export interface Attempt {
  /** 표시용. writer 가 안 채울 수 있어 null 가능 */
  attemptNumber: number | null;
  ecsTaskArn: string | null;
  executionStatus: ExecutionStatus;
  startedAt: string | null;
  finishedAt: string | null;
  /** **null 은 "모름"이지 0(성공) 이 아니다** */
  exitCode: number | null;
  failureReason: string | null;
  recordSource: RecordSource | null;
}

export interface TaskStatus {
  /** raw · normalize · feature — 파이프라인 순서 */
  stage: string;
  taskKey: string;
  /** 원장에서 nullable 이다 — 데이터셋이 없는 작업이 있을 수 있다 */
  dataset: string | null;
  planStatus: PlanStatus;
  outcome: TaskOutcome | null;
  /** SKIPPED 작업은 null. UNKNOWN 은 "판정 근거 부족"이지 정상이라는 뜻이 아니다 */
  dataStatus: DataStatus | null;
  /**
   * **현재 시도**의 물리 상태. 시도가 없으면 null.
   *
   * 서버가 고른다 — 마지막 원소가 아니다. 마지막 RUNNING → 원장의 `current_attempt_id` →
   * 순서상 마지막 순으로 해소한다(사후 복구가 시각을 흐트러뜨리고 지목은 작업 종료 시에만
   * 쓰이기 때문). **화면에서 attempts 로 다시 계산하지 마라** — 서버와 다른 답이 나온다.
   */
  executionStatus: ExecutionStatus | null;
  /**
   * 이 작업의 마지막 시도가 낸 건수. **null 은 "모름"이지 0 이 아니다** — 신호가 없거나
   * 못 믿을 값이면 원장이 NULL 로 남긴다(ALPHA-182). 0 으로 표시하면 "0건 처리"와 구분이 사라진다.
   */
  recordsOut: number | null;
  failedRecords: number | null;
  /** ISO 8601. 시도가 없으면 null */
  lastFinishedAt: string | null;
  /** 언제 하기로 했나 */
  expectedAt: string | null;
  /** 언제까지였나 */
  deadlineAt: string | null;
  /**
   * 언제 못 했다고 판정했나. **비래치라 나중에 FULFILLED 로 가도 남는다** — outcome 만 보면
   * "늦게라도 됐다"와 "제때 됐다"가 같은 값이다.
   */
  missedAt: string | null;
  fulfilledAt: string | null;
  /** 왜 계획에서 빠졌나(예: NON_TRADING_DAY) */
  skipReason: string | null;
  /**
   * 왜 그 귀결이 됐나(예: FAILED_TO_START). **시도 행이 없는 실패의 유일한 설명**이다 —
   * ECS ARN 이 안 생긴 submit 실패는 원장에 시도 행 자체를 남기지 않는다.
   */
  outcomeReason: string | null;
  /** 이 작업의 시도 전량(시각 오름차순). 시도가 없으면 빈 배열 */
  attempts: Attempt[];
}

/** 이슈 스코프. Reconciler 가 무엇 단위로 연 불일치인지. */
export type IssueScope = 'run' | 'task' | 'slot';

/**
 * Reconciler 가 연 예정↔실제 불일치. 원장은 판정해 저장하는데 화면이 안 보여주면
 * 운영자에게는 없는 사실이다.
 */
export interface ReconciliationIssue {
  /** MISSED·STALLED·INCOMPLETE·LEDGER_GAP·… 원장 어휘 그대로(새 값이 추가될 수 있다) */
  issueType: string;
  scope: IssueScope | null;
  /** scope 가 task 일 때 그 작업 키. 그 외에는 null */
  taskKey: string | null;
  status: 'OPEN' | 'RESOLVED';
  occurrenceCount: number;
  firstSeenAt: string | null;
  lastSeenAt: string | null;
  resolutionReason: string | null;
}

export interface SourceReport {
  /** 지목한(또는 최신) 런. 원장에 런이 하나도 없으면 null(초기 환경 — 에러가 아니다) */
  run: PipelineRun | null;
  tasks: TaskStatus[];
  issues: ReconciliationIssue[];
}

/* ---------- 실행 격자 (ALPHA-594) ---------- */

/**
 * 격자 셀 하나 — 한 슬롯에서 한 작업의 관측 상태. 축 분리(plan·outcome·data)와 건수 null
 * 계약(모름 ≠ 0)은 TaskStatus 와 같다. 시도·시각 축은 안 온다 — 셀에서 드릴다운으로 넘어가 본다.
 */
export interface GridCell {
  stage: string;
  taskKey: string;
  planStatus: PlanStatus;
  outcome: TaskOutcome | null;
  dataStatus: DataStatus | null;
  recordsOut: number | null;
  failedRecords: number | null;
  skipReason: string | null;
  outcomeReason: string | null;
}

/** 격자 한 열 — 슬롯(런) 하나. tasks 가 빈 런(기동 실패 등)도 열로 온다 — 부재가 1급 신호다. */
export interface GridSlot {
  runKey: string;
  launchStatus: LaunchStatus | null;
  orchestrationStatus: OrchestrationStatus | null;
  tradingDate: string | null;
  tasks: GridCell[];
}

/** 실행 격자 응답. slots 는 계획 시각 오름차순 — **배열 순서가 곧 표시 순서**다. */
export interface SourceGrid {
  days: number;
  slots: GridSlot[];
}
