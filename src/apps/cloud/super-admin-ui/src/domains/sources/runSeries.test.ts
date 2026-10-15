/* 데이터셋별 실행 추이의 계열 규칙 (ALPHA-1284).
 *
 * 지키는 의도: **없는 값을 그리지 않는다.** 실행 기록 없이 완료로 적힌 작업이 원장에 실제로
 * 있고(가격 3작업 7회 중 2회 등), 그것이 0행·0초·정상으로 서면 이 구역이 거짓을 말한다.
 * 그리고 종료 코드 2(부분 성공)가 정상과 한 칸에 서면 "매일 부분 성공으로 끝난다"가 안 보인다.
 *
 * 실행: node --test src/domains/sources/runSeries.test.ts
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { ALL_DATASETS } from './datasetCatalog.ts';
import { buildRunSeries, dailyLastRows, exitStateOf, formatDuration, representativeTask } from './runSeries.ts';
import type { GridCell, GridSlot } from './types.ts';

const cell = (o: Partial<GridCell> & Pick<GridCell, 'taskKey' | 'stage'>): GridCell => ({
  planStatus: 'DUE',
  outcome: 'FULFILLED',
  dataStatus: 'VALID',
  recordsOut: null,
  failedRecords: null,
  skipReason: null,
  outcomeReason: null,
  running: false,
  ...o,
});

const slot = (runKey: string, tasks: GridCell[]): GridSlot => ({
  runKey,
  launchStatus: 'LAUNCHED',
  orchestrationStatus: 'SUCCEEDED',
  tradingDate: runKey.slice(runKey.indexOf(':') + 1, runKey.indexOf('T')),
  tasks,
});

const GRID = ALL_DATASETS.filter((d) => d.inOpsGrid);
const only = (id: string) => GRID.filter((d) => d.id === id);
const at = (start: string, seconds: number, exitCode: number | null = 0) => ({
  startedAt: `2026-10-12T${start}:00Z`,
  finishedAt: new Date(Date.parse(`2026-10-12T${start}:00Z`) + seconds * 1000).toISOString(),
  exitCode,
});

/** 가격 일봉 한 실행 — 세 작업이 각각 10·20·30초, 대표 작업(LOAD_PRICE_DAILY)이 148행 */
const priceRun = (runKey: string, over: Partial<Record<string, Partial<GridCell>>> = {}): GridSlot =>
  slot(runKey, [
    cell({ taskKey: 'PRICE_COLLECTION_KIS', stage: 'raw', recordsOut: 900, ...at('06:40', 10), ...over.raw }),
    cell({ taskKey: 'NORMALIZE_PRICE', stage: 'normalize', recordsOut: 800, ...at('06:41', 20), ...over.normalize }),
    cell({ taskKey: 'LOAD_PRICE_DAILY', stage: 'feature', recordsOut: 148, ...at('06:42', 30), ...over.feature }),
  ]);

