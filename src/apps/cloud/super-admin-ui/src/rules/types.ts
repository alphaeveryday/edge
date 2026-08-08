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

/**
 * 실시간(1분) 수집 세션 한 건 — 규칙이 읽는 **최소 구조**다.
 *
 * ⚠️ API DTO(`domains/sources/types.ts` 의 `MinuteSession`)를 여기로 import 하지 않는다.
 * 규칙은 사실만 읽는 층이고 화면 도메인을 모른다 — 반대로 끌어오면 계층이 뒤집힌다.
 * 대신 호출자가 DTO 를 이 모양으로 맞춰 넣는다(`minuteFacts` 어댑터).
 *
 * `jobs` 는 **그 데이터셋의 후속 처리 원장**이다. 가격은 세션에 붙은 job, 뉴스는 세션 연결
 * 컬럼이 없어 날짜 축 집계다 — 어느 쪽을 넣을지는 어댑터가 정하고 규칙은 모른다.
 */
export interface MinuteSessionFact {
  dataset: string;
  /**
   * 세션 identity 의 두 번째 축 — 한 세션은 `(dataset, sourceGroup, date)` 다.
   * 데이터셋만으로는 안 갈린다(같은 `news_minute` 를 벤더별로 따로 돌린다).
   */
  sourceGroup: string;
  /** 원장 어휘 그대로(ACTIVE·DRAINED·QC_RUNNING·FINALIZED·FAILED). 모르는 값은 그대로 둔다 */
  phase: string;
  /** 서버(DB 시계) 판정. `null` 은 lease 부재 — 만료(true)와 다른 사실이다 */
  leaseExpired: boolean | null;
  /** 기한이 지났는데 실행·결과 증거가 없는 창 수 */
  overdueNoEvidence: number;
  /** 후속 처리 원장에서 종료 상태 실패로 남은 건수 */
  deadJobs: number;
}

/** 하루치 실시간 세션 — 원장에 `minute_ingestion_session/window` 축이 없으면 통째로 부재다 */
export interface MinuteFacts {
  /** 세션 날짜(KST) */
  date: string;
  sessions: MinuteSessionFact[];
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
  /**
   * 실시간 수집 세션 — **스냅샷에는 없다**(facts-snapshot 은 배치 원장만 담는다).
   * 화면이 `/sources/minute` 응답을 실어 줄 때만 채워지고, 없으면 R17~R19 는
   * evaluated:false 다 — "위반 0건"이 아니라 "못 돌았다"로 남는다.
   */
  minute?: MinuteFacts;
  /** `"R05.LOAD_DOCUMENTS"` 또는 `"R15"` 키 → 조치. 없으면 "런북 미등록" */
  runbook: Record<string, RunbookEntry>;
  meta: { db: string; aws: string; today: string };
  [extra: string]: unknown;
}

/** 규칙 run() 이 만드는 원시 위반 — 엔진이 rule 메타를 붙여 Violation 으로 만든다.
 *
 * **필드 규약 — 한 필드는 한 용도다.** 이전에는 세 필드가 서로의 일을 했다: `unit` 이 레인
 * 이름(`news`)과 비교 문장(`기대 … 실제 …`)을 담고, `metric` 이 `'-50%'`·`'STALE'` 같은
 * 문자열을 담고, `target` 이 `'o.pub'` 같은 내부 id 였다. 그래서 화면이
 * `typeof metric === 'number'` 로 용도를 **추측해서** 갈라 그렸다 — 룰이 새 문자열을 넣으면
 * 화면이 조용히 다르게 그린다. 규약을 여기 박아 그 추측을 없앤다.
 */
export interface RawViolation {
  /** 사람이 읽을 대상 — 표 셀에 그대로 선다. 내부 id 를 넣지 않는다 */
  target: string;
  /** 안정 식별자 — **무엇이 고장났나**. 런북 키 `${rule}.${targetId}` · 간선 매칭.
   *  생략하면 `target` 이 그 역할을 겸한다(작업 키·데이터셋 id·큐 이름처럼 이미 둘 다인 경우).
   *
   *  ⚠️ **여기에 시각·날짜를 넣지 마라.** 런북은 "이 대상이 고장나면 이렇게 조치한다"라
   *  날짜와 무관한데, 날짜가 섞이면 키가 매일 달라져 **어떤 런북도 등록할 수 없게 된다**.
   *  시점 축은 `scope` 다. */
  targetId?: string;
  title: string;
  /** 세는 값. **양이 아니면 `null`** — 판정 문자열은 `state` 로 간다 */
  metric: number | null;
  /** `metric` 의 단위. `metric` 이 `null` 이면 단위도 없다. 문맥을 여기 넣지 않는다 */
  unit?: string;
  /** 룰이 내린 판정 어휘(`STALE`·`TIMED_OUT`·`미귀결`) — 세는 값이 아니라 상태다 */
  state?: string;
  /** 문맥 — 기대 대비 실제, 비교 대상, 어느 레인인가. 단위 자리에 문장을 넣지 않는다 */
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
  /**
   * 사건 식별자의 **시점 범위** — 이 위반이 어느 실행 인스턴스의 것인가.
   *
   * `targetId`(무엇이) 와 다른 축이다. 배치는 런 키가 그 역할을 겸하므로 생략하고, 런이 없는
   * 실시간 세션만 세션 날짜를 싣는다. 정규화(`scope ?? runId`)는 **엔진이 한 번만** 한다 —
   * 소비자가 각자 폴백을 쓰면 한 곳만 빠뜨려도 키가 갈린다(`targetId` 와 같은 규약).
   */
  scope?: string;
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
  /** 항상 채워진다 — 엔진이 `raw.targetId ?? raw.target` 으로 정규화한다.
   *  소비자(런북 키·간선·조사 경로)는 이 필드만 보면 되고 폴백을 각자 쓰지 않는다. */
  targetId: string;
  /** 엔진이 `raw.scope ?? raw.runId` 로 정규화한 값 — 소비자는 이 필드만 본다.
   *  범위가 없는 대상(큐·산출처럼 실행에 안 매인 것)은 `undefined` 다. */
  scope?: string;
  rule: string;
  ruleName: string;
  layer: Layer;
  kls: string;
  sev: Severity;
  dep: string | null;
  /**
   * 사건 식별자 — 딥링크(`/ops/incidents/detail?vid=…`)가 쥐는 축이다.
   * `${rule}:${targetId}` + 범위가 있으면 `@${scope ?? runId}`.
   *
   * **위치 인덱스(`R05#0`)였다.** 앞 위반이 해소되면 뒤가 당겨져 공유된 링크가 404 도 없이
   * **다른 사건**을 열었다. 정적 스냅샷은 이걸 못 보여준다 — 위반 집합이 안 바뀌기 때문이다.
   *
   * `targetId` 만으로는 부족하다: R05·R06·R16 의 대상은 `task_key` 라 같은 작업이 여러 런에
   * 걸리면 겹친다(셋 다 `runId` 를 들고 있어 그게 범위가 된다). 런이 없는 실시간 R17~R19 는
   * 세션 날짜를 `scope` 로 싣는다 — `targetId` 에 넣으면 런북 키가 매일 달라진다.
   */
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
