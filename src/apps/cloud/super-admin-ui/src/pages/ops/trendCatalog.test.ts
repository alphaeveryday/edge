/* 지표 카탈로그가 **부재를 만나는 자리** (ALPHA-738 D).
 *
 * 이 파일이 겨누는 것은 판정 산식이 아니라(그건 `trendMetrics.test.ts`) **축이 없을 때의 거동**
 * 이다. 실 응답에는 `news_funnel` 이 아예 없고 `outputs` 도 산출마다 있다
 * 없다 하므로, 지표 카탈로그가 그 부재를 어떻게 다루느냐가 곧 화면의 진실성이다:
 *
 *   ① 모듈 평가에서 죽지 않는다 — 예전에는 `export const METRICS` 가 import 시점에
 *      `output('o.doc')!.today` 를 읽어, 축이 비면 렌더가 아니라 **모듈 평가**에서 죽었다.
 *      그 자리는 ErrorBoundary 밖이라 화면이 아니라 **흰 화면**이 된다.
 *   ② 부재를 0 으로 그리지 않는다 — `chain('c.res') ?? 0` 은 "결과 생성률 0%" 라는 거짓
 *      경보를 매일 낸다. 계측 없음은 정상도 실패도 아니다.
 *   ③ 없는 과거를 그리지 않는다 — 일별 계열을 주는 응답이 없는 지표는 조회일 값 한 점뿐이고,
 *      값을 응답에서 읽지 못하는 지표는 목록에 없다.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import factsJson from '../../rules/facts-snapshot.json' with { type: 'json' };
import type { Facts } from '../../rules/types.ts';
import type { AnalysisTrendDto } from '../../domains/console/types.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';
import { buildMetrics, entityResolutionMetric, latestTask } from './trendCatalog.ts';
import { evaluateMetric } from './trendMetrics.ts';

/** 서버가 실제로 보내는 축만 — `runbook` 은 없다(계약 §축별 소스). */
const WIRED: Facts = {
  runs: [],
  tasks: [],
  datasets: [],
  outputs: [
    { id: 'o.doc', label: '뉴스 문서', today: 3861, base: 3402, unit: '건' },
    { id: 'o.trig', label: '트리거 이벤트', today: 47, base: 51, unit: '건' },
  ],
  boundary: { published_without_delivery: 0, delivery_now_nonpublished: 1, delivery_rows: 114 },
  /* 체인은 이제 온다(ALPHA-979 조각 1). 단계 값을 **서로 다르게** 둔다 — 같으면 `c.run`·`c.res`
   * 를 맞바꾸는 변이가 비율 1.0 위에서 안 보인다. */
  chain: {
    feeds: [
      { id: 'feed.batch', label: '배치 트리거', v: 20, unit: 'ETF', src: 'price_movement_trigger' },
      { id: 'feed.intraday', label: '장중 트리거', v: 65, unit: '건', src: 'minute_price_trigger' },
    ],
    stages: [
      { id: 'c.run', label: '런', batch: 16, intraday: 0, src: 'explanation_run' },
      { id: 'c.res', label: '결과', batch: 12, intraday: 0, src: 'explanation_result' },
    ],
  },
  meta: { db: '2026-08-03T16:20:00+09:00', today: '2026-08-03' },
};

/** 산출까지 통째로 빈 응답 — 계측이 하나도 안 붙은 날의 형상이다. */
const BARE: Facts = { ...WIRED, outputs: [] };

const byId = (f: Facts, id: string) => {
  const m = buildMetrics(f).find((x) => x.id === id);
  assert.ok(m, `${id} 지표가 사라졌다`);
  return m;
};

test('🔴 축이 없어도 카탈로그를 만드는 동안 죽지 않는다 — 여기서 죽으면 화면이 아니라 흰 화면이다', () => {
  /* 모듈 최상위 상수였을 때는 이 단언 자체가 불가능했다: import 만으로 이미 죽었다. */
  assert.doesNotThrow(() => buildMetrics(BARE));
  assert.doesNotThrow(() => buildMetrics(WIRED));
  /* 축이 없다고 지표가 목록에서 사라지지도 않는다 — 사라지면 계측 부채가 화면에서 증발한다 */
  assert.equal(buildMetrics(BARE).length, buildMetrics(factsJson as unknown as Facts).length);
});

