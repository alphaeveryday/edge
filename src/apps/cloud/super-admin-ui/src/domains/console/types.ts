/* console 도메인 — `GET /api/v1/console/facts` 의 **와이어 형상**(ALPHA-738 B1·B2b).
 *
 * ⚠️ 이 타입은 규칙 엔진의 `Facts` 가 아니다. 와이어는 camelCase 이고 엔진은 snake_case 이며,
 * 무엇보다 **부재의 규약이 다르다**(계약 §부재를 싣는 규약: 계측 없음이면 서버가 필드를 아예
 * 안 보낸다). 둘을 잇는 어댑터는 **한 곳**이어야 한다(아직 없다 — 화면 조각이 들여온다):
 * 규칙은 화면 도메인을 모르고, 도메인은 규칙을 모른다.
 *
 * 서버가 **안 보내는 축**은 여기에도 없다: `runbook`·
 * `runs[].kind`·`tasks[].maxRetries`. 있는 척 선언하면 어댑터가 `?? []` 로
 * 메우게 되고, 그러면 계측 공백이 실측으로 위조된다.
 */

/** 서버가 준 그대로 — `apiClient` 결과를 캐스팅만 한 것이라 **값 검증이 없다.**
 *  검증은 규칙 층으로 넘기는 어댑터의 일이고, 그 어댑터는 아직 없다(화면 조각이 들여온다). */
export interface ConsoleFactsDto {
  runs: RunDto[];
  tasks: TaskDto[];
  datasets: DatasetDto[];
  outputs: OutputDto[];
  /** 롤백·UI 선배포 시 부재, SQS 관측 실패 시 null이다. */
  queues?: QueueDto[] | null;
  boundary: BoundaryDto;
  /** 🔴 **옵셔널이다** — 이 축을 안 싣는 배포본(롤백·UI 선배포)이 실재할 수 있고, 그때
   *  응답을 거부하면 체인 카드 하나 때문에 **ops 전 화면**이 빈다. 없으면 R10 이 `못 돎`. */
  chain?: ChainDto;
  /** 나중에 붙은 축 — 롤백·UI 선배포 때는 부재할 수 있다. */
  etfLedger?: EtfLedgerDto;
  /**
   * 조회일의 ETF별 v2 분석 귀결(ALPHA-1312) — 흐름 화면의 ETF 표가 그린다. 이 축을 싣기 전의 API
   * 에서는 없다. 없으면 표는 "기록 없음"이고 옛 축(`etfLedger`)으로 대신 그리지 않는다.
   */
  etfAnalyses?: EtfAnalysesDto;
  /**
   * 조회일에 v2 분석이 끝내 완료되지 못한 ETF(ALPHA-1291). 항목은 `outlook`·`movement` 둘이다.
   * `failedEtfs` 는 그날 그 종류의 분석에 실패 행이 있고 완료 행이 하나도 없는 ETF 코드다.
   * **옵셔널이다** — 이 축을 안 싣는 API 에서는 오지 않고, 그때 R15 는 `못 돎` 이다.
   */
  analysisFailures?: { kind: AnalysisTrendKind; failedEtfs: string[] }[];
  meta: MetaDto;
}

/** 뉴스 argument 엔티티 해소율의 최근 일별 실측. `rate: null`은 실제 0/0 관측이다. */
export interface EntityResolutionTrendDto {
  points: EntityResolutionTrendPointDto[];
}

export interface EntityResolutionTrendPointDto {
  date: string;
  totalArguments: number;
  resolvedArguments: number;
  rate: number | null;
}

export type AnalysisTrendKind = 'outlook' | 'movement';

/**
 * v2 분석(전망·가격변동)의 최근 일별 건수(ALPHA-1290). `kinds` 는 항상 두 종류다.
 * `points` 는 **분석 행이 있던 날짜만** 담는다 — 없는 날은 0 이 아니라 기록이 없는 날이다.
 */
export interface AnalysisTrendDto {
  from: string;
  to: string;
  kinds: { kind: AnalysisTrendKind; points: AnalysisTrendPointDto[] }[];
}

/**
 * `running` 은 실패가 아니라 끝나지 않은 건수다. `completedEtfs` 는 그날 완료 행이 있는 ETF 종
 * 수이고, `failedEtfs` 는 **그날 실패 행이 있고 완료 행이 하나도 없는** ETF 종 수다(ALPHA-1291) —
 * 실패했다가 같은 날 다시 돌아 완료된 ETF 는 들어 있지 않다.
 */
export interface AnalysisTrendPointDto {
  date: string;
  completed: number;
  failed: number;
  running: number;
  completedEtfs: number;
  failedEtfs: number;
}

