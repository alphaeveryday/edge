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
 *   ③ 스냅샷을 실측이라 말하지 않는다 — 뉴스 퍼널은 응답 밖 축이라 오늘 점도 목이다.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import factsJson from '../../rules/facts-snapshot.json' with { type: 'json' };
import type { Facts } from '../../rules/types.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';
import { buildMetrics, entityResolutionMetric, latestTask } from './trendCatalog.ts';
import { FUNNEL_DATE } from './newsFunnelSnapshot.ts';
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
  assert.match(source, /buildMetrics\(q\.facts, minute\.data, entityResolution\.data\)/);
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
  assert.match(source, /buildMetrics\(q\.facts, minute\.data, entityResolution\.data\)/);
  assert.doesNotMatch(source, /entityResolution\.isError\s*\?\s*undefined\s*:\s*entityResolution\.data/);
  assert.match(source, /entityResolution\.data !== undefined \|\| m\.id !== 'n\.entity_resolution_rate'/);
  assert.match(source, /조회 중이라 전체 이상 여부가 아직 확정되지 않았습니다/);
  assert.match(source, /조회 실패로 전체 이상 여부를 확인할 수 없습니다/);
  assert.match(source, /\$\{values\.length\}개 관측 추이/);
  assert.doesNotMatch(source, /\$\{values\.length\}영업일 추이/);
  assert.match(source, /verdict\.tone === 'warn' \? 'tr-today tr-warn'/, '주의 점도 빨강으로 그리면 안 된다');
  assert.match(css, /\.tr-warn \{ fill: var\(--warn\)/, '주의 점은 기존 주황 토큰을 써야 한다');
  assert.match(source, /DB_LEDGER 계열은 카드에 표시된 날짜의 실측/);
  assert.doesNotMatch(source, /카드의 날짜가 조회일과\s*다르면 그 지표는 이 응답 밖 축/);
});

test('🔴 체인 축 부재를 0 으로 그리지 않는다 — "결과 생성률 0%" 는 거짓 경보다', () => {
  /* 축이 배선된 뒤에도(ALPHA-979 조각 1) 이 갈래는 살아 있다 — 그 축을 안 싣던 배포본을 보는
   * 동안이다. 부재를 0 으로 접으면 그때 "결과 생성률 0%" 가 매일 뜬다. */
  const noChain: Facts = { ...WIRED, chain: undefined };
  for (const id of ['a.results', 'a.run_to_result']) {
    const m = byId(noChain, id);
    assert.equal(m.comparisonType, 'uninstrumented', `${id} 가 판정을 만들어 냈다`);
    assert.deepEqual(m.series, [], `${id} 에 없는 계열이 생겼다`);
    const v = evaluateMetric(m);
    assert.equal(v.kind, 'uninstrumented', `${id} 판정 종류`);
    assert.equal(v.actual, null, `${id} 오늘 값이 0 으로 그려졌다`);
  }
});

test('🔴 체인 축이 오면 그 두 지표가 실제로 선다 — 부재 갈래가 값 갈래를 잡아먹지 않았다', () => {
  /* ⭐ 부재만 단언하면 **축을 아예 안 읽는 회귀가 초록**이다(부재가 통과 조건이라 공허하게 참).
   * 값 갈래를 같이 건다 — 그래야 어댑터가 `chain` 을 떨구거나 여기가 계속 `null` 을 읽는 변이가
   * 죽는다. 값은 픽스처의 `c.res`/`c.run` = 12/16 이라 **1.0 이 아니다**: 둘을 맞바꾸는 변이는
   * 비율이 1 일 때 안 보인다. */
  const results = byId(WIRED, 'a.results');
  assert.notEqual(results.comparisonType, 'uninstrumented', '축이 왔는데 계속 판정 불가다');
  assert.equal(evaluateMetric(results).actual, 12);

  const rate = byId(WIRED, 'a.run_to_result');
  assert.notEqual(rate.comparisonType, 'uninstrumented', '축이 왔는데 계속 판정 불가다');
  assert.equal(evaluateMetric(rate).actual, 12 / 16);
});

