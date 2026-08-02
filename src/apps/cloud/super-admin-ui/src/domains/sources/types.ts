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

/** Planner 기대 ETF 수와 실제 수집 ETF 수의 대조 결과. null 은 "모름"이지 0이 아니다. */
export interface TaskCompleteness {
  expected: number | null;
  received: number | null;
  missing: number | null;
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
  /** 기대 유니버스를 배선한 작업만 값이 있다. 행 수(recordsOut)와 다른 ETF entity 개수다. */
  completeness: TaskCompleteness | null;
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
  /**
   * 귀결이 아직 없는데(PENDING) 도는 물리 시도가 있는가. outcome 은 wrapper 가 끝날 때 써서
   * 실행 중엔 PENDING 이라 — 이 축이 없으면 "돌고 있다"와 "아직 시작도 안 했다"가 같은 회색
   * 셀이 된다. 귀결이 적힌 셀에서는 항상 false — 죽은 RUNNING 잔재가 판정 끝난 셀을 영구
   * "실행 중"으로 만들지 않기 위해서다(서버가 판정한다 — 화면에서 다시 계산하지 마라).
   */
  running: boolean;
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

/* ---------- Run Overview (ALPHA-683) ---------- */

/**
 * 판정 스펙 §7 의 Run 집계 어휘 — 원장 4축의 재명명이 아니라 스펙이 별도 정의한 파생 요약이고,
 * 파생은 서버 한 곳에서 한다(화면 재계산 금지 — running 플래그와 같은 원칙).
 */
export type OpsStatus = 'IN_PROGRESS' | 'READY' | 'DEGRADED' | 'BLOCKED' | 'UNKNOWN';

/**
 * 귀결 수(fulfilled~pending)는 **필수(required) DUE 작업 기준**이다 — "오늘 발행 가능한가"의
 * 분모는 필수 작업이다. due 만 전체 DUE 수. 화면은 반드시 단위("필수 작업 N개 중")를 붙인다.
 */
export interface OverviewCounts {
  due: number;
  requiredDue: number;
  fulfilled: number;
  failed: number;
  missed: number;
  blocked: number;
  pending: number;
  skipped: number;
}

/** 결함 하나 — 축 원문 그대로. overdue(마감 경과 미귀결)만 서버 시계 판정이다. */
export interface OverviewDefect {
  stage: string;
  taskKey: string;
  outcome: TaskOutcome | null;
  dataStatus: DataStatus | null;
  freshnessStatus: 'UNKNOWN' | 'FRESH' | 'STALE' | null;
  failedRecords: number | null;
  overdue: boolean;
}

/**
 * 레인(pipeline_type)별 최신 런 요약. defects 는 **단계 순**(같은 단계 안은 task_key 사전순 —
 * 실행 순서가 아니다)이라, 첫 원소는 "최초 결함이 속한 단계"까지만 말한다. 첫 원소를 최초
 * 결함 지점으로 그리지 마라 — 정확한 지점은 드릴다운 소관.
 */
export interface OverviewLane {
  pipelineType: string;
  runKey: string;
  tradingDate: string | null;
  /** 계획 시각(ISO). "이 판정이 언제 런 기준인가"의 근거 */
  plannedAt: string | null;
  /**
   * 서버 KST 시계 판정 — Planner 가 오늘 안 돌면 조회는 어제 런을 최신으로 재사용한다.
   * 오늘 화면이 그 사실을 숨기면 지난 판정이 오늘 것처럼 보인다(화면 재계산 금지).
   */
  notToday: boolean;
  launchStatus: LaunchStatus | null;
  orchestrationStatus: OrchestrationStatus | null;
  opsStatus: OpsStatus;
  counts: OverviewCounts;
  defects: OverviewDefect[];
}

export interface SourceOverview {
  lanes: OverviewLane[];
}

/* ---------- 뉴스 계보 — Dataset Explorer (ALPHA-685) ---------- */

/**
 * 모든 건수의 단위는 **문서(기사)**다. withAssertion 은 "추출 성공"이 아니라 "구조화 증거가
 * 남은 문서" — 없음은 NO_EVENT·추출 실패·미실행이 **한 통**이다(그 구분은 RDS 에 없다,
 * S3 quality log 소관 — 문서별 terminal 승격은 후속). 화면은 이 한계를 명시한다.
 */
export interface NewsLineageSummary {
  totalDocuments: number;
  documentsWithAssertion: number;
  documentsUsedInAnalysis: number;
}

export interface NewsLineageDocument {
  documentId: string;
  title: string | null;
  sourceCode: string | null;
  publishedAt: string | null;
  availableAt: string | null;
  assertionCount: number;
  usedInAnalysis: boolean;
}

/** date 는 수집 시각(available_at)의 KST 날짜 필터. null = 전체 누적. */
export interface NewsLineage {
  date: string | null;
  summary: NewsLineageSummary;
  documents: NewsLineageDocument[];
}

/* ---------- holdings 결손 영향 (ALPHA-686) ---------- */

/**
 * 단위: 기대·적재·누락은 **ETF 종**, analyses 는 **설명 결과 건**. snapshotMissing 은 기대
 * 목록 부재 = 영향 범위 계산 불가(UNKNOWN) — 빈 누락 목록(영향 없음)과 **다르다**(스펙 §6.3).
 * 누락의 원인(수집/정제/적재 중 어디)은 이 응답이 단정하지 않는다.
 */
export interface HoldingsImpact {
  runKey: string | null;
  expectedAsOf: string | null;
  expectedCount: number | null;
  /** 기대 ∩ 기준일 적재 — expected = loaded + missing 산술 성립. 계산 불가면 null */
  loadedCount: number | null;
  snapshotMissing: boolean;
  /** 이 기준일 대상 적재 중 미귀결 존재(기준일 축 — 선택 런의 outcome 아님). true 면 결손은 잠정 */
  loadPending: boolean;
  missing: MissingEtf[];
  /** 권장 재실행 안내(정적) — 자동 실행 없음. 결손 없거나 적재 미귀결이면 null */
  recommendedAction: string | null;
}

export interface MissingEtf {
  ourEtfId: string;
  /** null = instrument 행 자체가 없음(프로필 수집까지 결손) — 단축코드만 표시 가능 */
  instrumentId: string | null;
  etfName: string | null;
  /** 빈 목록 = "기준일 분석 없음"이라는 사실 — 결손의 결과라고 단정하지 않는다(오귀인 금지) */
  analyses: AffectedAnalysis[];
}

export interface AffectedAnalysis {
  explanationResultId: string;
  publicationStatus: string | null;
  summary: string | null;
}

/* ---------- 장중 1분 파이프라인 (ALPHA-651) ---------- */

/** minute_ingestion_session.phase — 원장 어휘 그대로 */
export type MinutePhase =
  | 'PLANNED'
  | 'ACTIVE'
  | 'DRAINING'
  | 'DRAINED'
  | 'QC_RUNNING'
  | 'FINALIZED'
  | 'FAILED';

/** minute_ingestion_window.data_status — 원장 어휘 그대로 */
export type MinuteWindowStatus =
  | 'DUE'
  | 'CLAIMED'
  | 'VALID'
  | 'VALID_EMPTY'
  | 'INCOMPLETE'
  | 'MISSING'
  | 'INVALID';

/**
 * 창 상태 집계 + 파생 1개. overdueNoEvidence = 기한(window_end)이 지났는데 DUE/CLAIMED 인 창 —
 * MISSING 판정은 EOD QC 몫이라 장중의 "안 돌았다"는 이 파생으로만 보인다(서버 판정,
 * 화면 재계산 금지). validEmpty(돌았는데 데이터 없음)와 다른 축이다.
 */
export interface MinuteWindowCounts {
  due: number;
  claimed: number;
  valid: number;
  validEmpty: number;
  incomplete: number;
  missing: number;
  invalid: number;
  overdueNoEvidence: number;
}

/** 결손·무증거 창 하나 — 집계의 근거 목록(목록 없는 집계 금지) */
export interface MinuteGapWindow {
  windowStart: string;
  windowEnd: string;
  dataStatus: MinuteWindowStatus;
  /** true = 기한 지난 DUE/CLAIMED(증거 없음). false = 증거 있는 결함(INCOMPLETE 등) */
  noEvidence: boolean;
}

/** job 상태 집계 — waiting 은 PENDING+RETRY_WAIT */
export interface MinuteJobCounts {
  waiting: number;
  claimed: number;
  succeeded: number;
  dead: number;
}

/**
 * 하루 세션 하나의 요약. leaseExpired 는 서버 판정 — null 은 lease 부재(기동 증거 자체가
 * 없음)로, 만료(true = 실행체 증거 끊김)와 다른 사실이다.
 */
export interface MinuteSession {
  sessionId: string;
  dataset: string;
  sourceGroup: string;
  phase: MinutePhase;
  universeVersion: string;
  expectedWindowCount: number;
  processedThrough: string | null;
  contiguousCompleteThrough: string | null;
  heartbeatAt: string | null;
  leaseExpiresAt: string | null;
  leaseExpired: boolean | null;
  windows: MinuteWindowCounts;
  gaps: MinuteGapWindow[];
  priceJobs: MinuteJobCounts;
}

/** date = 세션 날짜(KST). 세션 부재는 빈 목록 — "미가동"이라는 사실이지 오류가 아니다 */
export interface MinuteStatus {
  date: string;
  sessions: MinuteSession[];
  /** 뉴스 job 은 세션 연결 컬럼이 없어 날짜(생성 시각 KST) 축의 별도 집계다 */
  newsJobs: MinuteJobCounts;
}
