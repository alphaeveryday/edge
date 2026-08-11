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
import type { Analysis, AnalysisEvidence, EvidenceType } from '../domains/analyses';
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

/**
 * 격자 픽스처의 작업 목록.
 *
 * `calendar` 는 **비거래일에 계획이 스킵되는가**다 — 정본은 `ops/catalog.py` 의
 * `kr_trading_calendar` 이고 테스트가 그 값과 맞물린다. 레인 단위가 아니라 **작업마다**
 * 다르다: 주말에 레인 전체를 스킵으로 칠하면 실 API 가 못 내는 슬롯이 되고, 그 모양으로
 * 검수하면 "주말엔 아무것도 안 돈다"는 없는 사실을 화면이 배운다.
 */
type FixtureTask = {
  stage: string;
  taskKey: string;
  calendar?: true;
  /** 선행 작업 — 정본은 `ops/catalog.py` 의 `depends_on` 이고 테스트가 전건 대조한다 */
  dependsOn?: string[];
};

const MARKET_TASKS: FixtureTask[] = [
  /* ⚠️ **etf-daily 전량이어야 한다.** planner 는 `catalog.entries(pipeline_type)` 를 통째로
   * 계획하므로(`ops/planner.py`), 일부만 담은 런은 실 `/sources/grid`·`/sources/overview` 가
   * 낼 수 없다 — 개요 due 가 그 수에 매이고, 빠진 데이터셋의 행·드릴다운이 검수에서 통째로
   * 사라진다. 테스트가 ops 카탈로그와 전건 대조한다. */
  { stage: 'raw', taskKey: 'ETF_HOLDINGS_COLLECTION_KRX' },
  { stage: 'raw', taskKey: 'PRICE_COLLECTION_KIS', calendar: true },
  { stage: 'raw', taskKey: 'INVESTOR_COLLECTION_KIS' },
  { stage: 'raw', taskKey: 'NAV_COLLECTION_KIS' },
  { stage: 'raw', taskKey: 'ETF_PROFILE_COLLECTION_KIS' },
  { stage: 'normalize', taskKey: 'NORMALIZE_ETF' },
  { stage: 'normalize', taskKey: 'NORMALIZE_PRICE', calendar: true, dependsOn: ['PRICE_COLLECTION_KIS'] },
  { stage: 'normalize', taskKey: 'NORMALIZE_INVESTOR' },
  { stage: 'normalize', taskKey: 'NORMALIZE_ETF_NAV' },
  { stage: 'normalize', taskKey: 'NORMALIZE_ETF_PROFILE' },
  { stage: 'feature', taskKey: 'LOAD_PRICE_DAILY', calendar: true, dependsOn: ['ENRICH_CORP_CODE'] },
  { stage: 'feature', taskKey: 'LOAD_ETF_HOLDINGS', dependsOn: ['ENRICH_CORP_CODE'] },
  { stage: 'feature', taskKey: 'LOAD_ETF_NAV', dependsOn: ['ENRICH_CORP_CODE'] },
  { stage: 'feature', taskKey: 'LOAD_ETF_FLOW', dependsOn: ['ENRICH_CORP_CODE'] },
  { stage: 'feature', taskKey: 'LOAD_INSTRUMENTS', dependsOn: ['NORMALIZE_PRICE', 'NORMALIZE_ETF', 'NORMALIZE_ETF_PROFILE', 'NORMALIZE_ETF_NAV', 'NORMALIZE_INVESTOR'] },
  { stage: 'feature', taskKey: 'LOAD_PRICE_TRIGGERS', dependsOn: ['ENRICH_CORP_CODE'] },
  { stage: 'feature', taskKey: 'ENRICH_CORP_CODE', dependsOn: ['LOAD_INSTRUMENTS'] },
];
/* ⚠️ `MOCK_OVERVIEW` 의 뉴스 레인이 `due: 6` 과 TAG_NEWS·ASSEMBLE_EVENTS 결함을 선언한다.
 * 격자·리포트는 **이 목록에서** 파생하므로 여기가 짧으면 개요가 말한 결함 행을 드릴다운에서
 * 영영 못 그린다 — 픽스처가 스스로와 모순되고 그 UI 경로가 검수에서 빠진다.
 * 여섯은 ops 카탈로그의 뉴스 작업 전량과 같다. */