test('🔴 대표 작업은 taskKeys 순서에서 stage 가 feature 인 첫 작업이다 — 카탈로그 전 데이터셋이 하나씩 얻는다', () => {
  /* stage 의 정본은 파이프라인 소스다. 화면에서는 격자 셀의 stage 로 같은 규칙을 돈다. */
  const src = readFileSync(
    new URL('../../../../data-pipeline/src/data_pipeline/ops/catalog.py', import.meta.url),
    'utf8',
  );
  const stages = new Map([...src.matchAll(/task_key="([^"]+)",(?:\s*flow="[^"]+",)?\s*stage="([^"]+)"/g)].map((m) => [m[1], m[2]]));
  const declared = [...src.matchAll(/task_key="([^"]+)"/g)].length;
  assert.equal(stages.size, declared, 'stage 를 못 읽은 작업이 있으면 아래 단언이 그 작업을 빼고 잰다');
  assert.ok(declared >= 30, `추출이 비면 아래 단언은 아무것도 안 잰다 — ${declared}`);
  const picked = Object.fromEntries(GRID.map((d) => [d.id, representativeTask(d, (k) => stages.get(k))]));
  assert.deepEqual(picked, {
    macro_observation: 'LOAD_MACRO',
    sector_classification: 'LOAD_SECTOR',
    financial_metric: 'LOAD_FINANCIAL_METRIC',
    disclosures: 'LOAD_DISCLOSURE',
    etf_holdings: 'LOAD_ETF_HOLDINGS',
    price_daily: 'LOAD_PRICE_DAILY',
    sector_index_daily: 'LOAD_MARKET_SERIES_DAILY',
    investor_flow: 'LOAD_ETF_FLOW',
    company_profile: 'ENRICH_CORP_CODE',
    investor_flow_intraday: 'LOAD_INVESTOR_INTRADAY',
    etf_nav: 'LOAD_ETF_NAV',
    etf_profile: 'LOAD_INSTRUMENTS',
    price_movement_trigger: 'LOAD_PRICE_TRIGGERS',
    /* 뉴스는 feature 작업이 넷이다 — 첫째(LOAD_DOCUMENTS)여야 문서 수가 대표가 된다 */
    stock_news: 'LOAD_DOCUMENTS',
  });
});

test('점은 실행 하나다 — 같은 날 여러 번 돈 실행을 하루로 합치지 않는다', () => {
  const news = (runKey: string, rows: number) =>
    slot(runKey, [cell({ taskKey: 'LOAD_DOCUMENTS', stage: 'feature', recordsOut: rows, ...at('00:10', 5) })]);
  const [series] = buildRunSeries(
    [news('news:2026-10-12T00:10', 300), news('news:2026-10-12T12:10', 450)],
    only('stock_news'),
  );
  assert.deepEqual(series.points.map((p) => [p.runKey, p.rows]), [
    ['news:2026-10-12T00:10', 300],
    ['news:2026-10-12T12:10', 450],
  ]);
});

test('네 계열은 대표 작업의 행 수·작업 시간 합·가장 나쁜 종료 코드·완전성 숫자에서 온다', () => {
  const [series] = buildRunSeries(
    [priceRun('etf-daily:2026-10-12T15:40', { raw: { completenessExpected: 148, completenessReceived: 141 } })],
    only('price_daily'),
  );
  assert.equal(series.representative, 'LOAD_PRICE_DAILY');
  assert.deepEqual(series.points, [{
    runKey: 'etf-daily:2026-10-12T15:40',
    rows: 148,
    durationSec: 60,
    exit: 'ok',
    expected: 148,
    received: 141,
  }]);
});

test('🔴 값이 없는 실행은 빈 점이다 — 실행 기록 없이 완료로 적힌 작업을 0행·0초·정상으로 그리지 않는다', () => {
  const noRecord = { startedAt: null, finishedAt: null, exitCode: null, recordsOut: null };
  const [series] = buildRunSeries(
    [
      priceRun('etf-daily:2026-10-08T15:40'),
      priceRun('etf-daily:2026-10-09T15:40', { raw: noRecord, normalize: noRecord, feature: noRecord }),
      /* 한 작업만 기록이 없어도 합은 내지 않는다 — 나머지만 더하면 실제보다 짧다 */
      priceRun('etf-daily:2026-10-12T15:40', { normalize: { startedAt: null, finishedAt: null, exitCode: null } }),
    ],
    only('price_daily'),
  );
  assert.equal(series.points.length, 3, '기록 없는 실행도 점으로 남아야 빈 자리가 보인다');
  assert.deepEqual(
    series.points.map((p) => [p.rows, p.durationSec, p.exit, p.expected, p.received]),
    [
      [148, 60, 'ok', null, null],
      [null, null, 'none', null, null],
      [148, null, 'none', null, null],
    ],
  );
});

