/* 규칙 엔진 타입 — UI를 모른다 (ALPHA-738).
 *
 * 사실(Facts)은 스냅샷 JSON(또는 추후 API)이 주고, 규칙은 (사실) → 위반[] 순수 함수다.
 * 부재 4구분: 0(실측 0) / null·undefined(집계 없음) / blind(관측 불가) / 필드 자체 부재(계측 없음).
 * 계측 없음인 축은 목값 + mock 플래그로 채워져 있고, 규칙 결과에 mock 이 전파된다.
 */

export type Severity = 'P0' | 'P1' | 'P2';
export type Layer = '런' | '작업' | '데이터셋' | '흐름' | '큐' | '산출' | '경계';
export type FactSource =
  | 'DB_LEDGER'
  | 'S3_LOG'
  | 'AWS_CONTROL'
  | 'AWS_CONTROL+DB_LEDGER'
  | 'CODE'
  | 'SEED'
  | 'MOCK';

export interface RunFact {
  id: string;
  lane: string;
  kind: 'scheduled' | 'manual' | 'backfill';
  trading_date: string;
  ledger_status?: string | null;
  ledger_updated?: string | null;
  aws_status?: string | null;
  aws_stop?: string | null;
  deadline?: string | null;
  planned?: boolean;
  no_run_row?: boolean;
  mock?: boolean;
  note?: string;
  why?: string;
}

export interface TaskFact {
  task_key: string;
  run_id: string;
  run_key?: string;
  pipeline_type: string;
  trading_date?: string;
  stage: string;
  dataset?: string | null;
  required: boolean;
  plan_status?: string;
  /** SKIPPED 작업은 null — plan 축(plan_status)과 outcome 축은 다른 축이라 합치지 않는다 */
  task_outcome: string | null;
  data_status?: string | null;
  records_out?: number | null;
  failed_records?: number | null;
  /** 완전성 분모 — null 이면 분모 미배선(위반 아님, 평가 대상 아님) */
  completeness_expected?: number | null;
  completeness_received?: number | null;
  completeness_missing?: number | null;
  cmpl_mock?: boolean;
  attempts?: number;
  /** 재시도 정책 상한 — null 이면 정책 미선언(계측 없음) */
  max_retries?: number | null;
  retry_mock?: boolean;
  /* 시도 축(ops_task_attempt) — 이 스냅샷은 담지 않았다. 필드 부재는 "계측 없음"이지 0 이 아니다. */
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  last_ok?: string | null;
  ok_rate?: string | null;
  [extra: string]: unknown;
}

export interface DatasetFact {
  id: string;
  lane?: string;
  contract?: boolean;
  window_contract?: boolean;
  expected_as_of?: string | null;
  actual_as_of?: string | null;
  collected_at?: string | null;
  lag_days?: number | null;
  next_run?: string | null;
  /** 신선도 판정 불가 사유 — 있으면 R09 대상 */
  unverifiable?: string | null;
  mock?: boolean;
  why?: string;
}

export interface ChainFeed {
  id: string;
  label: string;
  v: number;
  unit: string;
  src: string;
  note?: string;
}

export interface ChainStage {
  id: string;
  label: string;
  batch?: number | null;
  intraday?: number | null;
  src: string;
  note?: string;
  /** 관측 불가 — 접근 채널 자체가 없다. 0 과 다르다 */
  blind?: boolean;
}

export interface QueueFact {
  name: string;
  purpose?: string;
  visible: number;
  in_flight: number;
  dlq: number;
  /** 큐→구독 서비스 매핑 — 선언 계측이 없어 현재 목 */
  subscribers?: string[];
  sub_mock?: boolean;
}

export interface OutputFact {
  id: string;
  label: string;
  today: number;
  /** 직전 10영업일 중앙값 — null 이면 기준 없음(평가 대상 아님) */
  base?: number | null;
  unit: string;
}

export interface BoundaryFact {
  published_without_delivery: number;
  delivery_now_nonpublished: number;
  sync_cursor_rows?: number;
  seed_note?: string;
}