test('오늘 분봉 원장이 있으면 0과 양수를 모두 목값 대신 확정값으로 쓴다', () => {
  const minute = {
    date: WIRED.meta.today,
    sessions: [{
      dataset: 'price_minute',
      windows: { overdueNoEvidence: 0 },
      priceJobs: { dead: 3 },
    }, {
      dataset: 'price_minute',
      windows: { overdueNoEvidence: 2 },
      priceJobs: { dead: 4 },
    }],
  } as MinuteStatus;
  const metrics = buildMetrics(WIRED, minute);
  assert.equal(metrics.find((m) => m.id === 'i.no_evidence')!.series.at(-1)!.value, 2);
  assert.equal(metrics.find((m) => m.id === 'i.dead_jobs')!.series.at(-1)!.value, 7);
  assert.equal(metrics.find((m) => m.id === 'i.no_evidence')!.series.at(-1)!.isMock, false);
});

test('오늘 분봉 응답이 빈 배열이어도 관측된 0이다 — 세션 부재를 MOCK 값으로 바꾸지 않는다', () => {
  const minute = { date: WIRED.meta.today, sessions: [] } as unknown as MinuteStatus;
  const metrics = buildMetrics(WIRED, minute);
  for (const id of ['i.no_evidence', 'i.dead_jobs']) {
    const point = metrics.find((m) => m.id === id)!.series.at(-1)!;
    assert.equal(point.value, 0, id);
    assert.equal(point.isMock, false, `${id}: 실측 0이 MOCK으로 바뀌었다`);
  }
});