test('🔴 종료 코드 2 는 정상이 아니다 — 부분 성공이 정상과 한 칸에 서지 않는다', () => {
  assert.equal(exitStateOf(0), 'ok');
  assert.equal(exitStateOf(2), 'partial');
  assert.equal(exitStateOf(1), 'failed');
  assert.equal(exitStateOf(137), 'failed');
  assert.equal(exitStateOf(null), 'none');
  assert.equal(exitStateOf(undefined), 'none');
  const exits = (over: Parameters<typeof priceRun>[1]) =>
    buildRunSeries([priceRun('etf-daily:2026-10-12T15:40', over)], only('price_daily'))[0].points[0].exit;
  assert.equal(exits({ normalize: { exitCode: 2 } }), 'partial', '셋 중 하나만 2 여도 부분 성공이다');
  assert.equal(exits({ normalize: { exitCode: 2 }, feature: { exitCode: 1 } }), 'failed', '실패가 부분 성공을 이긴다');
  assert.equal(exits({ raw: { exitCode: null }, feature: { exitCode: 2 } }), 'partial');
});

test('🔴 새 필드가 없는 옛 응답에서도 계열을 만든다 — 행 수만 서고 나머지는 기록 없음이다', () => {
  /* UI 가 API 보다 먼저 배포되면 받는 형상이다: 시각·종료 코드·완전성 키가 아예 없다 */
  const old = slot('etf-daily:2026-10-12T15:40', [
    cell({ taskKey: 'PRICE_COLLECTION_KIS', stage: 'raw', recordsOut: 900 }),
    cell({ taskKey: 'NORMALIZE_PRICE', stage: 'normalize', recordsOut: 800 }),
    cell({ taskKey: 'LOAD_PRICE_DAILY', stage: 'feature', recordsOut: 148 }),
  ]);
  for (const key of ['startedAt', 'finishedAt', 'exitCode', 'completenessExpected', 'completenessReceived']) {
    assert.ok(!(key in old.tasks[0]), `전제: 픽스처에 ${key} 가 있으면 옛 응답이 아니다`);
  }
  const [series] = buildRunSeries([old], only('price_daily'));
  assert.deepEqual(series.points, [{
    runKey: old.runKey, rows: 148, durationSec: null, exit: 'none', expected: null, received: null,
  }]);
});

test('휴장으로 건너뛴 실행은 점으로 남되 값이 없다 — 돌 일이 없던 작업을 실패로도 0 으로도 세지 않는다', () => {
  const skipped = { planStatus: 'SKIPPED' as const, outcome: null, recordsOut: null, startedAt: null, finishedAt: null, exitCode: null };
  const [series] = buildRunSeries(
    [priceRun('etf-daily:2026-10-09T15:40', { raw: skipped, normalize: skipped, feature: skipped })],
    only('price_daily'),
  );
  assert.deepEqual(series.points.map((p) => [p.rows, p.durationSec, p.exit]), [[null, null, 'none']]);
});

test('데이터셋의 작업이 하나도 없는 실행은 그 데이터셋의 점이 아니다 — 남의 레인 실행으로 빈 점을 늘리지 않는다', () => {
  const news = slot('news:2026-10-12T00:10', [cell({ taskKey: 'LOAD_DOCUMENTS', stage: 'feature', recordsOut: 300 })]);
  const result = buildRunSeries([news, priceRun('etf-daily:2026-10-12T15:40')], GRID);
  assert.equal(result.length, GRID.length, '행은 카탈로그의 실행 격자 데이터셋 전부다');
  const points = Object.fromEntries(result.map((s) => [s.dataset.id, s.points.map((p) => p.runKey)]));
  assert.deepEqual(points.price_daily, ['etf-daily:2026-10-12T15:40']);
  assert.deepEqual(points.stock_news, ['news:2026-10-12T00:10']);
  assert.deepEqual(points.etf_nav, []);
  assert.equal(result.find((s) => s.dataset.id === 'etf_nav')!.representative, null);
});

