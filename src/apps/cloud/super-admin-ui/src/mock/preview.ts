/* 화면 검수용 목데이터 (ALPHA-738).
 *
 * 실데이터가 0건인 화면은 UI 를 평가할 수 없다 — 표가 비면 열의 의미도, 상태 어휘도 안 보인다.
 * 이 파일은 **렌더링 전용** 픽스처다. API 응답·원장은 건드리지 않고, 화면이 실데이터 0건임을
 * 먼저 밝힌 뒤 분리된 MOCK 영역에서만 쓴다.
 *
 * 규칙 두 가지:
 *   1. 실측 0 을 목값으로 위장하지 않는다 — 목은 항상 MOCK 영역 안에서만 산다.
 *   2. 화면끼리 숫자가 모순되지 않게 한 사건·한 날짜를 공유한다. 기준 거래일은 2026-08-03,
 *      사건은 규칙 엔진 스냅샷과 같다(시장 15:40 진행 중 · 뉴스 15:30 타임아웃 ·
 *      수급 361/363 결손 · 구성종목 4종 누락 · 유니버스 33종).
 */
import type { Analysis, AnalysisEvidence } from '../domains/analyses';
import type {
  ExecutionStatus,
  GridCell,
  GridSlot,
  HoldingsImpact,
  MinuteStatus,
  NewsLineage,
  NewsLineageDocument,
  NewsLineageStage,
  SourceGrid,
  SourceOverview,
  SourceReport,
  TaskStatus,
} from '../domains/sources';

export const MOCK_TRADING_DATE = '2026-08-03';
const MARKET_RUN = `etf-daily:${MOCK_TRADING_DATE}T15:40`;
const NEWS_RUN = `news:${MOCK_TRADING_DATE}T15:30`;

/* ─────────── /grid — 실행 격자 ───────────
 * 최근 7거래일 × 두 레인. 성공·실행 중·실패·타임아웃·계획 스킵·데이터 결손·완전성 VALID 가
 * 한 화면에 다 나오도록 날짜별 시나리오를 고정한다(무작위 금지 — 검수 스크린샷이 흔들린다). */

const MARKET_TASKS: { stage: string; taskKey: string }[] = [
  { stage: 'raw', taskKey: 'ETF_HOLDINGS_COLLECTION_KRX' },
  { stage: 'raw', taskKey: 'PRICE_COLLECTION_KIS' },
  { stage: 'raw', taskKey: 'INVESTOR_COLLECTION_KIS' },
  { stage: 'normalize', taskKey: 'NORMALIZE_ETF' },
  { stage: 'normalize', taskKey: 'NORMALIZE_PRICE' },
  { stage: 'feature', taskKey: 'LOAD_PRICE_DAILY' },
  { stage: 'raw', taskKey: 'DISCLOSURE_COLLECTION_DART' },
  { stage: 'feature', taskKey: 'LOAD_ETF_FLOW' },
];
const NEWS_TASKS: { stage: string; taskKey: string }[] = [
  { stage: 'raw', taskKey: 'NEWS_COLLECTION_BIGKINDS' },
  { stage: 'normalize', taskKey: 'NORMALIZE_NEWS' },
  { stage: 'feature', taskKey: 'LOAD_DOCUMENTS' },
];

const cell = (
  base: { stage: string; taskKey: string },
  over: Partial<GridCell> = {},
): GridCell => ({
  stage: base.stage,
  taskKey: base.taskKey,
  planStatus: 'DUE',
  outcome: 'FULFILLED',
  dataStatus: 'UNKNOWN',
  recordsOut: 1452,
  failedRecords: 0,
  skipReason: null,
  outcomeReason: null,
  running: false,
  ...over,
});

/** 비거래일 — 계획 단계에서 빠진 것이지 실패가 아니다 */
const skipped = (base: { stage: string; taskKey: string }): GridCell =>
  cell(base, {
    planStatus: 'SKIPPED',
    outcome: null,
    dataStatus: null,
    recordsOut: null,
    failedRecords: null,
    skipReason: 'NON_TRADING_DAY',
  });

/** 완전성 대조까지 통과한 성공 — 격자 우하 초록 점 */
const verified = (base: { stage: string; taskKey: string }): GridCell =>
  cell(base, { dataStatus: 'VALID', recordsOut: 33 });

