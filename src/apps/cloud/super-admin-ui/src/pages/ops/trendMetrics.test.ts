/* 지표 판정과 목 계열 (ALPHA-738).
 *
 * 지키는 의도:
 *   · **모든 지표에 ±25% 를 적용하지 않는다** — 완전성·지연·결손 수는 각자의 규칙이 있다.
 *   · 중앙값·정상 범위·편차·판정이 **같은 계열**에서 나온다(따로 들고 있으면 어긋난다).
 *   · 계측 없음은 정상도 실패도 아니다.
 *   · 판정 문구는 하드코딩이 아니라 구조화 값에서 만들어진다.
 *
 * 실행: node --test src/pages/ops/trendMetrics.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildSeries, businessDays, evaluateMetric, formatValue } from './trendMetrics.ts';
import type { ComparisonType, Metric } from './trendMetrics.ts';
import { median } from './trendSeries.ts';
import { buildMetrics, tradingLag } from './trendCatalog.ts';
import factsJson from '../../rules/facts-snapshot.json' with { type: 'json' };
import type { Facts } from '../../rules/types.ts';

/* 카탈로그 계약(분류·라벨·드릴다운)은 **축이 전부 있는** 사실 위에서 검사한다 — 스냅샷이
 * 그 모양을 가진 유일한 픽스처다. 축이 없을 때의 거동은 `trendCatalog.test.ts` 가 본다. */
const METRICS = buildMetrics(factsJson as unknown as Facts);

const metric = (o: Partial<Metric>): Metric => ({
  id: 'm',
  label: 'm',
  group: 'batch',
  unit: '건',
  metricType: 'volume',
  comparisonType: 'medianDelta',
  threshold: 0.25,
  direction: 'stable',
  source: 'DB_LEDGER',
  series: [],
  help: '',
  drill: { href: '/', label: '상세' },
  ...o,
});

const flat = (values: number[]): Metric['series'] =>
  values.map((value, i) => ({ date: `2026-07-${String(20 + i).padStart(2, '0')}`, value, isMock: true }));

/* ── 판정 규칙이 지표마다 다르다 ── */

test('산출량은 중앙값 대비 ±25% 로 본다', () => {
  const v = evaluateMetric(metric({ series: flat([100, 100, 100, 100, 70]) }));
  assert.equal(v.kind, 'abnormal');
  assert.equal(v.deltaPct, -30);
  assert.match(v.label, /분포 밖 · 감소/);
  assert.deepEqual(v.band, [75, 125]);
  /* 근거 문구가 임계에서 만들어진다 — 하드코딩이 아니다 */
  assert.match(v.basis, /임계 ±25%/);
});

test('증가 이상도 이상이다 — 감소만 보지 않는다', () => {
  const v = evaluateMetric(metric({ series: flat([100, 100, 100, 100, 140]) }));
  assert.equal(v.kind, 'abnormal');
  assert.match(v.label, /증가/);
  assert.equal(v.direction, 'up');
});

test('완전성은 ±25% 가 아니라 계약 최소 비율로 본다', () => {
  const m = metric({
    comparisonType: 'minRatio' as ComparisonType,
    threshold: 0.95,
    metricType: 'coverage',
    direction: 'higherIsBetter',
    /* 중앙값 대비로는 −4% 라 분포 규칙이면 정상이지만, 계약 기준으로는 미달이다 */
    series: flat([1, 1, 1, 1, 0.94]),
  });
  const v = evaluateMetric(m);
  assert.equal(v.kind, 'abnormal');
  assert.equal(v.label, '기준 미달');
  assert.equal(v.expected, 0.95);
  assert.equal(v.deltaPct, null, '완전성에 편차%를 붙이지 않는다');
  assert.match(v.basis, /최소 95% 기준 미달/);
  assert.equal(formatValue(m, v.actual), '94.0%');
});

test('결손 수는 0 초과면 이상이다 — 분포로 보지 않는다', () => {
  const m = metric({
    comparisonType: 'maxCount' as ComparisonType,
    threshold: 0,
    unit: '창',
    metricType: 'defect',
    series: flat([0, 0, 0, 0, 4]),
  });
  const v = evaluateMetric(m);
  assert.equal(v.kind, 'abnormal');
  assert.match(v.basis, /허용 0창 초과/);
  assert.deepEqual(v.band, [0, 0]);
  assert.equal(evaluateMetric(metric({ ...m, series: flat([0, 0, 0, 0, 0]) })).kind, 'normal');
});

test('기준일 지연·워터마크는 각자의 허용치로 본다', () => {
  const lag = evaluateMetric(
    metric({ comparisonType: 'maxLagDays', threshold: 0, unit: '거래일', series: flat([0, 0, 0, 1]) }),
  );
  assert.equal(lag.kind, 'abnormal');
  assert.match(lag.basis, /허용 0거래일 초과/);

  const wm = evaluateMetric(
    metric({ comparisonType: 'maxDelayMinutes', threshold: 5, unit: '분', series: flat([2, 2, 2, 3]) }),
  );
  assert.equal(wm.kind, 'normal');
  assert.match(wm.basis, /허용 5분 이내/);
});

test('계측이 없으면 정상으로도 실패로도 세지 않는다', () => {
  const v = evaluateMetric(metric({ comparisonType: 'uninstrumented', threshold: null, series: [] }));
  assert.equal(v.kind, 'uninstrumented');
  assert.equal(v.actual, null);
  assert.equal(v.expected, null);
  assert.notEqual(v.tone, 'active');
  assert.notEqual(v.tone, 'blocked');
});