test('🔴 작업이 하나도 안 적힌 실행(기동 실패)은 그 레인 데이터셋의 빈 점이다 — 못 뜬 실행이 추이에서 사라지지 않는다', () => {
  const failed: GridSlot = { ...slot('etf-daily:2026-10-09T15:40', []), launchStatus: 'LAUNCH_FAILED', orchestrationStatus: null };
  const result = buildRunSeries([priceRun('etf-daily:2026-10-08T15:40'), failed], GRID);
  const price = result.find((s) => s.dataset.id === 'price_daily')!;
  assert.deepEqual(price.points.map((p) => [p.runKey, p.rows, p.durationSec, p.exit]), [
    ['etf-daily:2026-10-08T15:40', 148, 60, 'ok'],
    ['etf-daily:2026-10-09T15:40', null, null, 'none'],
  ]);
  /* 다른 레인의 데이터셋에는 귀속하지 않는다 */
  assert.deepEqual(result.find((s) => s.dataset.id === 'stock_news')!.points, []);
  /* 기동 실패가 확정되지 않은 빈 실행은 점이 아니다 — 돌았는지 모르는 실행이 최근 값이 되면 안 된다 */
  for (const launchStatus of ['PLANNING', 'LAUNCH_UNKNOWN', 'LAUNCHED', null] as const) {
    const pending = { ...slot('etf-daily:2026-10-12T15:40', []), launchStatus } as GridSlot;
    const [price2] = buildRunSeries([priceRun('etf-daily:2026-10-08T15:40'), pending], only('price_daily'));
    assert.deepEqual(price2.points.map((p) => p.runKey), ['etf-daily:2026-10-08T15:40'], String(launchStatus));
  }
});

test('🔴 점은 슬롯 시각 순서다 — 나중에 백필한 과거 실행이 최근 값이 되지 않는다', () => {
  /* 응답 배열은 원장에 계획이 적힌 순서라, 오늘 백필한 10-02 슬롯이 맨 끝에 온다 */
  const [series] = buildRunSeries(
    [
      priceRun('etf-daily:2026-10-08T15:40'),
      priceRun('etf-daily:2026-10-12T15:40', { feature: { recordsOut: 150 } }),
      priceRun('etf-daily:2026-10-02T15:40', { feature: { recordsOut: 39 } }),
    ],
    only('price_daily'),
  );
  assert.deepEqual(series.points.map((p) => [p.runKey, p.rows]), [
    ['etf-daily:2026-10-02T15:40', 39],
    ['etf-daily:2026-10-08T15:40', 148],
    ['etf-daily:2026-10-12T15:40', 150],
  ]);
});

test('시각이 뒤집힌 기록으로 음수 시간을 내지 않는다', () => {
  const [series] = buildRunSeries(
    [priceRun('etf-daily:2026-10-12T15:40', {
      raw: { startedAt: '2026-10-12T06:41:00Z', finishedAt: '2026-10-12T06:40:00Z' },
    })],
    only('price_daily'),
  );
  assert.equal(series.points[0].durationSec, null);
});

test('소요 시간은 초와 분으로 읽힌다', () => {
  assert.equal(formatDuration(59), '59초');
  assert.equal(formatDuration(62), '1분 2초');
  assert.equal(formatDuration(459), '7분 39초');
  assert.equal(formatDuration(8.4), '8초');
  assert.equal(formatDuration(null), '기록 없음');
});

/** 장중 수급 한 실행 — 대표 작업(LOAD_INVESTOR_INTRADAY)의 행 수와 종료 코드만 다르게 둔다 */
const intradayRun = (runKey: string, rows: number | null, exitCode: number | null = 0): GridSlot =>
  slot(runKey, [
    cell({ taskKey: 'LOAD_INVESTOR_INTRADAY', stage: 'feature', recordsOut: rows, ...at('01:00', 60, exitCode) }),
  ]);