function marketSlot(date: string): GridSlot {
  const runKey = `etf-daily:${date}T15:40`;
  const T = MARKET_TASKS;
  switch (date) {
    case '2026-08-01':
    case '2026-08-02':
      /* 주말 — 계획 스킵 */
      return { runKey, launchStatus: 'LAUNCHED', orchestrationStatus: 'SUCCEEDED', tradingDate: date, tasks: T.map(skipped) };
    case '2026-07-31':
      /* 수집 실패 → 하류가 선행 미충족으로 막힌다 */
      return {
        runKey,
        launchStatus: 'LAUNCHED',
        orchestrationStatus: 'FAILED',
        tradingDate: date,
        tasks: [
          verified(T[0]),
          cell(T[1], { outcome: 'FAILED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'UPSTREAM_TIMEOUT' }),
          verified(T[2]),
          cell(T[3], { recordsOut: 906 }),
          cell(T[4], { outcome: 'BLOCKED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'UPSTREAM_FAILED' }),
          cell(T[5], { outcome: 'BLOCKED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'UPSTREAM_FAILED' }),
          cell(T[6], { recordsOut: 2, failedRecords: null }),
          cell(T[7], { outcome: 'BLOCKED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'UPSTREAM_FAILED' }),
        ],
      };
    case '2026-07-29':
      /* 실행은 성공인데 데이터가 불완전 — "실행 성공 ≠ 데이터 유효" */
      return {
        runKey,
        launchStatus: 'LAUNCHED',
        orchestrationStatus: 'SUCCEEDED',
        tradingDate: date,
        tasks: [
          verified(T[0]),
          cell(T[1]),
          cell(T[2], { dataStatus: 'INCOMPLETE', failedRecords: 2, recordsOut: 1450 }),
          cell(T[3], { recordsOut: 906 }),
          cell(T[4]),
          cell(T[5], { recordsOut: 1450 }),
          cell(T[6], { recordsOut: 2, failedRecords: null }),
          cell(T[7], { recordsOut: 1450 }),
        ],
      };
    case MOCK_TRADING_DATE:
      /* 오늘 — 아직 도는 중(파란 테두리)이고 수급은 결손 */
      return {
        runKey,
        launchStatus: 'LAUNCHED',
        orchestrationStatus: 'RUNNING',
        tradingDate: date,
        tasks: [
          verified(T[0]),
          cell(T[1], { outcome: 'FAILED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'UPSTREAM_TIMEOUT' }),
          cell(T[2], { dataStatus: 'INCOMPLETE', failedRecords: 2, recordsOut: 1450 }),
          cell(T[3], { recordsOut: 906 }),
          cell(T[4], { outcome: 'BLOCKED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'UPSTREAM_FAILED' }),
          cell(T[5], { outcome: 'PENDING', dataStatus: null, recordsOut: null, failedRecords: null, running: true }),
          cell(T[6], { recordsOut: 2, failedRecords: null }),
          cell(T[7], { planStatus: 'SKIPPED', outcome: null, dataStatus: null, recordsOut: null, failedRecords: null, skipReason: 'NON_TRADING_DAY_SOURCE' }),
        ],
      };
    default:
      return {
        runKey,
        launchStatus: 'LAUNCHED',
        orchestrationStatus: 'SUCCEEDED',
        tradingDate: date,
        tasks: [
          verified(T[0]),
          cell(T[1]),
          verified(T[2]),
          cell(T[3], { recordsOut: 906 }),
          cell(T[4]),
          cell(T[5], { recordsOut: 1452 }),
          cell(T[6], { recordsOut: 2, failedRecords: null }),
          cell(T[7], { recordsOut: 1452 }),
        ],
      };
  }
}

function newsSlot(date: string): GridSlot {
  const runKey = `news:${date}T15:30`;
  const N = NEWS_TASKS;
  if (date === '2026-08-01' || date === '2026-08-02') {
    return { runKey, launchStatus: 'LAUNCHED', orchestrationStatus: 'SUCCEEDED', tradingDate: date, tasks: N.map(skipped) };
  }
  if (date === MOCK_TRADING_DATE) {
    /* 런이 타임아웃 — 그 안의 작업이 미실행으로 남는다 */
    return {
      runKey,
      launchStatus: 'LAUNCHED',
      orchestrationStatus: 'TIMED_OUT',
      tradingDate: date,
      tasks: [
        cell(N[0], { recordsOut: 3961 }),
        cell(N[1], { outcome: 'MISSED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'RUN_TIMED_OUT' }),
        cell(N[2], { outcome: 'FAILED', dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'RUN_TIMED_OUT' }),
      ],
    };
  }
  if (date === '2026-07-30') {
    /* 기동 자체가 실패한 슬롯 — orchestration 은 영영 null 이다 */
    return { runKey, launchStatus: 'LAUNCH_FAILED', orchestrationStatus: null, tradingDate: date, tasks: [] };
  }
  return {
    runKey,
    launchStatus: 'LAUNCHED',
    orchestrationStatus: 'SUCCEEDED',
    tradingDate: date,
    tasks: [cell(N[0], { recordsOut: 6122 }), cell(N[1], { recordsOut: 5327 }), cell(N[2], { recordsOut: 5327 })],
  };
}

const GRID_DATES = ['2026-07-28', '2026-07-29', '2026-07-30', '2026-07-31', '2026-08-01', '2026-08-02', MOCK_TRADING_DATE];

/* 같은 날짜의 두 번째 배치 실행 — 정규 슬롯이 실패한 뒤의 재실행이다.
 * 박스는 하나로 접히고 드릴다운의 실행 목록에만 두 줄로 선다(그 확인이 이 슬롯의 목적).
 * ⚠️ 원장에 런 kind(정규·수동·백필) 컬럼이 없어 목에서도 그 라벨을 붙이지 않는다 —
 * 여기서 지어내면 화면이 없는 축을 가진 것처럼 보인다(decisions.md §3-4 계측 부채). */
const rerunSlot = (date: string): GridSlot => ({
  runKey: `etf-daily:${date}T16:20`,
  launchStatus: 'LAUNCHED',
  orchestrationStatus: 'SUCCEEDED',
  tradingDate: date,
  /* 오늘 정규 슬롯에서 FAILED 였던 가격 수집만 다시 돌려 성공했다 */
  tasks: [cell(MARKET_TASKS[1], { recordsOut: 1452 })],
});

export const MOCK_GRID: SourceGrid = {
  days: 7,
  slots: [
    ...GRID_DATES.flatMap((d) => [newsSlot(d), marketSlot(d)]),
    rerunSlot(MOCK_TRADING_DATE),
  ],
};

/* ─────────── /minute — 장중 세션 ─────────── */

const iso = (hhmm: string) => `${MOCK_TRADING_DATE}T${hhmm}:00+09:00`;

export const MOCK_MINUTE: MinuteStatus = {
  date: MOCK_TRADING_DATE,
  sessions: [
    {
      sessionId: 'mock-session-price-kis',
      dataset: 'price_minute',
      /* 어휘 정본은 `data_pipeline/minute/states.py` 의 `SOURCE_GROUPS_BY_DATASET` 다
       * (`price_minute` = {toss, kis}). 목이라도 **존재할 수 없는 벤더**를 그리면 운영자가
       * 사건 대상(`R17:price_minute/…`)으로 그 이름을 본다 — MockChip 은 값이 목이라고
       * 말할 뿐 그 값이 어휘 밖이라고는 안 말한다. */
      sourceGroup: 'kis',
      phase: 'ACTIVE',
      universeVersion: 'v2026-08-03',
      /* 09:00~15:30 = 390분. 아래 창 집계의 합과 반드시 같아야 한다 —
       * 어긋나면 화면이 "원장 불일치" 경고를 띄운다(목이 거짓 경고를 만들지 않게). */
      expectedWindowCount: 390,
      processedThrough: iso('15:28'),
      contiguousCompleteThrough: iso('15:12'),
      heartbeatAt: iso('15:29'),
      leaseExpiresAt: iso('15:34'),
      leaseExpired: false,
      windows: {
        due: 36,
        claimed: 6,
        valid: 300,
        validEmpty: 42,
        incomplete: 3,
        missing: 2,
        invalid: 1,
        overdueNoEvidence: 4,
      },
      gaps: [
        { windowStart: iso('10:14'), windowEnd: iso('10:15'), dataStatus: 'DUE', noEvidence: true },
        { windowStart: iso('10:15'), windowEnd: iso('10:16'), dataStatus: 'DUE', noEvidence: true },
        { windowStart: iso('11:02'), windowEnd: iso('11:03'), dataStatus: 'CLAIMED', noEvidence: true },
        { windowStart: iso('13:41'), windowEnd: iso('13:42'), dataStatus: 'CLAIMED', noEvidence: true },
        { windowStart: iso('09:37'), windowEnd: iso('09:38'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('14:08'), windowEnd: iso('14:09'), dataStatus: 'INVALID', noEvidence: false },
        { windowStart: iso('12:20'), windowEnd: iso('12:21'), dataStatus: 'MISSING', noEvidence: false },
      ],
      priceJobs: { waiting: 12, claimed: 3, claimedExpired: 1, succeeded: 1284, dead: 2 },
    },
    /* 뉴스 세션은 가격과 **별도 객체**다 — 기대 창 수만 베껴 오면 뉴스가 가격의 사본이 된다.
     * 값의 모양이 다른 이유: 뉴스는 신규 기사가 없는 분이 다수라 VALID_EMPTY 가 대부분이고,
     * 결함은 anchor 에 못 닿고 잘린 poll(INCOMPLETE)로 나타난다(commit_news_window 판정).
     * universeVersion 은 원장이 실제로 넣는 'none' 이다(뉴스 세션은 소스 단위 — states.py
     * UNIVERSE_DATASETS 밖). 창 집계의 합은 여기서도 기대 수와 같아야 거짓 원장 불일치가
     * 안 뜬다: 30+4+88+264+3+0+1 = 390. */
    {
      sessionId: 'mock-session-news-bigkinds',
      dataset: 'news_minute',
      sourceGroup: 'bigkinds',
      phase: 'ACTIVE',
      universeVersion: 'none',
      expectedWindowCount: 390,
      processedThrough: iso('15:27'),
      contiguousCompleteThrough: iso('14:52'),
      heartbeatAt: iso('15:29'),
      leaseExpiresAt: iso('15:34'),
      leaseExpired: false,
      windows: {
        due: 30,
        claimed: 4,
        valid: 88,
        validEmpty: 264,
        incomplete: 3,
        missing: 0,
        invalid: 1,
        overdueNoEvidence: 2,
      },
      gaps: [
        { windowStart: iso('11:18'), windowEnd: iso('11:19'), dataStatus: 'DUE', noEvidence: true },
        { windowStart: iso('11:19'), windowEnd: iso('11:20'), dataStatus: 'DUE', noEvidence: true },
        { windowStart: iso('13:05'), windowEnd: iso('13:06'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('13:06'), windowEnd: iso('13:07'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('14:53'), windowEnd: iso('14:54'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('09:12'), windowEnd: iso('09:13'), dataStatus: 'INVALID', noEvidence: false },
      ],
      /* 뉴스 세션에는 window job 이 없다 — price_window_job 은 가격 창을 참조한다.
       * 서버도 0 을 채워 보낸다(JdbcMinuteStatusRepository 의 getOrDefault). */
      priceJobs: { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 },
    },
  ],
  /* 기사 단위 추출 job — 세션이 아니라 날짜 축이다(백필 생산자분이 섞인다) */
  newsJobs: { waiting: 5, claimed: 2, claimedExpired: 0, succeeded: 318, dead: 3 },
};

/* ─────────── /overview — 레인 원장 요약 ─────────── */

export const MOCK_OVERVIEW: SourceOverview = {
  lanes: [
    {
      pipelineType: 'etf-daily',
      runKey: MARKET_RUN,
      tradingDate: MOCK_TRADING_DATE,
      plannedAt: iso('15:40'),
      notToday: false,
      launchStatus: 'LAUNCHED',
      orchestrationStatus: 'RUNNING',
      opsStatus: 'DEGRADED',
      counts: { due: 21, requiredDue: 18, fulfilled: 15, failed: 1, missed: 0, blocked: 1, pending: 1, skipped: 3 },
      defects: [
        { stage: 'raw', taskKey: 'PRICE_COLLECTION_KIS', outcome: 'FAILED', dataStatus: null, freshnessStatus: null, failedRecords: null, overdue: false },
        { stage: 'raw', taskKey: 'INVESTOR_COLLECTION_KIS', outcome: 'FULFILLED', dataStatus: 'INCOMPLETE', freshnessStatus: null, failedRecords: 2, overdue: false },
        { stage: 'raw', taskKey: 'ETF_HOLDINGS_COLLECTION_KRX', outcome: 'FULFILLED', dataStatus: 'VALID', freshnessStatus: 'STALE', failedRecords: null, overdue: false },
        { stage: 'feature', taskKey: 'LOAD_PRICE_DAILY', outcome: 'BLOCKED', dataStatus: null, freshnessStatus: null, failedRecords: null, overdue: true },
      ],
    },
    {
      pipelineType: 'news',
      runKey: NEWS_RUN,
      tradingDate: MOCK_TRADING_DATE,
      plannedAt: iso('15:30'),
      notToday: false,
      launchStatus: 'LAUNCHED',
      orchestrationStatus: 'TIMED_OUT',
      opsStatus: 'BLOCKED',
      counts: { due: 6, requiredDue: 6, fulfilled: 2, failed: 1, missed: 2, blocked: 0, pending: 1, skipped: 0 },
      defects: [
        { stage: 'feature', taskKey: 'LOAD_DOCUMENTS', outcome: 'FAILED', dataStatus: null, freshnessStatus: null, failedRecords: null, overdue: false },
        { stage: 'feature', taskKey: 'ASSEMBLE_EVENTS', outcome: 'MISSED', dataStatus: null, freshnessStatus: null, failedRecords: null, overdue: true },
        { stage: 'feature', taskKey: 'TAG_NEWS', outcome: 'MISSED', dataStatus: null, freshnessStatus: null, failedRecords: null, overdue: true },
      ],
    },
  ],
};

/* ─────────── /sources — 수집 상태 ─────────── */

const task = (o: Partial<TaskStatus> & Pick<TaskStatus, 'stage' | 'taskKey'>): TaskStatus => ({
  dataset: null,
  planStatus: 'DUE',
  outcome: 'FULFILLED',
  dataStatus: 'UNKNOWN',
  executionStatus: 'SUCCEEDED',
  recordsOut: null,
  failedRecords: 0,
  completeness: null,
  lastFinishedAt: iso('15:46'),
  expectedAt: iso('15:40'),
  deadlineAt: iso('16:40'),
  missedAt: null,
  fulfilledAt: iso('15:46'),
  skipReason: null,
  outcomeReason: null,
  attempts: [
    {
      attemptNumber: 1,
      ecsTaskArn: null,
      executionStatus: 'SUCCEEDED',
      startedAt: iso('15:40'),
      finishedAt: iso('15:46'),
      exitCode: 0,
      failureReason: null,
      recordSource: 'WRAPPER',
    },
  ],
  ...o,
});

export const MOCK_REPORT: SourceReport = {
  run: {
    runKey: MARKET_RUN,
    launchStatus: 'LAUNCHED',
    orchestrationStatus: 'RUNNING',
    tradingDate: MOCK_TRADING_DATE,
  },
  tasks: [
    /* 정상 — 완전성 대조까지 통과 */
    task({
      stage: 'raw',
      taskKey: 'ETF_HOLDINGS_COLLECTION_KRX',
      dataset: 'etf_holdings',
      dataStatus: 'VALID',
      recordsOut: 906,
      completeness: { expected: 33, received: 33, missing: 0 },
    }),
    /* 부분 결손 — 실행은 성공인데 엔티티가 모자란다 */
    task({
      stage: 'raw',
      taskKey: 'INVESTOR_COLLECTION_KIS',
      dataset: 'investor_flow',
      dataStatus: 'INCOMPLETE',
      recordsOut: 1450,
      failedRecords: 2,
      completeness: { expected: 363, received: 361, missing: 2 },
      outcomeReason: 'PARTIAL_SYMBOLS',
    }),
    /* 실패 — 재시도 2회 소진 */
    task({
      stage: 'raw',
      taskKey: 'PRICE_COLLECTION_KIS',
      dataset: 'price_daily',
      outcome: 'FAILED',
      dataStatus: null,
      executionStatus: 'FAILED',
      recordsOut: null,
      failedRecords: null,
      fulfilledAt: null,
      outcomeReason: 'UPSTREAM_TIMEOUT',
      lastFinishedAt: iso('16:02'),
      attempts: [
        { attemptNumber: 1, ecsTaskArn: null, executionStatus: 'TIMED_OUT', startedAt: iso('15:40'), finishedAt: iso('15:51'), exitCode: null, failureReason: 'KIS 응답 지연 (60s QUERYTIMEOUT)', recordSource: 'WRAPPER' },
        { attemptNumber: 2, ecsTaskArn: null, executionStatus: 'FAILED', startedAt: iso('15:52'), finishedAt: iso('16:02'), exitCode: 1, failureReason: 'KIS 응답 지연 (60s QUERYTIMEOUT)', recordSource: 'WRAPPER' },
      ],
    }),
    /* 건수 신호를 안 남긴 작업 — "—" 는 0건 처리와 다르다 */
    task({ stage: 'raw', taskKey: 'DISCLOSURE_COLLECTION_DART', dataset: 'disclosures', recordsOut: 2, failedRecords: null }),
    task({ stage: 'normalize', taskKey: 'NORMALIZE_ETF', dataset: 'etf_holdings', recordsOut: 906 }),
    /* 선행 미충족 — 시도 행 자체가 없다 */
    task({
      stage: 'normalize',
      taskKey: 'NORMALIZE_PRICE',
      dataset: 'price_daily',
      outcome: 'BLOCKED',
      dataStatus: null,
      executionStatus: null,
      recordsOut: null,
      failedRecords: null,
      fulfilledAt: null,
      outcomeReason: 'UPSTREAM_FAILED',
      lastFinishedAt: null,
      attempts: [],
    }),
    /* 아직 도는 중 */
    task({
      stage: 'feature',
      taskKey: 'LOAD_PRICE_DAILY',
      dataset: 'price_daily',
      outcome: 'PENDING',
      dataStatus: null,
      executionStatus: 'RUNNING',
      recordsOut: null,
      failedRecords: null,
      fulfilledAt: null,
      lastFinishedAt: null,
      attempts: [
        { attemptNumber: 1, ecsTaskArn: null, executionStatus: 'RUNNING', startedAt: iso('16:05'), finishedAt: null, exitCode: null, failureReason: null, recordSource: 'WRAPPER' },
      ],
    }),
    /* 계획 스킵 — 안 한 게 아니라 할 일이 아니었다 */
    task({
      stage: 'feature',
      taskKey: 'LOAD_ETF_FLOW',
      dataset: 'etf_flow',
      planStatus: 'SKIPPED',
      outcome: null,
      dataStatus: null,
      executionStatus: null,
      recordsOut: null,
      failedRecords: null,
      fulfilledAt: null,
      lastFinishedAt: null,
      skipReason: 'NON_TRADING_DAY_SOURCE',
      attempts: [],
    }),
  ],
  issues: [
    { issueType: 'INCOMPLETE', scope: 'task', taskKey: 'INVESTOR_COLLECTION_KIS', status: 'OPEN', occurrenceCount: 3, firstSeenAt: iso('15:47'), lastSeenAt: iso('16:10'), resolutionReason: null },
    { issueType: 'STALLED', scope: 'run', taskKey: null, status: 'RESOLVED', occurrenceCount: 1, firstSeenAt: iso('15:55'), lastSeenAt: iso('16:03'), resolutionReason: 'RETRY_SUCCEEDED' },
  ],
};

const MOCK_DATASET: Record<string, string> = {
  ETF_HOLDINGS_COLLECTION_KRX: 'etf_holdings',
  PRICE_COLLECTION_KIS: 'price_daily',
  INVESTOR_COLLECTION_KIS: 'investor_flow',
  DISCLOSURE_COLLECTION_DART: 'disclosures',
  NORMALIZE_ETF: 'etf_holdings',
  NORMALIZE_PRICE: 'price_daily',
  LOAD_PRICE_DAILY: 'price_daily',
  LOAD_ETF_FLOW: 'etf_flow',
  NEWS_COLLECTION_BIGKINDS: 'stock_news',
  NORMALIZE_NEWS: 'stock_news',
  LOAD_DOCUMENTS: 'document',
};

function mockSlotAt(slot: GridSlot) {
  const time = slot.runKey.match(/T(\d{2}:\d{2})/)?.[1];
  return slot.tradingDate && time ? `${slot.tradingDate}T${time}:00+09:00` : null;
}

function mockExecutionStatus(gridCell: GridCell): ExecutionStatus | null {
  if (gridCell.running) return 'RUNNING';
  if (gridCell.outcome === 'FULFILLED') return 'SUCCEEDED';
  if (gridCell.outcome === 'FAILED') {
    return /TIMEOUT/i.test(gridCell.outcomeReason ?? '') ? 'TIMED_OUT' : 'FAILED';
  }
  return null;
}

/** 목 격자의 런·작업을 눌렀을 때 라이브 API 가 아니라 같은 픽스처의 원장 상세를 연다. */
export function mockReportForRun(runKey: string): SourceReport | null {
  /* 대표 런은 재시도·대조 이슈까지 직접 채운 상세 픽스처를 쓴다. */
  if (runKey === MARKET_RUN) return MOCK_REPORT;

  const slot = MOCK_GRID.slots.find((candidate) => candidate.runKey === runKey);
  if (!slot) return null;

  const at = mockSlotAt(slot);
  const tasks = slot.tasks.map((gridCell): TaskStatus => {
    const executionStatus = mockExecutionStatus(gridCell);
    const finishedAt = executionStatus !== null && executionStatus !== 'RUNNING' ? at : null;
    const completeness =
      gridCell.taskKey === 'ETF_HOLDINGS_COLLECTION_KRX' && gridCell.dataStatus === 'VALID'
        ? { expected: 33, received: 33, missing: 0 }
        : gridCell.taskKey === 'INVESTOR_COLLECTION_KIS' && gridCell.dataStatus === 'INCOMPLETE'
          ? { expected: 363, received: 361, missing: 2 }
          : null;

    return {
      stage: gridCell.stage,
      taskKey: gridCell.taskKey,
      dataset: MOCK_DATASET[gridCell.taskKey] ?? null,
      planStatus: gridCell.planStatus,
      outcome: gridCell.outcome,
      dataStatus: gridCell.dataStatus,
      executionStatus,
      recordsOut: gridCell.recordsOut,
      failedRecords: gridCell.failedRecords,
      completeness,
      lastFinishedAt: finishedAt,
      expectedAt: at,
      deadlineAt: null,
      missedAt: gridCell.outcome === 'MISSED' ? at : null,
      fulfilledAt: gridCell.outcome === 'FULFILLED' ? at : null,
      skipReason: gridCell.skipReason,
      outcomeReason: gridCell.outcomeReason,
      attempts:
        executionStatus === null
          ? []
          : [
              {
                attemptNumber: 1,
                ecsTaskArn: null,
                executionStatus,
                startedAt: at,
                finishedAt,
                exitCode: executionStatus === 'SUCCEEDED' ? 0 : executionStatus === 'RUNNING' ? null : 1,
                failureReason:
                  executionStatus === 'FAILED' || executionStatus === 'TIMED_OUT'
                    ? gridCell.outcomeReason
                    : null,
                recordSource: 'WRAPPER',
              },
            ],
    };
  });

  const issues: SourceReport['issues'] = [];
  if (slot.launchStatus === 'LAUNCH_FAILED') {
    issues.push({ issueType: 'LAUNCH_FAILED', scope: 'run', taskKey: null, status: 'OPEN', occurrenceCount: 1, firstSeenAt: at, lastSeenAt: at, resolutionReason: null });
  } else if (slot.orchestrationStatus === 'TIMED_OUT') {
    issues.push({ issueType: 'STALLED', scope: 'run', taskKey: null, status: 'OPEN', occurrenceCount: 1, firstSeenAt: at, lastSeenAt: at, resolutionReason: null });
  }
  for (const gridCell of slot.tasks) {
    const issueType =
      gridCell.dataStatus === 'INCOMPLETE' || gridCell.dataStatus === 'INVALID'
        ? gridCell.dataStatus
        : gridCell.outcome === 'FAILED' || gridCell.outcome === 'MISSED'
          ? gridCell.outcome
          : null;
    if (!issueType) continue;
    issues.push({ issueType, scope: 'task', taskKey: gridCell.taskKey, status: 'OPEN', occurrenceCount: 1, firstSeenAt: at, lastSeenAt: at, resolutionReason: null });
  }

  return {
    run: {
      runKey: slot.runKey,
      launchStatus: slot.launchStatus,
      orchestrationStatus: slot.orchestrationStatus,
      tradingDate: slot.tradingDate,
    },
    tasks,
    issues,
  };
}

/* ─────────── /impact/holdings — 결손 영향 ─────────── */

export const MOCK_HOLDINGS: HoldingsImpact = {
  runKey: MARKET_RUN,
  expectedAsOf: MOCK_TRADING_DATE,
  expectedCount: 33,
  loadedCount: 29,
  snapshotMissing: false,
  loadPending: false,
  missing: [
    {
      ourEtfId: 'etf_24221908',
      instrumentId: 'ins_0001',
      etfName: 'KODEX 200',
      analyses: [
        { explanationResultId: 'res_0001', explanationRunId: 'run_0001', publicationStatus: 'PUBLISHED', summary: '반도체 대형주 강세가 지수 상승을 견인' },
        { explanationResultId: 'res_0002', explanationRunId: 'run_0002', publicationStatus: 'DRAFT', summary: '금융 업종 기여도 재계산 대기' },
      ],
    },
    {
      ourEtfId: 'etf_24221912',
      instrumentId: 'ins_0002',
      etfName: 'TIGER 미디어컨텐츠',
      analyses: [
        { explanationResultId: 'res_0003', explanationRunId: 'run_0003', publicationStatus: 'PUBLISHED', summary: '콘텐츠 수출 기대에 따른 동반 상승' },
      ],
    },
    {
      ourEtfId: 'etf_24221935',
      instrumentId: null,
      etfName: null,
      analyses: [],
    },
    {
      ourEtfId: 'etf_24221947',
      instrumentId: 'ins_0004',
      etfName: 'TIGER 필수소비재',
      analyses: [],
    },
  ],
  recommendedAction: 'ingest-raw-etf-holdings --trade-date 2026-08-03 재실행 후 load-etf-holdings 재적재',
};

/* ─────────── /analyses — 가격 변동 분석 목록 ─────────── */

const analysis = (o: Partial<Analysis> & Pick<Analysis, 'id' | 'name' | 'code' | 'market' | 'direction' | 'changePct' | 'status'>): Analysis => ({
  basisTime: '15:30',
  basisTimeAbs: `${MOCK_TRADING_DATE} 15:30 KST`,
  doneTime: '—',
  confidence: null,
  /* 게시 상태는 실행 상태와 별개 축이다 — 결과가 아직 없는 런은 null (ALPHA-737) */
  publicationStatus: null,
  result: '',
  evidence: [],
  ...o,
});

/** 사용 근거 한 건 — 응답이 실제로 주는 축만(구분·제목·수집 소스·발행 시각) */
const ev = (type: '뉴스' | '공시', title: string, source: string, time: string): AnalysisEvidence => ({
  type,
  title,
  source,
  time: `${MOCK_TRADING_DATE} ${time}`,
});

/**
 * 검수용 분석 목데이터. 화면 구조를 보려면 다음 사례가 다 있어야 한다:
 *   · 종목 6개(KRX·NASDAQ) · 같은 종목의 장중 다건(KODEX 반도체 4건)
 *   · 최신 시도가 실패했지만 이전 유효 설명이 남은 종목(KODEX 200)
 *   · 진행 중인 종목(TIGER 미디어컨텐츠) · 근거 0건인 결과(KODEX 은행)
 *   · 전체 근거 수가 표시 상한보다 큰 결과(KODEX 반도체 15:30 — 56건 중 3건 표시)
 *
 * ⚠️ 목록과 상세의 숫자가 어긋나면 검수가 무의미하다 — 이 배열 하나가 두 화면의 유일한
 * 출처이고, 종목 상세의 "오늘 N건"도 여기서 센다. 실패한 시도에는 설명 본문을 넣지 않는다.
 */
export const MOCK_ANALYSES: Analysis[] = [
  /* KODEX 반도체 — 장중 4건. 최신 15:30 이 유효 설명이다 */
  analysis({
    id: 'mock-091160-1530', name: 'KODEX 반도체', code: '091160', market: 'KRX',
    direction: 1, changePct: 3.2, status: 'COMPLETED',
    basisTime: '15:30', basisTimeAbs: `${MOCK_TRADING_DATE} 15:30`, doneTime: '15:41',
    confidence: 'HIGH', result: '반도체 업종 강세로 구성종목이 동반 상승했습니다.',
    evidence: [
      ev('뉴스', '반도체 수출 3개월 연속 증가', 'BIGKINDS', '12:40'),
      ev('공시', '단일판매·공급계약 체결', 'DART', '10:05'),
      ev('뉴스', 'HBM 수요 증가 전망', 'BIGKINDS', '09:31'),
    ],
    /* 표시 상한보다 총 건수가 큰 사례 — 화면이 "56건 중 3건 표시"라고 말해야 한다 */
    evidenceTotal: 56,
  }),
  analysis({
    id: 'mock-091160-1410', name: 'KODEX 반도체', code: '091160', market: 'KRX',
    direction: 1, changePct: 2.4, status: 'COMPLETED',
    basisTime: '14:10', basisTimeAbs: `${MOCK_TRADING_DATE} 14:10`, doneTime: '14:19',
    confidence: 'MEDIUM', result: '장중 반등 — 외국인 순매수 전환.',
    evidence: [ev('뉴스', '외국인 순매수 전환', 'BIGKINDS', '13:55')], evidenceTotal: 1,
  }),
  analysis({
    id: 'mock-091160-1022', name: 'KODEX 반도체', code: '091160', market: 'KRX',
    direction: 1, changePct: 1.8, status: 'COMPLETED',
    basisTime: '10:22', basisTimeAbs: `${MOCK_TRADING_DATE} 10:22`, doneTime: '10:31',
    confidence: 'MEDIUM', result: '개장 초 강세.',
    evidence: [ev('뉴스', '개장 초 반도체 강세', 'BIGKINDS', '10:02')], evidenceTotal: 1,
  }),
  analysis({
    id: 'mock-091160-0910', name: 'KODEX 반도체', code: '091160', market: 'KRX',
    direction: 1, changePct: 1.1, status: 'COMPLETED',
    basisTime: '09:10', basisTimeAbs: `${MOCK_TRADING_DATE} 09:10`, doneTime: '09:18',
    confidence: 'LOW', result: '시초가 갭 상승.',
    evidence: [], evidenceTotal: 0,
  }),

  /* TIGER 2차전지테마 — 2건 */
  analysis({
    id: 'mock-305540-1452', name: 'TIGER 2차전지테마', code: '305540', market: 'KRX',
    direction: -1, changePct: 4.18, status: 'COMPLETED',
    basisTime: '14:52', basisTimeAbs: `${MOCK_TRADING_DATE} 14:52`, doneTime: '15:02',
    confidence: 'MEDIUM', result: '전기차 수요 둔화 우려로 밸류체인이 조정받았습니다.',
    evidence: [
      ev('뉴스', '2차전지 밸류체인 조정', 'BIGKINDS', '14:31'),
      ev('뉴스', '전기차 판매 증가율 둔화', 'BIGKINDS', '11:20'),
    ],
    evidenceTotal: 12,
  }),
  analysis({
    id: 'mock-305540-1105', name: 'TIGER 2차전지테마', code: '305540', market: 'KRX',
    direction: -1, changePct: 2.6, status: 'COMPLETED',
    basisTime: '11:05', basisTimeAbs: `${MOCK_TRADING_DATE} 11:05`, doneTime: '11:14',
    confidence: 'LOW', result: '오전 약세.',
    evidence: [ev('뉴스', '2차전지 약세 지속', 'BIGKINDS', '10:44')], evidenceTotal: 1,
  }),

  /* KODEX 200 — 최신 시도(15:20)가 실패했지만 13:10 유효 설명이 남아 있다 */
  analysis({
    id: 'mock-069500-1520', name: 'KODEX 200', code: '069500', market: 'KRX',
    direction: 1, changePct: 1.12, status: 'FAILED',
    basisTime: '15:20', basisTimeAbs: `${MOCK_TRADING_DATE} 15:20`,
  }),
  analysis({
    id: 'mock-069500-1310', name: 'KODEX 200', code: '069500', market: 'KRX',
    direction: 1, changePct: 0.94, status: 'COMPLETED',
    basisTime: '13:10', basisTimeAbs: `${MOCK_TRADING_DATE} 13:10`, doneTime: '13:19',
    confidence: 'MEDIUM', result: '지수 전반 완만한 상승.',
    evidence: [ev('뉴스', '코스피 외국인 순매수', 'BIGKINDS', '12:58')], evidenceTotal: 4,
  }),

  /* 진행 중 — 결과가 아직 없다(본문·신뢰도 null) */
  analysis({
    id: 'mock-228810-1535', name: 'TIGER 미디어컨텐츠', code: '228810', market: 'KRX',
    direction: -1, changePct: 3.05, status: 'PENDING',
    basisTime: '15:35', basisTimeAbs: `${MOCK_TRADING_DATE} 15:35`,
  }),

  /* 근거가 하나도 없는 결과 */
  analysis({
    id: 'mock-091170-1450', name: 'KODEX 은행', code: '091170', market: 'KRX',
    direction: 1, changePct: 2.31, status: 'COMPLETED',
    basisTime: '14:50', basisTimeAbs: `${MOCK_TRADING_DATE} 14:50`, doneTime: '14:59',
    confidence: 'LOW', result: '금리 기대 변화에 따른 은행주 강세.',
    evidence: [], evidenceTotal: 0,
  }),

  /* NASDAQ — 2종목 */
  analysis({
    id: 'mock-QQQ-0500', name: 'Invesco QQQ Trust', code: 'QQQ', market: 'NASDAQ',
    direction: 1, changePct: 2.04, status: 'COMPLETED',
    basisTime: '05:00', basisTimeAbs: `${MOCK_TRADING_DATE} 05:00`, doneTime: '05:18',
    confidence: 'HIGH', result: '빅테크 실적 호조로 지수 ETF 가 상승했습니다.',
    evidence: [ev('뉴스', '빅테크 분기 실적 호조', 'BIGKINDS', '04:32')], evidenceTotal: 9,
  }),
  analysis({
    id: 'mock-SOXX-0500', name: 'iShares Semiconductor', code: 'SOXX', market: 'NASDAQ',
    direction: -1, changePct: 3.76, status: 'COMPLETED',
    basisTime: '05:00', basisTimeAbs: `${MOCK_TRADING_DATE} 05:00`, doneTime: '05:21',
    confidence: 'LOW', result: '반도체 장비 수출 규제 우려가 반영됐습니다.',
    evidence: [ev('공시', '수출 규제 관련 공시', 'DART', '04:10')], evidenceTotal: 2,
  }),
];

/* ─────────── /lineage/news — 근거·계보 ───────────
 * 단계별 분자·분모가 서로 맞물리게 만든다. 문서 목록은 단계 필터가 실제로 부분집합을
 * 가리키는지 확인할 수 있도록 세 부류(증거 있음·없음·분석 사용)를 섞는다. */

const doc = (
  n: number,
  o: Partial<NewsLineageDocument> & Pick<NewsLineageDocument, 'title' | 'publisher' | 'assertionCount' | 'usedInAnalysis'>,
): NewsLineageDocument => ({
  documentId: `mock-doc-${n}`,
  sourceCode: 'BIGKINDS',
  sourceUri: null,
  publishedAt: iso('14:05'),
  availableAt: iso('15:05'),
  ...o,
});

const MOCK_DOCUMENTS: NewsLineageDocument[] = [
  doc(1, { title: '반도체 수출 3개월 연속 증가…대형주 동반 강세', publisher: '한국경제', assertionCount: 4, usedInAnalysis: true, availableAt: iso('15:22') }),
  doc(2, { title: '2차전지 밸류체인, 전기차 수요 둔화에 조정', publisher: '매일경제', assertionCount: 3, usedInAnalysis: true, availableAt: iso('15:18') }),
  doc(3, { title: '금융지주 배당 확대 검토', publisher: '서울경제', assertionCount: 2, usedInAnalysis: false, availableAt: iso('15:11') }),
  doc(4, { title: '코스피 장중 등락 반복…외국인 순매수 전환', publisher: '연합뉴스', assertionCount: 1, usedInAnalysis: false, availableAt: iso('15:04') }),
  doc(5, { title: '오늘의 증시 일정', publisher: '이데일리', assertionCount: 0, usedInAnalysis: false, availableAt: iso('14:58') }),
  doc(6, { title: '[표] 주요 ETF 종가', publisher: '뉴시스', assertionCount: 0, usedInAnalysis: false, availableAt: iso('14:52') }),
  doc(7, { title: '미디어·콘텐츠주 수출 기대감 확산', publisher: '전자신문', assertionCount: 2, usedInAnalysis: true, availableAt: iso('14:47') }),
  doc(8, { title: '보험업 손해율 개선 지연', publisher: '파이낸셜뉴스', assertionCount: 1, usedInAnalysis: false, availableAt: iso('14:41') }),
  doc(9, { title: '조선 수주 잔고 사상 최대', publisher: '한국경제', assertionCount: 3, usedInAnalysis: true, availableAt: iso('14:33') }),
  doc(10, { title: '증권가 "하반기 실적 눈높이 하향"', publisher: '머니투데이', assertionCount: 0, usedInAnalysis: false, availableAt: iso('14:26') }),
];

/** 단계 필터가 실제로 그 부분집합을 내려 주는지 확인할 수 있게, 목에서도 같은 정의로 거른다 */
export function mockLineage(stage: NewsLineageStage | undefined, limit: number): NewsLineage {
  const filtered = MOCK_DOCUMENTS.filter((d) =>
    stage === 'structured'
      ? d.assertionCount > 0
      : stage === 'unstructured'
        ? d.assertionCount === 0
        : stage === 'used'
          ? d.usedInAnalysis
          : true,
  );
  return {
    date: MOCK_TRADING_DATE,
    stage: stage ?? null,
    /* 규칙 엔진 스냅샷의 뉴스 계보와 같은 수치 — 화면끼리 어긋나지 않게 */
    summary: { totalDocuments: 5327, documentsWithAssertion: 1125, documentsUsedInAnalysis: 628 },
    documents: filtered.slice(0, limit),
    extraction: {
      succeeded: 318,
      dead: 3,
      deadByErrorCode: [
        { errorCode: 'EXTRACTION_TIMEOUT', count: 2 },
        { errorCode: null, count: 1 },
      ],
    },
  };
}