test('추이 화면은 재조회 오류만으로 React Query가 보존한 분봉 실측을 버리지 않는다', () => {
  const source = readFileSync(new URL('./TrendPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /buildMetrics\(q\.facts, minute\.data, entityResolution\.data, analysis\.data\)/);
  assert.doesNotMatch(source, /minute\.isError\s*\?\s*undefined\s*:\s*minute\.data/);
});

test('엔티티 해소율은 API 실측만 쓰고 60% 경계에서 주황과 초록을 가른다', () => {
  const points = [
    { date: '2026-08-18', totalArguments: 10, resolvedArguments: 7, rate: 0.7 },
    { date: '2026-08-19', totalArguments: 1000, resolvedArguments: 599, rate: 0.599 },
  ];
  const low = entityResolutionMetric({ points });
  assert.deepEqual(low.series, points.map((p) => ({ date: p.date, value: p.rate, isMock: false })));
  assert.equal(evaluateMetric(low).tone, 'warn');
  assert.equal(evaluateMetric(low).label, '기준 미달');

  const edge = entityResolutionMetric({ points: [{ ...points[1], resolvedArguments: 600, rate: 0.6 }] });
  assert.equal(evaluateMetric(edge).tone, 'active');
  assert.equal(evaluateMetric(edge).label, '기준 충족');
});

test('빈 관측과 최신 0/0은 0% 경보를 만들지 않는다', () => {
  for (const trend of [
    { points: [] },
    { points: [{ date: '2026-08-19', totalArguments: 0, resolvedArguments: 0, rate: null }] },
  ]) {
    const metric = entityResolutionMetric(trend);
    assert.deepEqual(metric.series, []);
    assert.equal(evaluateMetric(metric).kind, 'uninstrumented');
    assert.equal(evaluateMetric(metric).actual, null);
  }
});

test('0/0 또는 날짜 공백을 건너뛰어 거짓 연속 추이를 만들지 않는다', () => {
  const latest = { date: '2026-08-19', totalArguments: 10, resolvedArguments: 6, rate: 0.6 };
  for (const points of [
    [
      { date: '2026-08-17', totalArguments: 10, resolvedArguments: 7, rate: 0.7 },
      { date: '2026-08-18', totalArguments: 0, resolvedArguments: 0, rate: null },
      latest,
    ],
    [
      { date: '2026-08-17', totalArguments: 10, resolvedArguments: 7, rate: 0.7 },
      latest,
    ],
  ]) {
    const metric = entityResolutionMetric({ points });
    assert.deepEqual(metric.series, [{ date: latest.date, value: latest.rate, isMock: false }]);
    assert.match(metric.help, /관측 공백은 선으로 잇지 않고/);
  }
});

test('추이 화면은 facts가 본 날짜로 해소율을 조회하고 직전 실측을 재조회 오류만으로 버리지 않는다', () => {
  const source = readFileSync(new URL('./TrendPage.tsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../../styles/ops.css', import.meta.url), 'utf8');
  assert.match(source, /useEntityResolutionTrend\(q\.ready \? q\.facts\.meta\.today : undefined, q\.ready\)/);
  assert.match(source, /buildMetrics\(q\.facts, minute\.data, entityResolution\.data, analysis\.data\)/);
  assert.doesNotMatch(source, /entityResolution\.isError\s*\?\s*undefined\s*:\s*entityResolution\.data/);
  assert.match(source, /entityResolution\.data !== undefined \|\| m\.id !== 'n\.entity_resolution_rate'/);
  /* R15 사건은 분석 실패 카드로 넘어온다 — 그 카드는 분석 추이 응답 뒤에야 DOM 에 선다 */
  assert.match(source, /useFocusRow\(q\.ready && \(!focusNeedsAnalysis \|\| analysis\.data !== undefined\)\)/);
  assert.match(source, /startsWith\('metric-a\.'\)/, '분석 카드 앵커만 분석 응답을 기다린다');
  assert.match(source, /조회 중이라 전체 이상 여부가 아직 확정되지 않았습니다/);
  assert.match(source, /조회 실패로 전체 이상 여부를 확인할 수 없습니다/);
  assert.match(source, /\$\{values\.length\}개 관측 추이/);
  assert.doesNotMatch(source, /\$\{values\.length\}영업일 추이/);
  assert.match(source, /verdict\.tone === 'warn' \? 'tr-today tr-warn'/, '주의 점도 빨강으로 그리면 안 된다');
  assert.match(css, /\.tr-warn \{ fill: var\(--warn\)/, '주의 점은 기존 주황 토큰을 써야 한다');
  assert.match(source, /DB_LEDGER 계열은 카드에 표시된 날짜의 실측/);
  assert.doesNotMatch(source, /카드의 날짜가 조회일과\s*다르면 그 지표는 이 응답 밖 축/);
});

/** v2 분석 추이 응답 — 조회일(08-03)에 전망은 행이 있고 가격변동 분석은 없다 */
const ANALYSIS: AnalysisTrendDto = {
  from: '2026-08-01',
  to: '2026-08-03',
  kinds: [
    {
      kind: 'outlook',
      points: [
        { date: '2026-08-02', completed: 140, failed: 0, running: 0, completedEtfs: 140, failedEtfs: 0 },
        /* 완료 150건·148종, 실패 5건·3종, 끝나지 않은 2건 — 다섯 수를 서로 다르게 둔다 */
        { date: '2026-08-03', completed: 150, failed: 5, running: 2, completedEtfs: 148, failedEtfs: 3 },
      ],
    },
    { kind: 'movement', points: [{ date: '2026-08-02', completed: 9, failed: 1, running: 0, completedEtfs: 8, failedEtfs: 1 }] },
  ],
};
const analysisCard = (id: string, analysis?: AnalysisTrendDto) => {
  const m = buildMetrics(WIRED, undefined, undefined, analysis).find((x) => x.id === id);
  assert.ok(m, `${id} 지표가 사라졌다`);
  return m;
};

test('🔴 분석 결과 카드는 v2 분석 추이의 조회일 값을 읽는다 — 멈춘 옛 엔진 표를 읽지 않는다', () => {
  /* 값 넷을 서로 다른 수로 건다 — 완료 건수와 종 수, 실패 건수와 종 수를 맞바꾸는 변이가 죽는다 */
  const completed = analysisCard('a.outlook_completed', ANALYSIS);
  assert.deepEqual(completed.series, [{ date: '2026-08-03', value: 150, isMock: false }]);
  const failed = analysisCard('a.outlook_failed_etfs', ANALYSIS);
  assert.deepEqual(failed.series, [{ date: '2026-08-03', value: 3, isMock: false }]);
  assert.equal(evaluateMetric(failed).kind, 'abnormal', '실패 ETF 1종 이상이면 이상이다');
  /* 완료 건수는 기준값을 주는 응답이 없어 판정하지 않는다 — 임계를 새로 만들지 않았다 */
  assert.equal(evaluateMetric(completed).label, '기준 없음');
  /* 옛 엔진의 체인·ETF 원장이 실려 와도 분석 카드는 그것을 읽지 않는다 */
  const withV1 = buildMetrics(
    { ...WIRED, etf_ledger: { rows: [{ etf: 'A', name: 'a', triggered: true, outcome: 'FAILED' }] } },
    undefined, undefined, ANALYSIS,
  );
  assert.equal(evaluateMetric(withV1.find((m) => m.id === 'a.outlook_failed_etfs')!).actual, 3);
  for (const id of ['a.results', 'a.run_to_result', 'a.failed_etfs']) {
    assert.ok(!withV1.some((m) => m.id === id), `옛 엔진 표를 읽는 카드 ${id} 가 남아 있다`);
  }
});

test('🔴 끝나지 않은 분석(running)은 실패 ETF 수에 들어가지 않는다', () => {
  const onlyRunning: AnalysisTrendDto = {
    ...ANALYSIS,
    kinds: [
      { kind: 'outlook', points: [{ date: '2026-08-03', completed: 0, failed: 0, running: 7, completedEtfs: 0, failedEtfs: 0 }] },
      { kind: 'movement', points: [] },
    ],
  };
  const failed = analysisCard('a.outlook_failed_etfs', onlyRunning);
  assert.equal(evaluateMetric(failed).actual, 0, '진행 중인 분석을 실패로 셌다');
  assert.equal(evaluateMetric(failed).kind, 'normal');
  assert.match(failed.help, /끝나지 않은 7건/, '끝나지 않은 건수는 따로 밝힌다');
  /* 행이 있던 날의 완료 0건은 0 이다 — 부재와 다르다 */
  assert.equal(evaluateMetric(analysisCard('a.outlook_completed', onlyRunning)).actual, 0);
});

test('🔴 실패했다가 같은 날 완료된 ETF 는 실패 ETF 카드에 들지 않는다 — 카드는 실패 건수가 아니라 끝내 완료하지 못한 ETF 수를 그린다', () => {
  /* dev 10-13 의 형상이다: 가격변동 분석은 실패 행이 22건인데 끝내 완료하지 못한 ETF 는 3종,
   * 전망은 실패 3건에 0종. 실패 건수나 "실패한 적 있는 ETF"를 그리면 이 카드가 매일 임계를 넘는다. */
  const retried: AnalysisTrendDto = {
    ...ANALYSIS,
    kinds: [
      { kind: 'outlook', points: [{ date: '2026-08-03', completed: 150, failed: 3, running: 0, completedEtfs: 148, failedEtfs: 0 }] },
      { kind: 'movement', points: [{ date: '2026-08-03', completed: 195, failed: 22, running: 0, completedEtfs: 119, failedEtfs: 3 }] },
    ],
  };
  const outlook = analysisCard('a.outlook_failed_etfs', retried);
  assert.equal(evaluateMetric(outlook).actual, 0, '다시 돌아 완료된 실패를 실패 ETF 로 그렸다');
  assert.equal(evaluateMetric(outlook).kind, 'normal');
  const movement = analysisCard('a.movement_failed_etfs', retried);
  assert.equal(evaluateMetric(movement).actual, 3, '실패 건수(22)를 실패 ETF 수로 그렸다');
  assert.equal(evaluateMetric(movement).kind, 'abnormal', '끝내 완료하지 못한 ETF 는 여전히 이상이다');
  /* 도움말이 새 정의를 말하고, 실패 건수는 따로 밝힌다 */
  assert.match(movement.help, /끝내 완료되지 않은 ETF/);
  assert.match(movement.help, /실패 22건, 끝내 완료하지 못한 ETF 3종/);
});

test('🔴 조회일에 분석 행이 없으면 0 이 아니라 계측 없음이다 — 가격변동 분석은 장이 열린 날에만 생긴다', () => {
  for (const id of ['a.movement_completed', 'a.movement_failed_etfs']) {
    const m = analysisCard(id, ANALYSIS);
    assert.deepEqual(m.series, [], `${id} 에 없는 값이 생겼다`);
    assert.equal(evaluateMetric(m).kind, 'uninstrumented', id);
    assert.match(m.help, /행이 없다/, `${id} 가 사유를 안 밝힌다`);
  }
});

test('🔴 분석 추이 응답이 없어도 카드는 서고 계측 없음이다 — 그 엔드포인트가 없는 API 에서 화면이 깨지지 않는다', () => {
  /* UI 가 API 보다 먼저 배포되면 `/console/trends/analysis` 가 404 다 — `buildMetrics` 는 넷째
   * 인자 없이 불린다. 카드가 목록에서 빠지면 계측 부채가 화면에서 사라진다. */
  for (const id of ['a.outlook_completed', 'a.outlook_failed_etfs', 'a.movement_completed', 'a.movement_failed_etfs']) {
    const m = analysisCard(id);
    assert.deepEqual(m.series, [], id);
    assert.equal(evaluateMetric(m).kind, 'uninstrumented', id);
    assert.match(m.help, /응답을 받지 못해/, id);
  }
});

test('산출 축이 안 실린 날 그 지표는 판정 불가다 — `!` 로 죽지도, 0 으로 서지도 않는다', () => {
  for (const id of ['n.documents', 'a.trigger_events']) {
    assert.equal(byId(BARE, id).comparisonType, 'uninstrumented', id);
  }
  /* 실려 있으면 판정한다 — 그리고 기준선은 규칙(R13)이 쓰는 `base` 그대로여야 한다.
   * 화면이 자기 중앙값을 따로 만들면 같은 사실에 두 편차가 선다. */
  const doc = byId(WIRED, 'n.documents');
  const v = evaluateMetric(doc);
  assert.equal(v.actual, 3861);
  assert.equal(v.expected, 3402, '중앙값이 응답의 base 와 갈렸다');
});

test('🔴 기준(base)이 없으면 과거를 지어내지 않는다 — 휴장일에 "정상 범위" 가 서던 자리다', () => {
  /* 서버는 장에 매인 산출의 기준을 휴장일에 **일부러 비운다**(계약 §무엇이 실제로 나가는가).
   * 그때 오늘 값 둘레로 과거를 만들면 중앙값 == 오늘 → 편차 0% → `정상 범위` 가 선다:
   * 원장이 "평소를 모른다"고 말한 날에 화면이 "평소와 같다"고 답한다. */
  const noBase: Facts = {
    ...WIRED,
    outputs: [{ id: 'o.doc', label: '뉴스 문서', today: 2977, base: null, unit: '건' }],
  };
  const m = byId(noBase, 'n.documents');
  assert.equal(m.series.length, 1, '없는 과거를 만들었다');
  const v = evaluateMetric(m);
  assert.equal(v.actual, 2977, '오늘 값은 실측이라 그대로 그린다');
  assert.equal(v.expected, null, '기준을 지어냈다');
  assert.equal(v.deltaPct, null, '기준이 없는데 편차를 냈다');
  assert.equal(v.kind, 'uninstrumented', '판정을 만들어 냈다');
});

test('🔴 완전성 분모 0 을 초록으로 그리지 않는다 — 0/0 은 NaN 이고 NaN 비교는 전부 false 다', () => {
  /* 검증 경계는 음수만 막고 `0` 은 정당한 건수로 통과시킨다. 그때 `0/0 = NaN` 이 되는데
   * `NaN < 0.95` 가 **false** 라 minRatio 판정이 `기준 충족`(초록)으로 서고 값은 `NaN%` 다 —
   * 판정 불가여야 할 자리에 가장 강한 안심이 선다. */
  const task = (completenessExpected: number, completenessReceived: number) => ({
    ...WIRED,
    tasks: [{
      task_key: 'PRICE_COLLECTION_KIS', run_id: 'r', pipeline_type: 'etf-daily',
      trading_date: '2026-08-03', stage: 'raw', dataset: 'price_daily', required: true,
      plan_status: 'DUE', task_outcome: 'FULFILLED', data_status: 'VALID',
      completeness_expected: completenessExpected, completeness_received: completenessReceived,
      attempts: 1,
    }],
  });
  const zero = evaluateMetric(byId(task(0, 0), 'b.price_daily'));
  assert.equal(zero.kind, 'uninstrumented', '분모 0 에 판정을 세웠다');
  assert.ok(!Number.isNaN(zero.actual ?? 0), `NaN 이 값 자리에 섰다 — ${zero.actual}`);
  assert.notEqual(zero.tone, 'active', '판정 불가를 초록으로 그렸다');
  /* 분모가 있으면 그때는 판정한다 — 부재 갈래가 값 갈래를 잡아먹지 않았는지 함께 본다 */
  const real = evaluateMetric(byId(task(100, 94), 'b.price_daily'));
  assert.equal(real.kind, 'abnormal', '94% 는 최소 95% 미달이다');
});

test('같은 작업 키의 여러 슬롯은 배열 순서가 아니라 가장 늦은 run_key를 오늘 값으로 쓴다', () => {
  const base = {
    task_key: 'PRICE_COLLECTION_KIS', pipeline_type: 'etf-daily', trading_date: '2026-08-03',
    stage: 'raw', dataset: 'price_daily', required: true, plan_status: 'DUE',
    task_outcome: 'FULFILLED', data_status: 'VALID', completeness_expected: 100, attempts: 1,
  };
  const facts: Facts = {
    ...WIRED,
    tasks: [
      { ...base, run_id: 'etf-daily:2026-08-03T16:40', completeness_received: 99 },
      { ...base, run_id: 'etf-daily:2026-08-03T15:40', completeness_received: 40 },
    ],
  };
  assert.equal(latestTask(facts, 'PRICE_COLLECTION_KIS')?.completeness_received, 99);
  assert.equal(evaluateMetric(byId(facts, 'b.price_daily')).actual, 0.99);
});

test('🔴 실측 과거가 없으면 한 점만 낸다 — 과거 구간을 만들면 없는 추이가 그려진다', () => {
  /* 모든 축이 실린 응답이다. 축이 비면 계열이 `[]` 라 "한 점 이하" 단언이 공허하게 참이 된다 —
   * 그래서 **정확히 한 점**을 지표 id 목록과 함께 건다. */
  const task = {
    run_id: 'r', pipeline_type: 'etf-daily', trading_date: '2026-08-03', stage: 'raw', required: true,
    plan_status: 'DUE', task_outcome: 'FULFILLED', data_status: 'VALID',
    completeness_expected: 100, completeness_received: 99, attempts: 1,
  };
  const full = {
    ...WIRED,
    tasks: [
      { ...task, task_key: 'PRICE_COLLECTION_KIS', dataset: 'price_daily' },
      { ...task, task_key: 'ETF_HOLDINGS_COLLECTION_KRX', dataset: 'etf_holdings' },
    ],
    datasets: [
      { id: 'investor_flow', actual_as_of: '2026-07-31', expected_as_of: '2026-08-03' },
      { id: 'etf_nav', actual_as_of: '2026-08-03', expected_as_of: '2026-08-03' },
    ],
    etf_ledger: { rows: [{ etf: 'A', name: 'a', triggered: true, outcome: 'FAILED' }] },
  } as unknown as Facts;
  const minute = {
    date: WIRED.meta.today,
    sessions: [{ dataset: 'price_minute', windows: { overdueNoEvidence: 1 }, priceJobs: { dead: 1 } }],
  } as unknown as MinuteStatus;
  const trend = {
    points: [
      { date: '2026-08-02', totalArguments: 10, resolvedArguments: 7, rate: 0.7 },
      { date: '2026-08-03', totalArguments: 10, resolvedArguments: 8, rate: 0.8 },
    ],
  };
  /* 조회일(08-03)에 두 종류 모두 행이 있는 분석 추이 */
  const analysis: AnalysisTrendDto = {
    from: '2026-08-02',
    to: '2026-08-03',
    kinds: (['outlook', 'movement'] as const).map((kind) => ({
      kind,
      points: [
        { date: '2026-08-02', completed: 9, failed: 0, running: 0, completedEtfs: 9, failedEtfs: 0 },
        { date: '2026-08-03', completed: 12, failed: 1, running: 0, completedEtfs: 11, failedEtfs: 1 },
      ],
    })),
  };
  const metrics = buildMetrics(full, minute, trend, analysis);
  const drawn = metrics.filter((m) => m.series.length > 0);
  assert.deepEqual(
    drawn.map((m) => m.id),
    [
      'b.price_daily', 'b.etf_holdings', 'b.investor_flow', 'b.etf_nav',
      'i.no_evidence', 'i.dead_jobs',
      'n.documents', 'n.entity_resolution_rate',
      'a.outlook_completed', 'a.outlook_failed_etfs', 'a.movement_completed', 'a.movement_failed_etfs',
      'a.trigger_events',
    ],
    '전제: 계측 없는 한 장을 뺀 모든 카드가 값을 그린다',
  );
  for (const m of drawn) {
    assert.ok(m.series.every((p) => !p.isMock), `${m.id} 가 목 점을 그린다`);
    if (m.id === 'n.entity_resolution_rate') continue;
    assert.deepEqual(
      m.series.map((p) => p.date),
      [full.meta.today],
      `${m.id} 가 조회일 한 점이 아니다 — 없는 과거를 만들었다`,
    );
  }
  /* 실측 계열은 그대로다 — 한 점 규칙이 일별 API 의 계열까지 자르면 안 된다 */
  assert.deepEqual(
    metrics.find((m) => m.id === 'n.entity_resolution_rate')!.series,
    trend.points.map((p) => ({ date: p.date, value: p.rate, isMock: false })),
  );
  /* 기준은 응답의 `base` 다 — 화면이 과거를 만들어 중앙값을 맞추던 자리다 */
  assert.equal(evaluateMetric(metrics.find((m) => m.id === 'a.trigger_events')!).expected, 51);
  /* 기준을 주는 응답이 없는 산출량은 숫자를 지어 판정하지 않는다 */
  assert.equal(evaluateMetric(metrics.find((m) => m.id === 'a.movement_completed')!).label, '기준 없음');
});

test('🔴 값을 응답에서 읽지 못하는 카드는 목록에 없다 — 코드에 박힌 숫자·얼린 스냅샷을 오늘 값으로 그리지 않는다', () => {
  const ids = buildMetrics(factsJson as unknown as Facts).map((m) => m.id);
  for (const id of ['i.disclosures', 'i.evidence_ratio', 'i.watermark_delay', 'n.universe_rate']) {
    assert.ok(!ids.includes(id), `${id} 가 돌아왔다`);
  }
});

test('응답이 목이라고 밝힌 데이터셋의 지연은 목 점이다 — 출처 칩과 점이 갈리지 않는다', () => {
  const mocked = {
    ...WIRED,
    datasets: [{ id: 'etf_nav', actual_as_of: '2026-08-03', expected_as_of: '2026-08-03', mock: true }],
  } as unknown as Facts;
  const m = byId(mocked, 'b.etf_nav');
  assert.equal(m.source, 'MOCK');
  assert.deepEqual(m.series, [{ date: '2026-08-03', value: 0, isMock: true }]);
});

test('🔴 분봉 응답이 없으면 분봉 지표는 판정 불가다 — 숫자를 대신 넣지 않는다', () => {
  for (const id of ['i.no_evidence', 'i.dead_jobs']) {
    const m = byId(WIRED, id);
    assert.deepEqual(m.series, [], `${id} 에 없는 값이 생겼다`);
    assert.equal(evaluateMetric(m).kind, 'uninstrumented', id);
  }
  assert.match(byId(WIRED, 'i.dead_jobs').help, /응답을 받지 못해/, '조회 중일 수도 있다 — 없다고 단정하지 않는다');
  /* 다른 날짜의 분봉 응답도 조회일의 값이 아니다 */
  const stale = { date: '2026-08-02', sessions: [] } as unknown as MinuteStatus;
  assert.deepEqual(buildMetrics(WIRED, stale).find((m) => m.id === 'i.dead_jobs')!.series, []);
});
