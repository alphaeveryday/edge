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

export interface DatasetEntry {
  /** facts-snapshot 어휘 그대로 */
  id: string;
  label: string;
  taskKeys: string[];
  cadence: Cadence;
  /** ops 격자(ops_expected_task)가 이 데이터셋을 담는가. false 면 다른 원장 소관이다 */
  inOpsGrid: boolean;
  /** 다른 원장 소관일 때 어디서 보는가 */
  elsewhere?: { href: string; label: string };
}

export interface DatasetGroup {
  group: string;
  datasets: DatasetEntry[];
}

const daily = (label: string): Cadence => ({ kind: 'daily', label });

export const DATASET_GROUPS: DatasetGroup[] = [
  {
    group: '시장 (EOD)',
    datasets: [
      {
        id: 'etf_holdings',
        label: 'ETF 구성종목',
        taskKeys: ['ETF_HOLDINGS_COLLECTION_KRX', 'NORMALIZE_ETF', 'LOAD_ETF_HOLDINGS'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'price_daily',
        label: '가격 일봉',
        taskKeys: ['PRICE_COLLECTION_KIS', 'NORMALIZE_PRICE', 'LOAD_PRICE_DAILY'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'investor_flow',
        label: '수급',
        taskKeys: ['INVESTOR_COLLECTION_KIS', 'NORMALIZE_INVESTOR', 'LOAD_ETF_FLOW'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'disclosures',
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
        label: 'ETF NAV',
        taskKeys: ['NAV_COLLECTION_KIS', 'NORMALIZE_ETF_NAV', 'LOAD_ETF_NAV'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'etf_profile',
        label: 'ETF 프로필',
        taskKeys: ['ETF_PROFILE_COLLECTION_KIS', 'NORMALIZE_ETF_PROFILE', 'LOAD_INSTRUMENTS'],
        cadence: daily('일 1회 · 15:40 슬롯'),
        inOpsGrid: true,
      },
      {
        id: 'price_movement_trigger',
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
    group: '장중',
    datasets: [
      {
        /* ops 격자에는 없다 — 1분 수집은 minute_ingestion_* 라는 다른 원장이다.
         * 여기서 분별 격자를 복제하지 않고(요구 §9) 어디서 보는지만 가리킨다. */
        id: 'price_minute',
        label: '1분 가격',
        taskKeys: [],
        cadence: {
          kind: 'intradayWindows',
          label: '분 단위 · 장중 세션의 기대 창 수',
          ledger: 'minute_ingestion_window',
        },
        inOpsGrid: false,
        elsewhere: { href: '/minute', label: '장중 세션' },
      },
    ],
  },
];

/** 작업 → 데이터셋 (역인덱스). 카탈로그에 없는 작업은 undefined — 임의로 배정하지 않는다. */
export const DATASET_OF_TASK: Record<string, string> = Object.fromEntries(
  DATASET_GROUPS.flatMap((g) => g.datasets.flatMap((d) => d.taskKeys.map((t) => [t, d.id]))),
);

export const ALL_DATASETS: DatasetEntry[] = DATASET_GROUPS.flatMap((g) => g.datasets);
