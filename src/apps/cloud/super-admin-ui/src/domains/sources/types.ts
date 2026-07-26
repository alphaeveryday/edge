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
  /** 마지막 시도의 물리 상태. 시도가 없으면 null */
  executionStatus: ExecutionStatus | null;
  /**
   * 이 작업의 마지막 시도가 낸 건수. **null 은 "모름"이지 0 이 아니다** — 신호가 없거나
   * 못 믿을 값이면 원장이 NULL 로 남긴다(ALPHA-182). 0 으로 표시하면 "0건 처리"와 구분이 사라진다.
   */
  recordsOut: number | null;
  failedRecords: number | null;
  /** ISO 8601. 시도가 없으면 null */
  lastFinishedAt: string | null;
}

export interface SourceReport {
  /** 최신 런. 원장에 런이 하나도 없으면 null(초기 환경 — 에러가 아니다) */
  run: PipelineRun | null;
  tasks: TaskStatus[];
}