/** 장중 FIRE 코호트가 분석·게시 단계에 도달한 최근 일별 실측. */
export interface IntradayAnalysisTrendDto {
  asOf: string;
  points: IntradayAnalysisTrendPointDto[];
}

export interface IntradayAnalysisTrendPointDto {
  date: string;
  triggers: number;
  observations: number;
  runs: number;
  activeRuns: number;
  failedRuns: number;
  results: number;
  published: number;
}

/** 전망의 그날 상태 — `none` 은 그날 분석 행이 없다는 뜻이다 */
export type OutlookStateDto = 'completed' | 'failed' | 'running' | 'none';
/**
 * 가격변동 분석의 그날 상태. `not_started` 는 트리거에서 이어진 수용된 요청이 있는데 분석 행이 없고
 * 유예 밖인 것, `pending` 은 같은 형상인데 유예 안이라 아직 판정할 수 없는 것이다.
 */
export type MovementStateDto = OutlookStateDto | 'not_started' | 'pending';

export interface EtfAnalysesDto {
  rows: { etf: string; name: string; outlook: OutlookStateDto; movement: MovementStateDto }[];
}

export interface EtfLedgerDto {
  rows: EtfLedgerRowDto[];
}

export interface EtfLedgerRowDto {
  etf: string;
  name: string;
  triggered: boolean;
  outcome: string | null;
  error: string | null;
}

/**
 * `planned`·`noRunRow` 는 **런 행이 없는 계획 슬롯에만** 실린다(서버가 필드 단위 `NON_NULL`).
 * `lane`·`tradingDate` 의 `null` 은 **정상 도달값**이다 — 비거래일 런, 슬롯 키 파싱 실패.
 */
export interface RunDto {
  id: string;
  lane: string | null;
  tradingDate: string | null;
  ledgerStatus: string | null;
  ledgerUpdated: string | null;
  deadline: string | null;
  /** 나중에 붙은 제어면 축 — API 롤백·UI 선배포 동안은 키 자체가 없을 수 있다. */
  awsStatus?: string | null;
  awsStop?: string | null;
  planned?: boolean;
  noRunRow?: boolean;
}

export interface TaskDto {
  taskKey: string;
  /** 런의 `id`(=run_key)와 같은 축 — 사건을 런에 매다는 값이다 */
  runId: string;
  pipelineType: string;
  tradingDate: string | null;
  stage: string;
  dataset: string | null;
  required: boolean;
  planStatus: string;
  taskOutcome: string | null;
  dataStatus: string | null;
  recordsOut: number | null;
  /** API/UI 독립 배포 중 구 API는 이 필드를 아직 생략할 수 있다. */
  unsupportedRecords?: number | null;
  failedRecords: number | null;
  completenessExpected: number | null;
  completenessReceived: number | null;
  completenessMissing: number | null;
  attempts: number;
}

/** 작업에서 파생한 축. `unverifiable` 은 **판정 코드**다(문장이 아니다 — 포맷은 UI 소관). */
export interface DatasetDto {
  id: string;
  contract: boolean;
  expectedAsOf: string | null;
  actualAsOf: string | null;
  collectedAt: string | null;
  unverifiable: string | null;
}

/** `base` 가 `null` 이면 **기준 없음** — 편차 판정 대상이 아니다(휴장일의 장 산출 포함). */
export interface OutputDto {
  id: string;
  label: string;
  today: number;
  base: number | null;
  unit: string;
  /**
   * `base` 를 낸 기준일별 건수(날짜 오름차순, ALPHA-1289). **조회일은 들어 있지 않다.**
   * 선택 필드다 — 이 필드가 없는 API 에서는 오지 않는다. `count` 0 은 실측이다.
   */
  history?: { date: string; count: number }[];
}

export interface QueueDto {
  name: string;
  visible: number;
  inFlight: number;
  dlq: number;
}

export interface BoundaryDto {
  publishedWithoutDelivery: number;
  /** 무효화 통지가 **안 간** 발번만 센다 — 정상 무효화는 여기 안 들어온다 */
  deliveryNowNonpublished: number;
  deliveryRows: number;
  /**
   * 앞의 두 수가 **지금의 게시를 못 본다**(ALPHA-1293). 두 수는 옛 설명 엔진의 게시만 발번과
   * 맞대는데, 그 엔진이 **지금** 멈춰 있을 때 참이다(이 축은 날짜 창을 안 타 조회일과 무관하다).
   * 그때 0 은 정합이 아니라 관측 불가다.
   * 이 필드가 없는 API 에서는 오지 않는다(없으면 종전처럼 읽는다).
   */
  blind?: boolean;
}

