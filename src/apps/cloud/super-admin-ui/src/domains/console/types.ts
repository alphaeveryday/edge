/* console 도메인 — `GET /api/v1/console/facts` 의 **와이어 형상**(ALPHA-738 B1·B2b).
 *
 * ⚠️ 이 타입은 규칙 엔진의 `Facts` 가 아니다. 와이어는 camelCase 이고 엔진은 snake_case 이며,
 * 무엇보다 **부재의 규약이 다르다**(계약 §부재를 싣는 규약: 계측 없음이면 서버가 필드를 아예
 * 안 보낸다). 둘을 잇는 어댑터는 **한 곳**이어야 한다(아직 없다 — 화면 조각이 들여온다):
 * 규칙은 화면 도메인을 모르고, 도메인은 규칙을 모른다.
 *
 * 서버가 **안 보내는 축**은 여기에도 없다: `queues`·`etfLedger`·`runbook`·
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
  boundary: BoundaryDto;
  /** 🔴 **옵셔널이다** — 이 축을 안 싣는 배포본(롤백·UI 선배포)이 실재할 수 있고, 그때
   *  응답을 거부하면 체인 카드 하나 때문에 **ops 전 화면**이 빈다. 없으면 R10 이 `못 돎`. */
  chain?: ChainDto;
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
  stages: ChainStageDto[];
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
}

/** `today` 는 **응답이 실제로 본 날**이다 — 조회 기준일이지 "지금"도 거래일도 아니다.
 *  `?date=` 를 주면 **그 날짜가 그대로** 실리고(dev 실측), 생략했을 때만 원장이 아는 가장 최근 날이다.
 *  뒤엣것만 적어 두면 과거 조회 응답에 "원장 최신일"이라는 거짓 라벨이 붙는다. */
export interface MetaDto {
  db: string;
  /** 키 부재=미배선, null=조회 실패라 두 형상을 보존한다. */
  aws?: string | null;
  today: string;
}