test('🔴 하루 안에서 누적되는 행 수는 하루 마지막 실행 값 한 점이다 — 소요 시간·종료 상태는 실행마다 남는다', () => {
  /* 장중 수급의 행 수는 실행마다 그날 그 시점까지의 누적이다(ALPHA-1327 — dev 10-12 521 → 2,919).
   * 실행마다 그리면 하루 안에서 커졌다가 다음 날 아침 작아지는 톱니가 된다. 날마다 마지막 값이
   * 다르게 두고, 같은 날의 앞 실행 값(작은 값)이 하루 점으로 새지 않는지 본다. */
  const [series] = buildRunSeries(
    [
      intradayRun('investor-intraday:2026-10-13T14:35', 3021, 2),
      intradayRun('investor-intraday:2026-10-12T09:35', 521),
      intradayRun('investor-intraday:2026-10-12T14:35', 2919),
      intradayRun('investor-intraday:2026-10-13T09:35', 562),
    ],
    only('investor_flow_intraday'),
  );
  assert.deepEqual(series.dailyRows, [
    { day: '2026-10-12', runKey: 'investor-intraday:2026-10-12T14:35', rows: 2919 },
    { day: '2026-10-13', runKey: 'investor-intraday:2026-10-13T14:35', rows: 3021 },
  ]);
  // 실행 단위 계열은 그대로다 — 점 네 개, 슬롯 순서, 종료 상태도 실행마다.
  assert.deepEqual(series.points.map((p) => [p.runKey.slice(-16), p.rows, p.durationSec, p.exit]), [
    ['2026-10-12T09:35', 521, 60, 'ok'],
    ['2026-10-12T14:35', 2919, 60, 'ok'],
    ['2026-10-13T09:35', 562, 60, 'ok'],
    ['2026-10-13T14:35', 3021, 60, 'partial'],
  ]);
});

test('🔴 그날 마지막 실행에 행 수가 없으면 빈 점이다 — 앞 실행의 값으로 채우지 않는다', () => {
  /* 채우면 마지막 실행이 못 쓴 날이 그 앞 슬롯까지의 누적으로 서서 하루치가 다 쌓인 날처럼 보인다 */
  const [series] = buildRunSeries(
    [
      intradayRun('investor-intraday:2026-10-12T09:35', 521),
      intradayRun('investor-intraday:2026-10-12T13:25', 2308),
      intradayRun('investor-intraday:2026-10-12T14:35', null, null),
    ],
    only('investor_flow_intraday'),
  );
  assert.deepEqual(series.dailyRows, [
    { day: '2026-10-12', runKey: 'investor-intraday:2026-10-12T14:35', rows: null },
  ]);
});

test('그날은 실행 키의 슬롯 시각 날짜다 — 자정 직후 슬롯은 그 날의 점이다', () => {
  assert.deepEqual(
    dailyLastRows([
      { runKey: 'news:2026-10-12T00:10', rows: 1, durationSec: null, exit: 'none', expected: null, received: null },
      { runKey: 'news:2026-10-12T08:10', rows: 2, durationSec: null, exit: 'none', expected: null, received: null },
      { runKey: 'news:2026-10-13T00:10', rows: 3, durationSec: null, exit: 'none', expected: null, received: null },
    ]).map((d) => [d.day, d.rows]),
    [['2026-10-12', 2], ['2026-10-13', 3]],
  );
});

test('누적이 아닌 데이터셋은 하루 계열이 없다 — 뉴스 기사는 실행마다 그린다', () => {
  const news = slot('news:2026-10-12T00:10', [
    cell({ taskKey: 'LOAD_DOCUMENTS', stage: 'feature', recordsOut: 300, ...at('00:10', 5) }),
  ]);
  const [series] = buildRunSeries([news], only('stock_news'));
  assert.equal(series.dailyRows, null);
});