/**
 * 설명 생산 체인 — 그 날 발화한 트리거가 단계마다 몇 건 남았나.
 *
 * **두 목록의 순서가 계약이다** — 소비자는 `feeds` 를 각 갈래의 첫 점으로 삼아 `stages` 를
 * 순서대로 인접 비교해 감소를 손실로 읽는다. `feeds[0]` 이 배치, `feeds[1]` 이 장중이다(위치로
 * 읽는다 — id 로 찾지 않는다).
 *
 * 🔴 **수에 `null` 이 없다.** 코호트를 정해 놓고 세므로 "못 셌다"가 없고, 0 은 **아무도 그
 * 단계에 도달 못 했다**는 실측이다. 축이 통째로 없는 응답("안 물어봤다")과는 다른 사실이다.
 * ⚠️ 다만 **0 자체가 위반은 아니다** — R10 은 인접한 두 값의 감소를 보므로 앞 단계도 0 이면
 * 아무 위반도 안 선다.
 */
export interface ChainDto {
  feeds: ChainFeedDto[];
  /** 옛 설명 엔진의 단계다. **화면과 규칙은 더는 읽지 않는다**(ALPHA-1296) — 형상 검증만 한다 */
  stages: ChainStageDto[];
  /**
   * 새 분석 엔진(v2)의 단계(ALPHA-1296) — 분석 요청 → 분석 시작 → 분석 완료 → 게시. 화면과 R10 이
   * 읽는 목록이다. **옵셔널이다**: 이 목록을 안 싣는 API(롤백·UI 선배포)에서는 오지 않고, 그때
   * 체인은 축이 없는 것으로 읽는다(R10 은 `못 돎`).
   */
  analysisStages?: AnalysisStageDto[];
}

/** 수는 조회일에 발화한 장중 트리거 가운데 그 단계에 도달한 트리거 수다. */
export interface AnalysisStageDto {
  id: string;
  label: string;
  /** 항상 `null` — 배치 트리거는 새 분석 엔진의 대상이 아니다(0 이 아니다) */
  batch: number | null;
  intraday: number;
  src: string;
  /** 그날 코호트에 요청이 없고 첫 v2 요청이 조회일보다 뒤일 때 참 — `intraday` 의 0 은 실측이 아니다 */
  blind?: boolean;
  /**
   * 이 단계에 **아직 판정할 수 없는** 트리거 수(ALPHA-1309) — 앞 단계에는 도달했고 이 단계에는 못
   * 갔는데 앞 단계에 도달한 지 유예 시간이 안 지났다. `intraday`(도달)에는 들어 있지 않고 손실도 아니다.
   * 이 필드가 없는 API 에서는 오지 않는다(없으면 0 으로 읽는다 — 종전 동작).
   */
  pending?: number;
}

/** `unit` 이 갈래마다 다르다 — 배치는 ETF 종수, 장중은 발화 건수다. */
export interface ChainFeedDto {
  id: string;
  label: string;
  v: number;
  unit: string;
  src: string;
}

export interface ChainStageDto {
  id: string;
  label: string;
  batch: number;
  intraday: number;
  src: string;
  /**
   * 이 단계를 **셀 수 없다**(ALPHA-1293) — 참이면 두 수의 0 은 실측이 아니다. 옛 설명 엔진이
   * 조회일보다 앞서 멈췄고 그날 코호트에 그 엔진의 행이 없을 때다. 이 필드가 없는 API 에서는
   * 오지 않는다(없으면 종전처럼 읽는다).
   */
  blind?: boolean;
}

/** `today` 는 **응답이 실제로 본 날**이다 — 조회 기준일이지 "지금"도 거래일도 아니다.
 *  `?date=` 를 주면 **그 날짜가 그대로** 실리고(dev 실측), 생략했을 때만 원장이 아는 가장 최근 날이다.
 *  뒤엣것만 적어 두면 과거 조회 응답에 "원장 최신일"이라는 거짓 라벨이 붙는다. */
export interface MetaDto {
  db: string;
  /** 키 부재=미배선, null=조회 실패라 두 형상을 보존한다. */
  aws?: string | null;
  today: string;
  /**
   * 이 응답을 만드는 데 서버가 쓴 시간(ALPHA-1314) — 구간별 밀리초와 전체 밀리초다. 이 필드를 싣기
   * 전의 API 에서는 없다. 화면은 그리지 않고 규칙도 읽지 않는다 — 응답을 직접 볼 때 쓰는 값이다.
   */
  timing?: { totalMs: number; segments: { name: string; kind: string; ms: number }[] };
}
