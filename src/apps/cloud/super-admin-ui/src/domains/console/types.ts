/* console 도메인 — `GET /api/v1/console/facts` 의 **와이어 형상**(ALPHA-738 B1·B2b).
 *
 * ⚠️ 이 타입은 규칙 엔진의 `Facts` 가 아니다. 와이어는 camelCase 이고 엔진은 snake_case 이며,
 * 무엇보다 **부재의 규약이 다르다**(계약 §부재를 싣는 규약: 계측 없음이면 서버가 필드를 아예
 * 안 보낸다). 둘을 잇는 어댑터는 **한 곳**이어야 한다(아직 없다 — 화면 조각이 들여온다):
 * 규칙은 화면 도메인을 모르고, 도메인은 규칙을 모른다.
 *
 * 서버가 **안 보내는 축**은 여기에도 없다: `chain`·`queues`·`etfLedger`·`runbook`·`meta.aws`·
 * `runs[].kind`·`runs[].awsStatus`·`tasks[].maxRetries`. 있는 척 선언하면 어댑터가 `?? []` 로
 * 메우게 되고, 그러면 계측 공백이 실측으로 위조된다.
 */

/** 서버가 준 그대로 — `apiClient` 결과를 캐스팅만 한 것이라 **값 검증이 없다.**
 *  검증은 규칙 층으로 넘기는 어댑터의 일이고, 그 어댑터는 아직 없다(화면 조각이 들여온다). */
export interface ConsoleFactsDto {
  runs: RunDto[];
  tasks: TaskDto[];
  datasets: DatasetDto[];
  outputs: OutputDto[];
  boundary: BoundaryDto;
  meta: MetaDto;
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
}

export interface BoundaryDto {
  publishedWithoutDelivery: number;
  /** 무효화 통지가 **안 간** 발번만 센다 — 정상 무효화는 여기 안 들어온다 */
  deliveryNowNonpublished: number;
  deliveryRows: number;
}

/** `today` 는 **응답이 실제로 본 날**이다 — 조회 기준일이지 "지금"도 거래일도 아니다.
 *  `?date=` 를 주면 **그 날짜가 그대로** 실리고(dev 실측), 생략했을 때만 원장이 아는 가장 최근 날이다.
 *  뒤엣것만 적어 두면 과거 조회 응답에 "원장 최신일"이라는 거짓 라벨이 붙는다. */
export interface MetaDto {
  db: string;
  today: string;
}