export interface EtfLedgerRow {
  etf: string;
  name: string;
  triggered: boolean;
  outcome: string;
  error?: string | null;
  published?: boolean;
  delivered?: boolean;
}

export interface EtfLedger {
  rows: EtfLedgerRow[];
  mock?: boolean;
  why?: string;
}

export interface RunbookEntry {
  cmd: string;
  note?: string;
}

export interface Facts {
  runs: RunFact[];
  tasks: TaskFact[];
  datasets: DatasetFact[];
  chain: { feeds: ChainFeed[]; stages: ChainStage[] };
  queues?: QueueFact[];
  outputs: OutputFact[];
  boundary: BoundaryFact;
  /** ETF별 분석 귀결 원장 — 계측이 없으면 아예 부재(undefined) → R15 evaluated:false */
  etf_ledger?: EtfLedger;
  /** `"R05.LOAD_DOCUMENTS"` 또는 `"R15"` 키 → 조치. 없으면 "런북 미등록" */
  runbook: Record<string, RunbookEntry>;
  meta: { db: string; aws: string; today: string };
  [extra: string]: unknown;
}

/** 규칙 run() 이 만드는 원시 위반 — 엔진이 rule 메타를 붙여 Violation 으로 만든다 */
export interface RawViolation {
  target: string;
  title: string;
  metric: number | string;
  unit: string;
  why: string;
  evidence: string;
  drill: [tab: string, anchor: string];
  /** 이 위반이 목데이터 위에서 났다 */
  mock?: boolean;
  /** 로컬 시드 유래 */
  seed?: boolean;
  /** 규칙 기본 심각도를 위반 단위로 덮을 때 */
  sev?: Severity;
  /** 분류(kls) 위반 단위 덮기 */
  kls?: string;
  /** 인과 간선 매칭용 — 이 위반이 속한 런 */
  runId?: string;
  /** R05: FULFILLED 실패가 아니라 상류 미실행(PENDING) 파생 여부 */
  cause?: boolean;
  /** R10: 어느 피드 갈래인가 (batch | intraday) */
  src?: string;
  /** 대상 목록 (예: 실패 ETF 이름들) */
  list?: string[];
  lastok?: string | null;
  okrate?: string | null;
}

export interface Violation extends RawViolation {
  rule: string;
  ruleName: string;
  layer: Layer;
  kls: string;
  sev: Severity;
  dep: string | null;
  vid: string;
}

export interface RuleCtx {
  now: Date;
}

export interface Rule {
  id: string;
  layer: Layer;
  name: string;
  desc: string;
  /** 분류 — 카드 우상단 뱃지 */
  kls: string;
  base: Severity;
  /** 이 규칙이 실제로 돌기 위해 필요한 계측(없으면 목 대체 중) — null 이면 의존 없음 */
  dep: string | null;
  source: FactSource;
  /** 필요한 사실 축이 아예 없으면 false → 리포트에 evaluated:false (돌지 못함 ≠ 조용함) */
  canRun?: (f: Facts) => boolean;
  /** 이 규칙이 읽는 사실이 목으로 채워져 있는가 (위반 0건이어도 표시하기 위함) */
  mockBacked?: (f: Facts) => boolean;
  /** 리포트 note — 예: R07 "분모 배선 작업 3/27" */
  note?: (f: Facts) => string | null;
  run: (f: Facts, ctx: RuleCtx) => RawViolation[];
}

/** 인과 간선: c(자식)가 p(부모)의 결과다. 같은 런이라는 사실만으로는 간선을 긋지 않는다. */
export interface Edge {
  c: string;
  p: string;
  when: (c: Violation, p: Violation) => boolean;
  why: string;
}

export interface Incident {
  root: Violation;
  members: { v: Violation; why: string }[];
  sev: Severity;
  size: number;
}

export interface RuleResult {
  id: string;
  name: string;
  layer: Layer;
  evaluated: boolean;
  violations: number;
  depends_on_mock: boolean;
  note: string | null;
}

export interface Evaluation {
  violations: Violation[];
  incidents: Incident[];
  rules: RuleResult[];
}
