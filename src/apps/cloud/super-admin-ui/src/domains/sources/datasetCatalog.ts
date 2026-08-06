/* 데이터셋 카탈로그 — 실행 이력의 행 축 (ALPHA-738).
 *
 * **왜 UI 측에 있는가**: 실행 격자 API(`/sources/grid`)는 런×작업만 준다 — `GridCell` 에
 * dataset 컬럼이 없다(GRID_SQL 이 `t.dataset` 을 안 뽑는다). 드릴다운 응답
 * (`/sources/report`)에는 `TaskStatus.dataset` 이 있지만 격자에는 없다. 백엔드를 바꾸지 않는
 * 범위에서 격자를 데이터셋 축으로 읽으려면 작업→데이터셋 매핑이 화면 쪽에 있어야 한다.
 *
 * **어디까지가 사실인가**
 *   · dataset id 어휘 — facts-snapshot 의 `datasets[].id` 및 `tasks[].dataset` 실측값 그대로다.
 *   · taskKeys — facts-snapshot `tasks[].dataset` 이 실제로 이어 준 작업들이다.
 *   · group·label·cadence — **원장에 없는 UI 카탈로그다**(CATALOG_SOURCE 참고). 화면에서
 *     그 사실을 표시한다.
 *
 * **행을 나누는 기준**(요구 §3): 독립 스케줄 · 독립 실패/재시도 · 독립 완전성 판정 ·
 * 개별 백필. 그래서 한 수집이 만드는 산출 테이블(예: `etf_holding_snapshot`,
 * `instrument_master`)마다 행을 쪼개지 않고, 그 수집 데이터셋 한 행에 묶는다.
 */

/** 이 카탈로그의 출처 — 화면이 "실측이 아니다"를 말할 수 있게 값으로 들고 다닌다 */
export const CATALOG_SOURCE = 'UI 카탈로그(원장 미제공)';

/**
 * 수집 주기 — 기대 실행 수의 **근거**다. 주기가 다른 데이터셋에 같은 기대치를 적용하지 않는다.
 *
 * 다만 ops 원장에 계획 행이 있는 데이터셋은 기대 실행 수를 이 값이 아니라 **원장의 DUE 셀
 * 수**로 센다(dailyRollup 참고) — 주기를 숫자로 지어내는 대신 원장이 실제로 계획한 것을 쓴다.
 * 여기 cadence 는 사람이 읽는 라벨과, ops 격자에 아예 안 나오는 레인의 설명에 쓰인다.
 */
export type Cadence =
  | { kind: 'daily'; label: string }
  | { kind: 'intradayWindows'; label: string; ledger: string };

/**
 * 실행 유형 — 화면에서 배치와 실시간을 가르는 축. `cadence.kind` 에서 유도한다(두 벌로 두면
 * 어긋난다). **상태를 갖는 부모 행이 아니라 배지·필터다** — 실제 제어 단위는 데이터셋이고,
 * "일배치 전체가 정상"이라는 판정은 원장에 근거가 없다.
 */
export type DatasetKindLabel = '일배치' | '실시간';
export const kindOf = (d: DatasetEntry): DatasetKindLabel =>
  d.cadence.kind === 'intradayWindows' ? '실시간' : '일배치';

/** 도메인 — 유형과 **직교하는** 축이다(실시간에도 시장·뉴스가 다 있다). 역시 필터·배지 전용. */
export type DatasetDomain = '시장' | '뉴스';

export interface DatasetEntry {
  /** facts-snapshot 어휘 그대로 */
  id: string;
  label: string;
  /** 필터·배지용 메타데이터. 그룹 롤업 행을 만드는 데 쓰지 않는다 */
  domain: DatasetDomain;
  taskKeys: string[];
  cadence: Cadence;
  /** ops 격자(ops_expected_task)가 이 데이터셋을 담는가. false 면 다른 원장 소관이다 */
  inOpsGrid: boolean;
  /** 다른 원장 소관일 때 어디서 보는가 */
  elsewhere?: { href: string; label: string };
  /**
   * 실시간 데이터셋의 세션 원장 키(`minute_ingestion_session.dataset`). 드릴다운이 이 값으로
   * 세션 상세를 지목한다 — 어휘 정본은 `data_pipeline/minute/states.py` 다.
   */
  sessionDataset?: string;
}

/** @deprecated 그룹 롤업 행은 제거됐다. 남은 용도는 필터 목록 생성뿐이다. */
export interface DatasetGroup {
  group: string;
  datasets: DatasetEntry[];
}

const daily = (label: string): Cadence => ({ kind: 'daily', label });

/**
 * 데이터셋 목록. **여기 순서가 곧 실행 이력의 행 순서다** — 화면은 그룹으로 접지 않고
 * 이 목록을 그대로 행으로 편다(그룹은 상태를 갖는 제어 단위가 아니다).
 */
