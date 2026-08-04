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
import type { Analysis } from '../domains/analyses';
import type { TaskFact } from '../rules/types';
import type {
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
          cell(T[1], { outcome: 'PENDING', dataStatus: null, recordsOut: null, failedRecords: null, running: true }),
          cell(T[2], { dataStatus: 'INCOMPLETE', failedRecords: 2, recordsOut: 1450 }),
          cell(T[3], { recordsOut: 906 }),
          cell(T[4], { outcome: 'PENDING', dataStatus: null, recordsOut: null, failedRecords: null, running: true }),
          cell(T[5], { outcome: 'PENDING', dataStatus: null, recordsOut: null, failedRecords: null }),
        ],
      };
    default:
      return {
        runKey,
        launchStatus: 'LAUNCHED',
        orchestrationStatus: 'SUCCEEDED',
        tradingDate: date,
        tasks: [verified(T[0]), cell(T[1]), verified(T[2]), cell(T[3], { recordsOut: 906 }), cell(T[4]), cell(T[5], { recordsOut: 1452 })],
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

export const MOCK_GRID: SourceGrid = {
  days: 7,
  slots: GRID_DATES.flatMap((d) => [newsSlot(d), marketSlot(d)]),
};

/* ─────────── /minute — 장중 세션 ─────────── */

const iso = (hhmm: string) => `${MOCK_TRADING_DATE}T${hhmm}:00+09:00`;

export const MOCK_MINUTE: MinuteStatus = {
  date: MOCK_TRADING_DATE,
  sessions: [
    {
      sessionId: 'mock-session-price-krx',
      dataset: 'price_minute',
      sourceGroup: 'KRX',
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
  ],
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
  corrected: false,
  result: '',
  evidence: [],
  ...o,
});

export const MOCK_ANALYSES: Analysis[] = [
  analysis({ id: 'mock-1', name: 'KODEX 반도체', code: '091160', market: 'KRX', direction: 1, changePct: 3.42, status: 'COMPLETED', doneTime: '15:47', confidence: 'HIGH', result: '반도체 대형주 강세' }),
  analysis({ id: 'mock-2', name: 'TIGER 2차전지테마', code: '305540', market: 'KRX', direction: -1, changePct: 4.18, status: 'COMPLETED', doneTime: '15:49', confidence: 'MEDIUM', result: '전기차 수요 둔화 우려' }),
  analysis({ id: 'mock-3', name: 'KODEX 200', code: '069500', market: 'KRX', direction: 1, changePct: 1.12, status: 'PENDING' }),
  analysis({ id: 'mock-4', name: 'TIGER 미디어컨텐츠', code: '228810', market: 'KRX', direction: -1, changePct: 3.05, status: 'FAILED' }),
  analysis({ id: 'mock-5', name: 'KODEX 보험', code: '140700', market: 'KRX', direction: -1, changePct: 2.87, status: 'FAILED' }),
  analysis({ id: 'mock-6', name: 'KODEX 은행', code: '091170', market: 'KRX', direction: 1, changePct: 2.31, status: 'EXCLUDED' }),
  analysis({ id: 'mock-7', name: 'Invesco QQQ Trust', code: 'QQQ', market: 'NASDAQ', direction: 1, changePct: 2.04, status: 'COMPLETED', basisTime: '05:00', basisTimeAbs: `${MOCK_TRADING_DATE} 05:00 KST`, doneTime: '05:18', confidence: 'HIGH', result: '빅테크 실적 호조' }),
  analysis({ id: 'mock-8', name: 'iShares Semiconductor', code: 'SOXX', market: 'NASDAQ', direction: -1, changePct: 3.76, status: 'COMPLETED', basisTime: '05:00', basisTimeAbs: `${MOCK_TRADING_DATE} 05:00 KST`, doneTime: '05:21', confidence: 'LOW', result: '반도체 장비 수출 규제 우려' }),
  analysis({ id: 'mock-9', name: 'Tesla', code: 'TSLA', market: 'NASDAQ', direction: -1, changePct: 5.62, status: 'PENDING', basisTime: '05:00', basisTimeAbs: `${MOCK_TRADING_DATE} 05:00 KST` }),
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

/* ─────────── /ops/runs — 런별 작업 (마스터–상세) ───────────
 *
 * 규칙 엔진 스냅샷(facts-snapshot.json)은 6개 런 중 **두 개**의 작업만 담고 있다
 * (시장 15:40 21개 · 뉴스 15:30 6개). 나머지 런은 스냅샷이 안 담은 것이지 작업이 없었던 것이
 * 아니므로, 선택 동작을 검수할 수 있도록 런마다 **서로 다른** 작업 세트를 목으로 채운다.
 *
 * 지키는 선:
 *   · 실제 기록이 있는 런은 목으로 덮지 않는다 — 원장 행이 언제나 이긴다.
 *   · 모든 런에 같은 목록을 넣지 않는다. 런의 성격(정규·수동·백필)과 원장 상태에 맞춘다.
 *   · 기동조차 못 한 런(no_run_row)에는 작업을 지어내지 않는다 — 그 런의 사실은 "행이 없다"다.
 */

const mockTask = (
  runId: string,
  pipeline: string,
  tradingDate: string,
  o: Partial<TaskFact> & Pick<TaskFact, 'stage' | 'task_key'>,
): TaskFact => ({
  run_id: runId,
  run_key: runId,
  pipeline_type: pipeline,
  trading_date: tradingDate,
  dataset: null,
  required: true,
  plan_status: 'DUE',
  task_outcome: 'FULFILLED',
  data_status: 'UNKNOWN',
  records_out: null,
  failed_records: 0,
  completeness_expected: null,
  completeness_received: null,
  attempts: 1,
  max_retries: 0,
  ...o,
});

/** 런 id → 그 런의 작업(목). 원장에 기록이 있는 런은 여기 없다. */
export const MOCK_RUN_TASKS: Record<string, TaskFact[]> = {
  /* 뉴스 정규 런 — 전건 귀결(원장 SUCCEEDED 와 일치) */
  'news:2026-08-03T15:00': (() => {
    const R = 'news:2026-08-03T15:00';
    const t = (o: Partial<TaskFact> & Pick<TaskFact, 'stage' | 'task_key'>) =>
      mockTask(R, 'news', MOCK_TRADING_DATE, o);
    return [
      t({ stage: 'raw', task_key: 'NEWS_COLLECTION_BIGKINDS', dataset: 'stock_news', records_out: 3874, started_at: iso('15:00'), finished_at: iso('15:06'), exit_code: 0 }),
      t({ stage: 'normalize', task_key: 'NORMALIZE_NEWS', dataset: 'stock_news', records_out: 3874, started_at: iso('15:06'), finished_at: iso('15:09'), exit_code: 0 }),
      t({ stage: 'feature', task_key: 'LOAD_DOCUMENTS', dataset: 'document', records_out: 3861, started_at: iso('15:09'), finished_at: iso('15:14'), exit_code: 0 }),
      t({ stage: 'feature', task_key: 'TAG_NEWS', dataset: 'document', records_out: 58, started_at: iso('15:14'), finished_at: iso('15:19'), exit_code: 0 }),
    ];
  })(),

  /* 백필 런 — 과거 거래일을 다시 돌린다. 정규 런보다 작업이 적다(적재까지만) */
  'news:2026-08-02T21:10': (() => {
    const R = 'news:2026-08-02T21:10';
    const t = (o: Partial<TaskFact> & Pick<TaskFact, 'stage' | 'task_key'>) =>
      mockTask(R, 'news', '2026-07-28', o);
    return [
      t({ stage: 'raw', task_key: 'NEWS_COLLECTION_BIGKINDS', dataset: 'stock_news', records_out: 2411, started_at: '2026-08-02T21:10:00+09:00', finished_at: '2026-08-02T21:16:00+09:00', exit_code: 0 }),
      t({ stage: 'normalize', task_key: 'NORMALIZE_NEWS', dataset: 'stock_news', records_out: 2411, started_at: '2026-08-02T21:16:00+09:00', finished_at: '2026-08-02T21:18:00+09:00', exit_code: 0 }),
      t({ stage: 'feature', task_key: 'LOAD_DOCUMENTS', dataset: 'document', records_out: 2408, started_at: '2026-08-02T21:18:00+09:00', finished_at: '2026-08-02T21:22:00+09:00', exit_code: 0 }),
    ];
  })(),

  /* 수동 런 — 원장 상태가 비고 AWS 만 FAILED 인 런. 실패·타임아웃·미기동·선행 미충족·
   * 계획 제외·대기가 한 런에 모여 있어 상태 어휘를 한 번에 검수할 수 있다. */
  'etf-daily:2026-08-02T11:03': (() => {
    const R = 'etf-daily:2026-08-02T11:03';
    const t = (o: Partial<TaskFact> & Pick<TaskFact, 'stage' | 'task_key'>) =>
      mockTask(R, 'etf-daily', '2026-08-02', o);
    return [
      t({ stage: 'raw', task_key: 'ETF_HOLDINGS_COLLECTION_KRX', dataset: 'etf_holdings', data_status: 'VALID', records_out: 906, completeness_expected: 33, completeness_received: 33, started_at: '2026-08-02T11:03:00+09:00', finished_at: '2026-08-02T11:09:00+09:00', exit_code: 0 }),
      t({ stage: 'raw', task_key: 'PRICE_COLLECTION_KIS', dataset: 'price_daily', task_outcome: 'FAILED', data_status: null, records_out: null, failed_records: null, attempts: 2, outcome_reason: 'TIMED_OUT', started_at: '2026-08-02T11:09:00+09:00', finished_at: '2026-08-02T11:24:00+09:00', exit_code: 124 }),
      t({ stage: 'raw', task_key: 'INVESTOR_COLLECTION_KIS', dataset: 'investor_flow', task_outcome: 'MISSED', data_status: null, records_out: null, failed_records: null, attempts: 0, missed_at: '2026-08-02T12:03:00+09:00', outcome_reason: 'FAILED_TO_START' }),
      t({ stage: 'normalize', task_key: 'NORMALIZE_PRICE', dataset: 'price_daily', task_outcome: 'BLOCKED', data_status: null, records_out: null, failed_records: null, attempts: 0, outcome_reason: 'UPSTREAM_FAILED' }),
      t({ stage: 'feature', task_key: 'LOAD_PRICE_DAILY', dataset: 'price_daily', task_outcome: 'BLOCKED', data_status: null, records_out: null, failed_records: null, attempts: 0, outcome_reason: 'UPSTREAM_FAILED' }),
      t({ stage: 'feature', task_key: 'LOAD_ETF_FLOW', dataset: 'etf_flow', plan_status: 'SKIPPED', task_outcome: null, data_status: null, records_out: null, failed_records: null, attempts: 0, skip_reason: 'NON_TRADING_DAY_SOURCE' }),
      t({ stage: 'analysis', task_key: 'ANALYZE_ETF', dataset: 'explanation_run', task_outcome: 'PENDING', data_status: null, records_out: null, failed_records: null, attempts: 0 }),
      t({ stage: 'publish', task_key: 'PUBLISH_EXPLANATIONS', dataset: 'explanation_result', task_outcome: 'MISSED', data_status: null, records_out: null, failed_records: null, attempts: 0, outcome_reason: 'UPSTREAM_FAILED' }),
    ];
  })(),
};