test('🔴 per-ETF 원장 부재를 "실패 0종" 으로 그리지 않는다', () => {
  const m = byId(WIRED, 'a.failed_etfs');
  assert.equal(m.comparisonType, 'uninstrumented');
  assert.equal(evaluateMetric(m).actual, null, '0 이면 "오늘 실패한 ETF 가 없다"가 된다');
  /* 축이 있으면 그때는 실제로 센다 — 부재 갈래가 값 갈래를 잡아먹지 않았는지 함께 본다 */
  const withLedger = byId(
    { ...WIRED, etf_ledger: { rows: [{ etf: 'A', name: 'a', triggered: true, outcome: 'FAILED' }], mock: true } },
    'a.failed_etfs',
  );
  assert.equal(evaluateMetric(withLedger).actual, 1);
  assert.equal(withLedger.source, 'MOCK');

  const withRealLedger = byId(
    { ...WIRED, etf_ledger: { rows: [{ etf: 'A', name: 'a', triggered: true, outcome: 'FAILED' }] } },
    'a.failed_etfs',
  );
  assert.equal(withRealLedger.source, 'DB_LEDGER', '실원장을 목으로 표시했다');
  assert.doesNotMatch(withRealLedger.help, /계측되지 않아|목 데이터/, '실원장을 목이라고 설명했다');
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

test('🔴 뉴스 퍼널 지표는 오늘 점도 스냅샷이다 — "오늘 실측" 이라고 말하면 안 된다', () => {
  /* `news_funnel` 은 이 응답 밖 축이라(계약 §범위 결정) 값이 응답의 거래일 것이 아니다.
   * `todayIsMock: false` 로 두면 카드가 "오늘 실측 · 과거 MOCK" 칩을 달아 **응답에서 온 값**
   * 이라고 말한다 — 화면에서 이 둘을 가르는 유일한 축이 그 플래그다. */
  for (const id of ['n.universe_rate']) {
    const m = byId(WIRED, id);
    /* `SNAPSHOT` 이지 `MOCK` 이 아니다 — 한때 관측한 값과 지어낸 값을 한 칸에 그리지 않는다.
     * `DB_LEDGER` 였다면 오늘 원장에서 온 수라고 말하는 것이라 그쪽이 가장 나쁘다. */
    assert.equal(m.source, 'SNAPSHOT', `${id} 출처 표기 — 원장도 목도 아니다`);
    assert.ok(m.series.length > 0, `${id} 계열 없음`);
    assert.ok(
      m.series.every((p) => p.isMock),
      `${id} 의 오늘 점이 실측으로 서 있다`,
    );
    assert.ok(m.help.includes('스냅샷'), `${id} 설명이 출처를 안 밝힌다`);
  }
});

test('🔴 퍼널 계열은 **스냅샷 날**에 찍힌다 — 조회일에 찍으면 과거 값이 오늘 값으로 따라온다', () => {
  /* ⚠️ 이 단언은 **조회일 ≠ 스냅샷 날**인 사실 위에서만 축을 가른다. `WIRED.meta.today` 는
   * 스냅샷과 같은 날이라 그대로 쓰면 두 축이 겹쳐 맞바꿔도 통과한다(이 트랙에서 이미 겪은
   * 함정: 픽스처의 두 자리가 같은 값이면 deepEqual 로도 안 잡힌다). */
  const other = '2026-08-07';
  assert.notEqual(other, FUNNEL_DATE, '전제: 조회일과 스냅샷 날이 달라야 축이 갈린다');
  const later: Facts = { ...WIRED, meta: { ...WIRED.meta, today: other } };
  const last = (id: string) => {
    const s = byId(later, id).series;
    return s[s.length - 1]?.date;
  };
  for (const id of ['n.universe_rate']) {
    assert.equal(last(id), FUNNEL_DATE, `${id} 계열이 조회일에 찍혔다`);
  }
  /* 응답에서 온 지표는 반대로 **조회일**에 찍힌다 — 한쪽만 맞추면 축이 뒤바뀌어도 통과한다 */
  assert.equal(last('n.documents'), other, '응답 지표가 스냅샷 날에 찍혔다');
});