test('임계가 선언되지 않으면 판정하지 않는다', () => {
  const v = evaluateMetric(metric({ comparisonType: 'minRatio', threshold: null, series: flat([1, 1]) }));
  assert.equal(v.kind, 'uninstrumented');
});

/* ── 계열 하나에서 모든 숫자가 나온다 ── */

test('중앙값·정상 범위·편차가 모두 같은 계열에서 계산된다', () => {
  const s = flat([80, 90, 100, 110, 120, 60]);
  const v = evaluateMetric(metric({ series: s }));
  const history = s.slice(0, -1).map((p) => p.value);
  assert.equal(v.expected, median(history), '기준선 = 계열의 중앙값');
  assert.equal(v.actual, s[s.length - 1].value, '오늘 값 = 계열의 마지막');
  assert.equal(v.deltaPct, Math.round(((60 - 100) / 100) * 100));
  assert.deepEqual(v.band, [75, 125]);
});

test('목 계열의 중앙값을 규칙이 쓰는 기준선에 고정할 수 있다', () => {
  /* 두 화면이 다른 편차를 말하면 안 된다 — pin 이 그 계약이다 */
  for (const pin of [6122, 31.5, 22, 0.42]) {
    const s = buildSeries({ today: 1, pin, todayIsMock: true, endDate: '2026-08-03' });
    assert.equal(median(s.slice(0, -1).map((p) => p.value)), pin, `pin=${pin}`);
  }
});

test('계열의 날짜 축은 주말을 건너뛴다', () => {
  const days = businessDays('2026-08-03', 4);
  assert.deepEqual(days, ['2026-07-29', '2026-07-30', '2026-07-31', '2026-08-03']);
});

test('결손 수 계열은 정수이고 음수가 되지 않는다', () => {
  const s = buildSeries({ today: 4, pin: 0, integer: true, min: 0, todayIsMock: true, endDate: '2026-08-03' });
  for (const p of s) {
    assert.ok(Number.isInteger(p.value), `정수: ${p.value}`);
    assert.ok(p.value >= 0, `음수 아님: ${p.value}`);
  }
});

/* ── 카탈로그 계약 ── */

test('기준일 지연은 두 실측 날짜 사이의 거래일 수다', () => {
  assert.equal(tradingLag('2026-08-03', '2026-08-03'), 0);
  assert.equal(tradingLag('2026-08-01', '2026-08-03'), 1, '주말은 세지 않는다');
  assert.equal(tradingLag('2026-07-30', '2026-08-03'), 2);
  assert.equal(tradingLag(null, '2026-08-03'), null, '값이 없으면 0 이 아니라 모름');
});

test('네 분류가 모두 지표를 갖는다 — 뉴스가 화면을 대표하지 않는다', () => {
  for (const g of ['batch', 'intraday', 'news', 'analysis'] as const) {
    const n = METRICS.filter((m) => m.group === g).length;
    assert.ok(n >= 3, `${g} 지표 ${n}개`);
  }
  const news = METRICS.filter((m) => m.group === 'news');
  assert.ok(news.length <= 3, `뉴스는 2~3개로 줄인다 — ${news.length}개`);
  /* assertion·source event 절대 수를 동급 카드로 나열하지 않는다 */
  assert.equal(news.filter((m) => m.metricType === 'volume').length, 1);
});

test('일배치는 데이터셋마다 식별되고 트리거가 대표하지 않는다', () => {
  const batch = METRICS.filter((m) => m.group === 'batch');
  for (const ds of ['price_daily', 'etf_holdings', 'investor_flow', 'etf_nav', 'disclosures']) {
    assert.ok(batch.some((m) => m.drill.href.includes(ds)), `${ds} 지표 있음`);
  }
  assert.ok(!batch.some((m) => m.label.includes('트리거')), '트리거는 일배치 대표 지표가 아니다');
  assert.ok(METRICS.some((m) => m.group === 'analysis' && m.label.includes('트리거')), '트리거는 분석 보조로 남는다');
});

test('전달 지표는 하나도 없다', () => {
  for (const m of METRICS) {
    for (const banned of ['게시', '발번', '테넌트', '전달']) {
      assert.ok(!m.label.includes(banned), `${m.id} 라벨에 ${banned}`);
    }
    assert.ok(!m.drill.href.startsWith('/ops/delivery'), `${m.id} 드릴다운이 전달 화면`);
  }
});

test('정상·이상·계측 없음이 모두 하나 이상 있다 — 검수용 목의 조건', () => {
  const kinds = METRICS.map((m) => evaluateMetric(m).kind);
  for (const k of ['normal', 'abnormal', 'uninstrumented'] as const) {
    assert.ok(kinds.includes(k), `${k} 지표 없음`);
  }
});

test('모든 지표가 판정 메타데이터와 드릴다운을 갖는다', () => {
  for (const m of METRICS) {
    assert.ok(m.unit && m.metricType && m.comparisonType, m.id);
    assert.ok(m.help.length > 20, `${m.id} 설명 없음`);
    assert.ok(m.drill.href.startsWith('/'), `${m.id} 드릴다운 없음`);
    /* 임계가 필요한 비교 유형은 임계를 갖는다 */
    if (m.comparisonType !== 'uninstrumented') assert.notEqual(m.threshold, null, `${m.id} 임계 없음`);
  }
});