const NEWS_TASKS: FixtureTask[] = [
  { stage: 'raw', taskKey: 'NEWS_COLLECTION_BIGKINDS' },
  { stage: 'normalize', taskKey: 'NORMALIZE_NEWS' },
  { stage: 'feature', taskKey: 'TAG_NEWS', dependsOn: ['NORMALIZE_NEWS'] },
  { stage: 'feature', taskKey: 'LOAD_DOCUMENTS', dependsOn: ['NORMALIZE_NEWS'] },
  { stage: 'feature', taskKey: 'LOAD_ASSERTIONS', dependsOn: ['TAG_NEWS', 'LOAD_DOCUMENTS'] },
  { stage: 'feature', taskKey: 'ASSEMBLE_EVENTS', dependsOn: ['LOAD_ASSERTIONS'] },
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

/* ⚠️ 위치가 아니라 키로 짚는다 — 목록에 한 줄 더하면 `marketTask('LOAD_PRICE_DAILY')` 가 조용히 다른 작업을
 * 가리킨다. 이 PR 에서 뉴스 목록·재실행 슬롯에 이어 세 번째다. */
const marketTask = (taskKey: string) => {
  const found = MARKET_TASKS.find((t) => t.taskKey === taskKey);
  if (!found) throw new Error(`MARKET_TASKS 에 없는 작업: ${taskKey}`);
  return found;
};

/**
 * 선행이 안 끝난 작업은 **진입하지 못한다** — wrapper 가 `depends_on` 미충족을 BLOCKED 로
 * 적는다. 그래서 한 수집이 실패하면 하류가 통째로 막힌다:
 * `NORMALIZE_PRICE` → `LOAD_INSTRUMENTS` → `ENRICH_CORP_CODE` → 적재 다섯.
 *
 * ⚠️ 손으로 적으면 목록이 늘 때마다 연쇄를 다시 세야 하고, 한 칸만 빠뜨리면 **닫힌 게이트
 * 뒤에서 성공한 적재**가 화면에 선다(실 API 가 못 내는 조합). 그래프에서 파생시킨다 —
 * ⚠️ **명시적으로 덮은 칸도 게이트가 닫혔으면 연쇄가 이긴다.** 그날의 의도라고 봐주면
 * "선행이 막혔는데 도는 중"·"막혔는데 성공" 같은 조합이 그대로 남는다 — 그게 실 API 가
 * 못 내는 바로 그 모양이다. 계획 스킵만 예외다(애초에 진입 대상이 아니다).
 */
const cascadeBlocked = (tasks: FixtureTask[], cells: GridCell[]): GridCell[] => {
  const byKey = new Map(cells.map((c) => [c.taskKey, c]));
  for (let changed = true; changed; ) {
    changed = false;
    for (const t of tasks) {
      if (!t.dependsOn?.length) continue;
      const current = byKey.get(t.taskKey)!;
      if (current.outcome === 'BLOCKED' || current.planStatus === 'SKIPPED') continue;
      const gateOpen = t.dependsOn.every((d) => byKey.get(d)?.outcome === 'FULFILLED');
      if (!gateOpen) {
        byKey.set(t.taskKey, cell(t, BLOCKED_BY_UPSTREAM));
        changed = true;
      }
    }
  }
  return tasks.map((t) => byKey.get(t.taskKey)!);
};

/**
 * 시장 슬롯 — **전 작업을 깔고 그날 다른 것만 키로 덮는다.**
 * 분기마다 17줄을 나열하면 목록이 늘 때마다 네 곳을 같이 고쳐야 하고, 한 곳만 빠뜨리면
 * 그 슬롯이 조용히 짧아진다(그게 due 8 인 런이 태어난 경위다).
 */
const marketTasks = (over: Record<string, Partial<GridCell>> = {}): GridCell[] =>
  cascadeBlocked(
    MARKET_TASKS,
    MARKET_TASKS.map((t) =>
      t.taskKey === 'ETF_HOLDINGS_COLLECTION_KRX' && !over[t.taskKey]
        ? verified(t)
        : cell(t, over[t.taskKey] ?? { recordsOut: 906 }),
    ),
  );

const BLOCKED_BY_UPSTREAM: Partial<GridCell> = {
  outcome: 'BLOCKED',
  dataStatus: null,
  recordsOut: null,
  failedRecords: null,
  outcomeReason: 'UPSTREAM_FAILED',
};
const COLLECT_TIMEOUT: Partial<GridCell> = {
  outcome: 'FAILED',
  dataStatus: null,
  recordsOut: null,
  failedRecords: null,
  outcomeReason: 'UPSTREAM_TIMEOUT',
};

function marketSlot(date: string): GridSlot {
  const runKey = `etf-daily:${date}T15:40`;
  const lane = (over: Record<string, Partial<GridCell>>, orchestrationStatus: string): GridSlot => ({
    runKey,
    launchStatus: 'LAUNCHED',
    orchestrationStatus: orchestrationStatus as GridSlot['orchestrationStatus'],
    tradingDate: date,
    tasks: marketTasks(over),
  });
  switch (date) {
    case '2026-08-01':
    case '2026-08-02':
      /* 주말 — **달력 게이트 작업만** 계획 스킵이다. 나머지는 그대로 돈다.
       * 레인 전체를 스킵으로 칠하면 실 planner 가 못 내는 슬롯이다. */
      return {
        runKey,
        launchStatus: 'LAUNCHED',
        orchestrationStatus: 'SUCCEEDED',
        tradingDate: date,
        tasks: MARKET_TASKS.map((t) => (t.calendar ? skipped(t) : cell(t, { recordsOut: 906 }))),
      };
    case '2026-07-31':
      /* 수집 실패 → 하류가 선행 미충족으로 막힌다 */
      return lane(
        {
          PRICE_COLLECTION_KIS: COLLECT_TIMEOUT,
          NORMALIZE_PRICE: BLOCKED_BY_UPSTREAM,
          LOAD_PRICE_DAILY: BLOCKED_BY_UPSTREAM,
          LOAD_PRICE_TRIGGERS: BLOCKED_BY_UPSTREAM,
          ENRICH_CORP_CODE: { recordsOut: 2, failedRecords: null },
        },
        'FAILED',
      );
    case '2026-07-29':
      /* 실행은 성공인데 데이터가 불완전 — "실행 성공 ≠ 데이터 유효" */
      return lane(
        {
          INVESTOR_COLLECTION_KIS: { dataStatus: 'INCOMPLETE', failedRecords: 2, recordsOut: 1450 },
          ENRICH_CORP_CODE: { recordsOut: 2, failedRecords: null },
        },
        'SUCCEEDED',
      );
    case MOCK_TRADING_DATE:
      /* 오늘 — 아직 도는 중(파란 테두리)이고 수급은 결손 */
      return lane(
        {
          PRICE_COLLECTION_KIS: COLLECT_TIMEOUT,
          INVESTOR_COLLECTION_KIS: { dataStatus: 'INCOMPLETE', failedRecords: 2, recordsOut: 1450 },
          /* 아직 도는 중인 칸은 **선행이 없는 raw** 에 둔다 — 막힌 게이트 뒤에서 도는 칸은
           * 실 원장에 설 수 없다(연쇄가 BLOCKED 로 덮어 버린다). */
          ETF_PROFILE_COLLECTION_KIS: { outcome: 'PENDING', dataStatus: null, recordsOut: null, failedRecords: null, running: true },
        },
        'RUNNING',
      );
    default:
      return lane({ ENRICH_CORP_CODE: { recordsOut: 2, failedRecords: null } }, 'SUCCEEDED');
  }
}

/* ⚠️ 작업을 **위치로 짚지 않는다.** `N[2]` 로 적으면 목록에 한 줄 더하는 순간 그 참조가
 * 조용히 다른 작업을 가리킨다 — 실제로 뉴스 작업을 셋에서 여섯으로 늘렸을 때 "FAILED 인
 * LOAD_DOCUMENTS" 가 TAG_NEWS 로 옮겨 갔다. 키로 짚으면 없는 키에서 즉시 죽는다. */
const newsTask = (taskKey: string) => {
  const found = NEWS_TASKS.find((t) => t.taskKey === taskKey);
  if (!found) throw new Error(`NEWS_TASKS 에 없는 작업: ${taskKey}`);
  return found;
};

function newsSlot(date: string): GridSlot {
  const runKey = `news:${date}T15:30`;
  const N = NEWS_TASKS;
  if (date === '2026-08-01' || date === '2026-08-02') {
    /* 뉴스 작업은 여섯 다 `kr_trading_calendar=False` 라 **주말에도 돈다** — 스킵으로 칠하면
     * 실 API 가 못 내는 슬롯이고, 주말 뉴스라는 실제 동작이 검수에서 빠진다. */
    return {
      runKey,
      launchStatus: 'LAUNCHED',
      orchestrationStatus: 'SUCCEEDED',
      tradingDate: date,
      tasks: N.map((t) => cell(t, { recordsOut: t.stage === 'raw' ? 2841 : 2603 })),
    };
  }
  if (date === MOCK_TRADING_DATE) {
    /* 런이 타임아웃 — 그 안의 작업이 미실행으로 남는다.
     * 귀결 분포는 `MOCK_OVERVIEW` 의 뉴스 레인 counts 와 같아야 한다
     * (due 6 · fulfilled 2 · failed 1 · missed 2 · pending 1) — 개요와 드릴다운이
     * 같은 런을 다르게 말하면 검수가 어느 쪽도 못 믿는다. */
    const timedOut = { dataStatus: null, recordsOut: null, failedRecords: null, outcomeReason: 'RUN_TIMED_OUT' } as const;
    return {
      runKey,
      launchStatus: 'LAUNCHED',
      orchestrationStatus: 'TIMED_OUT',
      tradingDate: date,
      /* 하류(LOAD_ASSERTIONS·ASSEMBLE_EVENTS)는 연쇄가 BLOCKED 로 덮는다 —
       * 선행이 MISSED·FAILED 인데 진입해서 MISSED·PENDING 이 될 수는 없다. */
      tasks: cascadeBlocked(NEWS_TASKS, [
        cell(newsTask('NEWS_COLLECTION_BIGKINDS'), { recordsOut: 3961 }),
        cell(newsTask('NORMALIZE_NEWS'), { recordsOut: 3961 }),
        cell(newsTask('TAG_NEWS'), { outcome: 'MISSED', ...timedOut }),
        cell(newsTask('LOAD_DOCUMENTS'), { outcome: 'FAILED', ...timedOut }),
        cell(newsTask('LOAD_ASSERTIONS'), { outcome: 'PENDING', ...timedOut, outcomeReason: null }),
        cell(newsTask('ASSEMBLE_EVENTS'), { outcome: 'MISSED', ...timedOut }),
      ]),
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
    tasks: N.map((t) => cell(t, { recordsOut: t.stage === 'raw' ? 6122 : 5327 })),
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
  /* 그날 정규 슬롯에서 FAILED 였던 가격 수집만 다시 돌려 성공했다.
   * 위치가 아니라 키로 짚는다 — 목록이 바뀌면 조용히 다른 작업을 가리킨다. */
  tasks: [cell(marketTask('PRICE_COLLECTION_KIS'), { recordsOut: 1452 })],
});

export const MOCK_GRID: SourceGrid = {
  days: 7,
  slots: [
    ...GRID_DATES.flatMap((d) => [newsSlot(d), marketSlot(d)]),
    /* ⚠️ **오늘이 아닌 날에 둔다.** 실 `/sources/overview` 는 pipeline_type 별로
     * `run_key DESC` 최신 하나를 고른다(`OVERVIEW_SQL` DISTINCT ON). 오늘 16:20 재실행을
     * 두면 개요가 고를 런은 15:40 이 아니라 그것인데, 개요 픽스처는 15:40 을 가리키고
     * 15:40 의 결함을 나열한다 — 실 API 가 낼 수 없는 조합이 된다. 07-31 은 정규 슬롯에서
     * 가격 수집이 FAILED 라 재실행이 자연스럽고, 드릴다운 두 줄 케이스도 그대로 남는다. */
    rerunSlot('2026-07-31'),
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
      /* ⚠️ **집계와 같은 수만큼 있어야 한다.** 서버 `GAPS_SQL` 은 LIMIT 없이 결함 창
       * (MISSING·INCOMPLETE·INVALID)과 무증거 창을 **전부** 낸다 — 집계는 6인데 목록이 3이면
       * 운영자가 못 들어가는 숫자가 생기고, 같은 상태가 여럿일 때의 구간 접기·범위 렌더가
       * 검수에서 빠진다. 연속·비연속을 섞어 둔다(gapRuns 가 접는 대상이 실제로 생기게). */
      gaps: [
        { windowStart: iso('10:14'), windowEnd: iso('10:15'), dataStatus: 'DUE', noEvidence: true },
        { windowStart: iso('10:15'), windowEnd: iso('10:16'), dataStatus: 'DUE', noEvidence: true },
        { windowStart: iso('11:02'), windowEnd: iso('11:03'), dataStatus: 'CLAIMED', noEvidence: true },
        { windowStart: iso('13:41'), windowEnd: iso('13:42'), dataStatus: 'CLAIMED', noEvidence: true },
        { windowStart: iso('09:37'), windowEnd: iso('09:38'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('09:38'), windowEnd: iso('09:39'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('11:45'), windowEnd: iso('11:46'), dataStatus: 'INCOMPLETE', noEvidence: false },
        { windowStart: iso('14:08'), windowEnd: iso('14:09'), dataStatus: 'INVALID', noEvidence: false },
        { windowStart: iso('12:20'), windowEnd: iso('12:21'), dataStatus: 'MISSING', noEvidence: false },
        { windowStart: iso('12:21'), windowEnd: iso('12:22'), dataStatus: 'MISSING', noEvidence: false },
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

/**
 * 레인의 결함 목록 — **격자 슬롯에서 파생한다.** 서버 `SourceService.toLane()` 은 필수 DUE
 * 작업 전부를 `isDefect()` 로 걸러 만들고, counts 도 같은 원장에서 센다. 손으로 적으면
 * counts 는 연쇄를 반영해 늘어나는데 목록만 옛것으로 남아, **개요가 "결함 8"이라 말하면서
 * 누를 행은 셋뿐인** 상태가 된다(실제로 그렇게 났다).
 *
 * `isDefect`: 귀결 실패(FAILED·MISSED·BLOCKED) · 데이터 결손(INCOMPLETE·INVALID) ·
 * 유실 건수 · 신선도 STALE · 마감 경과 미귀결. **UNKNOWN 은 결함이 아니다** — 완전성
 * 미배선이 설계상 대다수라 넣으면 화면 전체가 상시 결함이 된다.
 */
const defectsOf = (runKey: string) => {
  const slot = MOCK_GRID.slots.find((x) => x.runKey === runKey)!;
  return slot.tasks
    .filter((t) => t.planStatus !== 'SKIPPED')
    .filter(
      (t) =>
        t.outcome === 'FAILED' ||
        t.outcome === 'MISSED' ||
        t.outcome === 'BLOCKED' ||
        t.dataStatus === 'INCOMPLETE' ||
        t.dataStatus === 'INVALID' ||
        (t.failedRecords ?? 0) > 0 ||
        STALE_TASKS.has(t.taskKey),
    )
    .map((t) => ({
      stage: t.stage,
      taskKey: t.taskKey,
      outcome: t.outcome,
      dataStatus: t.dataStatus,
      /* 신선도는 격자 셀에 없는 축이라 여기서만 붙인다(계약 문서 §신선도) */
      freshnessStatus: STALE_TASKS.has(t.taskKey) ? ('STALE' as const) : null,
      failedRecords: t.failedRecords,
      /* `overdue` 는 미귀결에만 붙는다(`SourceService.overdue`) */
      overdue: t.outcome === null || t.outcome === 'PENDING',
    }));
};

/** 신선도가 낡은 작업 — 격자 셀에는 없는 축이라 픽스처가 따로 든다 */
const STALE_TASKS = new Set(['ETF_HOLDINGS_COLLECTION_KRX']);

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
      /* ⚠️ 파생값이다 — `SourceService.opsStatus` 규칙을 그대로 따라야 한다(테스트가 고정).
       * orchestration=RUNNING 이면 **결함을 보기 전에** IN_PROGRESS 다. 결함이 있다고
       * DEGRADED 를 적으면 실 `/sources/overview` 가 못 내는 배지를 검수가 승인한다. */
      opsStatus: 'IN_PROGRESS',
      /* 격자의 같은 런과 **같은 수**여야 한다 — 개요만 크게 적으면 운영자가 드릴다운에서
       * 재현할 수 없는 숫자가 되고, 검수는 어느 쪽도 못 믿는다(테스트가 고정한다). */
      counts: { due: 17, requiredDue: 17, fulfilled: 7, failed: 1, missed: 0, blocked: 8, pending: 1, skipped: 0 },
      defects: defectsOf(MARKET_RUN),
    },
    {
      pipelineType: 'news',
      runKey: NEWS_RUN,
      tradingDate: MOCK_TRADING_DATE,
      plannedAt: iso('15:30'),
      notToday: false,
      launchStatus: 'LAUNCHED',
      orchestrationStatus: 'TIMED_OUT',
      /* BLOCKED 은 **기동 실패·충돌 전용**이다(LAUNCH_FAILED·LAUNCH_CONFLICT). 기동은 됐고
       * 실행이 terminal 실패면 DEGRADED 다 — TIMED_OUT 은 ORCHESTRATION_TERMINAL_FAILED. */
      opsStatus: 'DEGRADED',
      counts: { due: 6, requiredDue: 6, fulfilled: 2, failed: 1, missed: 1, blocked: 2, pending: 0, skipped: 0 },
      defects: defectsOf(NEWS_RUN),
    },
  ],
};

/* ─────────── /sources — 수집 상태 ─────────── */


/**
 * 대표 런의 상세 — **손으로 쓴 것은 풍부한 상세뿐이고, 작업 목록은 격자 슬롯에서 파생한다.**
 *
 * 실 `/sources/report` 는 그 런의 `ops_expected_task` 를 전부 낸다(`TASKS_SQL`). 여기만
 * 여덟 줄로 두면 격자에서 새로 보이는 칸을 눌렀을 때 리포트에 그 행이 없다 — 방금 채운
 * NAV·프로필·적재 행의 드릴다운이 통째로 검수에서 빠진다.
 */
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

/**
 * 작업 → **원장 dataset**. `TaskStatus.dataset` 이 그 축이라 UI 카탈로그의 접기
 * (`datasetCatalog` 는 산출 테이블을 수집 데이터셋 한 행으로 접는다)와 **다른 값**이다.
 * 정본은 `ops/catalog.py` 이고 테스트가 전건 대조한다 — 접힌 값을 여기 적으면 실
 * `/sources/report` 가 못 내는 라벨을 검수가 승인한다.
 */
const MOCK_DATASET: Record<string, string> = {
  ETF_HOLDINGS_COLLECTION_KRX: 'etf_holdings',
  PRICE_COLLECTION_KIS: 'price_daily',
  INVESTOR_COLLECTION_KIS: 'investor_flow_daily',
  ENRICH_CORP_CODE: 'company_profile',
  NAV_COLLECTION_KIS: 'etf_nav',
  ETF_PROFILE_COLLECTION_KIS: 'etf_profile',
  NORMALIZE_ETF_NAV: 'etf_nav',
  NORMALIZE_ETF_PROFILE: 'etf_profile',
  NORMALIZE_INVESTOR: 'investor_flow_daily',
  LOAD_INSTRUMENTS: 'instrument_master',
  LOAD_ETF_NAV: 'etf_nav_daily',
  LOAD_ETF_HOLDINGS: 'etf_holding_snapshot',
  LOAD_PRICE_TRIGGERS: 'price_movement_trigger',
  NORMALIZE_ETF: 'etf_holdings',
  NORMALIZE_PRICE: 'price_daily',
  LOAD_PRICE_DAILY: 'price_daily',
  LOAD_ETF_FLOW: 'investor_flow_load',
  NEWS_COLLECTION_BIGKINDS: 'stock_news',
  NORMALIZE_NEWS: 'news_articles',
  LOAD_DOCUMENTS: 'document',
  TAG_NEWS: 'news_assertions',
  LOAD_ASSERTIONS: 'document_assertion',
  ASSEMBLE_EVENTS: 'source_event',
};

/**
 * 지금 상태를 말해주는 시도 — 서버 `TaskStatus.currentAttempt()` 규칙이다.
 * RUNNING 이 있으면 그중 마지막, 없으면 순서상 마지막(픽스처에는 원장 지목이 없다).
 * 헤더(`executionStatus`·`lastFinishedAt`)는 **이 시도에서 파생**된다 — 정의가 두 곳에 있으면
 * 헤더와 시도 목록이 서로 다른 말을 한다(`SourceReportResponse.TaskResponse.from`).
 */
const currentAttempt = (attempts: TaskStatus['attempts']) =>
  attempts.filter((a) => a.executionStatus === 'RUNNING').at(-1) ?? attempts.at(-1) ?? null;

/** 격자 셀 → 리포트 행. 대표 런과 파생 런이 **같은 변환**을 쓴다(둘이 갈리면 화면이 갈린다). */
function taskFromCell(gridCell: GridCell, at: string | null): TaskStatus {
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
}

/**
 * 손으로 쓴 것은 **셀이 못 만드는 상세뿐**이다 — 재시도 이력과 완전성 대조.
 * 상태 축(귀결·데이터 판정·시각)은 격자 셀에서 온다. 여기에 상태를 또 적으면 두 벌이 되고,
 * 연쇄가 셀을 바꿀 때 이쪽만 낡아 드릴다운이 격자와 다른 말을 한다.
 */
/**
 * 셀이 못 만드는 상세만 — **재시도 이력과 완전성 대조뿐이다.**
 *
 * ⚠️ 상태 축(귀결·데이터 판정·시각)은 여기 적지 않는다. 적으면 두 벌이 되고, 연쇄가 격자
 * 셀을 바꿀 때 이쪽만 낡아 **누른 칸과 열린 상세가 다른 말을** 한다(실제로 그렇게 났다).
 * 병합부가 이 두 필드만 읽으므로 나머지를 적어 봐야 무시된다 — 아예 안 적어 드리프트의
 * 원천을 없앤다.
 */
const RICH_TASKS: Record<string, Pick<TaskStatus, 'attempts' | 'completeness'>> = {
  /* 완전성 대조까지 통과 */
  ETF_HOLDINGS_COLLECTION_KRX: {
    completeness: { expected: 33, received: 33, missing: 0 },
    attempts: [],
  },
  /* 부분 결손 — 실행은 성공인데 엔티티가 모자란다 */
  INVESTOR_COLLECTION_KIS: {
    completeness: { expected: 363, received: 361, missing: 2 },
    attempts: [],
  },
  /* 실패 — 재시도 2회 소진. 셀은 시도 이력을 만들지 못한다 */
  PRICE_COLLECTION_KIS: {
    completeness: null,
    attempts: [
      { attemptNumber: 1, ecsTaskArn: null, executionStatus: 'TIMED_OUT', startedAt: iso('15:40'), finishedAt: iso('15:51'), exitCode: null, failureReason: 'KIS 응답 지연 (60s QUERYTIMEOUT)', recordSource: 'WRAPPER' },
      { attemptNumber: 2, ecsTaskArn: null, executionStatus: 'FAILED', startedAt: iso('15:52'), finishedAt: iso('16:02'), exitCode: 1, failureReason: 'KIS 응답 지연 (60s QUERYTIMEOUT)', recordSource: 'WRAPPER' },
    ],
  },
};

export const MOCK_REPORT: SourceReport = {
  run: {

    runKey: MARKET_RUN,
    launchStatus: 'LAUNCHED',
    orchestrationStatus: 'RUNNING',
    tradingDate: MOCK_TRADING_DATE,
  },
  /* 격자 슬롯이 정본 — 상세를 쓴 작업만 그 위에 얹는다. 새 작업이 레인에 늘면 여기도
   * 저절로 따라온다(안 그러면 리포트만 짧아져 드릴다운이 빈다). */
  tasks: (() => {

    const slot = MOCK_GRID.slots.find((x) => x.runKey === MARKET_RUN)!;
    /* ⚠️ **격자가 정본이다 — 상세로 행을 갈아치우지 않는다.** 통째로 바꾸면 손으로 쓴 낡은
     * 귀결(연쇄로 BLOCKED 가 된 작업이 성공·진행 중으로 남는 식)이 드릴다운에 떠서, 운영자가
     * 누른 칸과 열린 상세가 서로 다른 말을 한다. 상태 축은 셀에서 오고, 여기서는 셀이 못
     * 만드는 것(재시도 이력·완전성 대조)만 얹는다. 테스트가 귀결 일치를 고정한다. */
    return slot.tasks.map((gridCell) => {
      const base = taskFromCell(gridCell, mockSlotAt(slot));
      const extra = RICH_TASKS[gridCell.taskKey];
      if (!extra) return base;
      /* 상태 축은 셀에서 오지만 **헤더는 시도에서 파생**된다 — 시도 목록만 갈아끼우고 헤더를
       * 그대로 두면 "15:40 에 타임아웃"이라 적힌 헤더 아래 16:02 에 FAILED 로 끝난 시도
       * 목록이 붙는다. 서버는 둘 다 `currentAttempt()` 한 곳에서 뽑는다. */
      const merged = { ...base, ...extra };
      const cur = currentAttempt(merged.attempts);
      return {
        ...merged,
        executionStatus: cur?.executionStatus ?? null,
        lastFinishedAt: cur?.finishedAt ?? null,
      };
    });
  })(),
  issues: [
    { issueType: 'INCOMPLETE', scope: 'task', taskKey: 'INVESTOR_COLLECTION_KIS', status: 'OPEN', occurrenceCount: 3, firstSeenAt: iso('15:47'), lastSeenAt: iso('16:10'), resolutionReason: null },
    { issueType: 'STALLED', scope: 'run', taskKey: null, status: 'RESOLVED', occurrenceCount: 1, firstSeenAt: iso('15:55'), lastSeenAt: iso('16:03'), resolutionReason: 'RETRY_SUCCEEDED' },
  ],
};




/** 목 격자의 런·작업을 눌렀을 때 라이브 API 가 아니라 같은 픽스처의 원장 상세를 연다. */
export function mockReportForRun(runKey: string): SourceReport | null {
  /* 대표 런은 재시도·대조 이슈까지 직접 채운 상세 픽스처를 쓴다. */
  if (runKey === MARKET_RUN) return MOCK_REPORT;

  const slot = MOCK_GRID.slots.find((candidate) => candidate.runKey === runKey);
  if (!slot) return null;

  const at = mockSlotAt(slot);
  const tasks = slot.tasks.map((gridCell) => taskFromCell(gridCell, at));

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

const analysis = (o: Partial<Analysis> & Pick<Analysis, 'id' | 'name' | 'code' | 'market' | 'direction' | 'changePct' | 'status'>): Analysis => {
  const base: Analysis = {
    basisTime: '15:30',
    basisTimeAbs: `${MOCK_TRADING_DATE} 15:30 KST`,
    doneTime: '—',
    confidence: null,
    /* 게시 상태는 실행 상태와 별개 축이다 — 결과가 아직 없는 런은 null (ALPHA-737) */
    publicationStatus: null,
    result: '',
    evidence: [],
    ...o,
  };
  /* 본문이 있으면 게시 상태는 **절대 null 이 아니다** — `explanation_result.publication_status`
   * 가 `NOT NULL DEFAULT 'DRAFT'` 라 결과 행이 있으면 서버가 값을 반드시 싣는다. 픽스처가
   * "본문은 있는데 게시 상태 없음"을 만들면 실 API 가 낼 수 없는 조합이고, 그 조합으로
   * 검수하면 소비자(`hasResult`)가 유효 설명을 전부 대기로 읽는다. 본문에서 유도해
   * 호출부마다 다시 적지 않는다 — 빠뜨리면 그 종목만 조용히 사라진다. */
  /* 유도 조건은 소비자 술어(`hasResult`)와 **같은 집합**이어야 한다 — 블록은 길이가 아니라
   * 비지 않은 text 를 요구한다. 어긋나면 팩터리는 DRAFT 를 주는데 소비자는 무효로 읽는다. */
  const hasBody =
    base.result.trim().length > 0 ||
    (base.resultBlocks?.some((b) => b.text.trim().length > 0) ?? false);
  /* `'publicationStatus' in o` 로 가르면 **명시적 `undefined`** 도 재정의로 읽혀 유도를
   * 건너뛰고 필드가 `undefined` 로 남는다(`Partial<Analysis>` 가 허용한다). 의도적 null 만
   * 보존하고 미지정·undefined 는 유도한다. */
  return o.publicationStatus !== undefined
    ? base
    : { ...base, publicationStatus: hasBody ? 'DRAFT' : null };
};

/**
 * 사용 근거 한 건 — 응답이 실제로 주는 축만(구분·제목·수집 소스·발행 시각).
 *
 * `type` 이 넓은 이유: 실 API 는 영문 코드(`NEWS`·`DISCLOSURE`)를 보내고 라벨 매핑이 그걸
 * 히트한다. 한글은 UI·API 가 따로 배포된 동안 올 수 있는 **폴백 경로**다(`types.ts` 참고).
 * 미리보기가 한쪽만 담으면 검수가 나머지 경로를 한 번도 안 본다 — 둘 다 담는다.
 */
const ev = (type: EvidenceType | '뉴스' | '공시', title: string, source: string, time: string): AnalysisEvidence => ({
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
    /* 게시된 설명 — 무효화 액션은 PUBLISHED 에서만 활성이다(ALPHA-737). 전건 null 로 두면
     * 미리보기로는 그 버튼을 한 번도 못 본다. */
    publicationStatus: 'PUBLISHED',
    /* 고객에게 실제로 나간 산문 블록(ALPHA-878) — 있으면 상세가 result 대신 이걸 그린다.
     * 블록이 없는 픽스처만 두면 폴백 경로만 검수된다. */
    /* 코드·제목·참조 형식은 엔진 실물 그대로다(`statics/interval.py`
     * `final_explanation_payload`) — 코드는 'H'·'1'·'2'·'3'·'4'|'N', 참조는
     * `bars_5m:<ticker>`·`source_event:<id>`. 여기서 형식을 지어내면 미리보기 검수가
     * 운영 응답에 없는 모양을 정상 UI 로 승인한다(상세 화면은 참조를 그대로 출력한다). */
    resultBlocks: [
      {
        code: 'H',
        title: '헤더',
        text: 'KODEX 반도체는 8월 3일 +3.2% 상승했습니다.',
        evidenceRefs: ['bars_5m:091160'],
      },
      {
        code: '1',
        title: '기여 분해',
        text: '상승의 대부분은 상위 3종목에서 나왔고, 구성종목 28종 중 24종이 함께 올랐습니다.',
        evidenceRefs: ['bars_5m:091160'],
      },
      {
        code: '2',
        title: '시간 구간',
        text: '오전 10시대에 한 번, 장 마감 직전에 한 번 크게 움직였습니다.',
        evidenceRefs: ['bars_5m:091160'],
      },
      {
        code: '3',
        title: '움직임 분해',
        text: '시장 전체보다 업종 요인이 더 크게 작용했습니다.',
        evidenceRefs: ['bars_5m:091160'],
      },
      {
        code: '4',
        title: '이벤트 병치',
        text: '같은 구간에 반도체 수출 지표 개선 보도와 공급계약 공시가 있었습니다.',
        evidenceRefs: ['source_event:8f1c0a42', 'source_event:8f1c0a77'],
      },
    ],
    evidence: [
      ev('NEWS', '반도체 수출 3개월 연속 증가', 'BIGKINDS', '12:40'),
      ev('DISCLOSURE', '단일판매·공급계약 체결', 'DART', '10:05'),
      /* 한글 한 건은 남긴다 — 코드 전환 전 API 가 보내는 폴백 경로도 검수 대상이다 */
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
    /* 게시가 내려간 이력 — 목록이 PUBLISHED 와 다른 칸으로 그리는지 본다 */
    publicationStatus: 'WITHDRAWN',
    evidence: [ev('NEWS', '외국인 순매수 전환', 'BIGKINDS', '13:55')], evidenceTotal: 1,
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