export const DATASET_GROUPS: DatasetGroup[] = [
  {
    group: '시장 (EOD)',
    datasets: [
      {
        id: 'etf_holdings',
        domain: '시장',
        label: 'ETF 구성종목',
        taskKeys: ['ETF_HOLDINGS_COLLECTION_KRX', 'NORMALIZE_ETF', 'LOAD_ETF_HOLDINGS'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'price_daily',
        domain: '시장',
        label: '가격 일봉',
        taskKeys: ['PRICE_COLLECTION_KIS', 'NORMALIZE_PRICE', 'LOAD_PRICE_DAILY'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'investor_flow',
        domain: '시장',
        label: '수급',
        taskKeys: ['INVESTOR_COLLECTION_KIS', 'NORMALIZE_INVESTOR', 'LOAD_ETF_FLOW'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'disclosures',
        domain: '시장',
        label: '공시',
        taskKeys: [
          'DISCLOSURE_COLLECTION_DART',
          'NORMALIZE_DISCLOSURE',
          'NORMALIZE_DISCLOSURE_SEGMENT',
          'ENRICH_CORP_CODE',
          'LOAD_DISCLOSURE',
        ],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'etf_nav',
        domain: '시장',
        label: 'ETF NAV',
        taskKeys: ['NAV_COLLECTION_KIS', 'NORMALIZE_ETF_NAV', 'LOAD_ETF_NAV'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'etf_profile',
        domain: '시장',
        label: 'ETF 프로필',
        taskKeys: ['ETF_PROFILE_COLLECTION_KIS', 'NORMALIZE_ETF_PROFILE', 'LOAD_INSTRUMENTS'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'price_movement_trigger',
        domain: '시장',
        label: '가격 변동 트리거',
        taskKeys: ['LOAD_PRICE_TRIGGERS'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
    ],
  },
  {
    group: '뉴스',
    datasets: [
      {
        id: 'stock_news',
        domain: '뉴스',
        label: '뉴스 기사',
        taskKeys: [
          'NEWS_COLLECTION_BIGKINDS',
          'NORMALIZE_NEWS',
          'LOAD_DOCUMENTS',
          'TAG_NEWS',
          'LOAD_ASSERTIONS',
          'ASSEMBLE_EVENTS',
        ],
        cadence: daily('일 여러 슬롯 · 뉴스 레인'),
        inOpsGrid: true,
      },
    ],
  },
  {
    group: '실시간',
    datasets: [
      {
        /* ops 격자에는 없다 — 1분 수집은 minute_ingestion_* 라는 다른 원장이다.
         * 여기서 분별 격자를 복제하지 않고(요구 §9) 어디서 보는지만 가리킨다. */
        id: 'price_minute',
        domain: '시장',
        label: '1분 가격',
        taskKeys: [],
        cadence: {
          kind: 'intradayWindows',
          label: '1분 창 · 세션의 기대 창 수',
          ledger: 'minute_ingestion_window',
        },
        inOpsGrid: false,
        elsewhere: { href: '/minute', label: '실시간 세션' },
        sessionDataset: 'price_minute',
      },
      {
        /* 뉴스도 같은 세션 원장을 쓰는 1분 poll 레인이다(ALPHA-717) — 가격만 적어 두면
         * 실행 이력에서 뉴스 실시간 레인이 통째로 없는 것처럼 보인다. */
        id: 'news_minute',
        domain: '뉴스',
        label: '뉴스 (실시간)',
        taskKeys: [],
        cadence: {
          kind: 'intradayWindows',
          label: '1분 poll · 세션의 예정 poll 수',
          ledger: 'minute_ingestion_window',
        },
        inOpsGrid: false,
        elsewhere: { href: '/minute', label: '실시간 세션' },
        sessionDataset: 'news_minute',
      },
    ],
  },
];

/** 유형·도메인 필터 목록 — 카탈로그에 실제로 있는 값만. 없는 축을 필터로 세우지 않는다. */
export const DATASET_KINDS: DatasetKindLabel[] = ['일배치', '실시간'];
export const DATASET_DOMAINS: DatasetDomain[] = [
  ...new Set(DATASET_GROUPS.flatMap((g) => g.datasets.map((d) => d.domain))),
];

/** 작업 → 데이터셋 (역인덱스). 카탈로그에 없는 작업은 undefined — 임의로 배정하지 않는다. */
export const DATASET_OF_TASK: Record<string, string> = Object.fromEntries(
  DATASET_GROUPS.flatMap((g) => g.datasets.flatMap((d) => d.taskKeys.map((t) => [t, d.id]))),
);

export const ALL_DATASETS: DatasetEntry[] = DATASET_GROUPS.flatMap((g) => g.datasets);
