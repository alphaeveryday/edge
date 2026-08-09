/* 지표 카탈로그가 **부재를 만나는 자리** (ALPHA-738 D).
 *
 * 이 파일이 겨누는 것은 판정 산식이 아니라(그건 `trendMetrics.test.ts`) **축이 없을 때의 거동**
 * 이다. 실 응답에는 `chain`·`etf_ledger`·`news_funnel` 이 아예 없고 `outputs` 도 산출마다 있다
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
import { test } from 'node:test';
import factsJson from '../../rules/facts-snapshot.json' with { type: 'json' };
import type { Facts } from '../../rules/types.ts';
import { buildMetrics } from './trendCatalog.ts';
import { FUNNEL_DATE } from './newsFunnelSnapshot.ts';
import { evaluateMetric } from './trendMetrics.ts';

/** 서버가 실제로 보내는 축만 — `chain`·`etf_ledger`·`runbook`·`meta.aws` 는 없다(계약 §축별 소스). */
const WIRED: Facts = {
  runs: [],
  tasks: [],
  datasets: [],
  outputs: [
    { id: 'o.doc', label: '뉴스 문서', today: 3861, base: 3402, unit: '건' },
    { id: 'o.trig', label: '트리거 이벤트', today: 47, base: 51, unit: '건' },
  ],
  boundary: { published_without_delivery: 0, delivery_now_nonpublished: 1, delivery_rows: 114 },
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

test('🔴 체인 축 부재를 0 으로 그리지 않는다 — "결과 생성률 0%" 는 거짓 경보다', () => {
  /* `chain` 은 실 응답에 **항상** 없다(소스 0). 그러니 이 두 지표는 실 운영에서 매일 이 갈래다. */
  for (const id of ['a.results', 'a.run_to_result']) {
    const m = byId(WIRED, id);
    assert.equal(m.comparisonType, 'uninstrumented', `${id} 가 판정을 만들어 냈다`);
    assert.deepEqual(m.series, [], `${id} 에 없는 계열이 생겼다`);
    const v = evaluateMetric(m);
    assert.equal(v.kind, 'uninstrumented', `${id} 판정 종류`);
    assert.equal(v.actual, null, `${id} 오늘 값이 0 으로 그려졌다`);
  }
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

test('🔴 뉴스 퍼널 지표는 오늘 점도 스냅샷이다 — "오늘 실측" 이라고 말하면 안 된다', () => {
  /* `news_funnel` 은 이 응답 밖 축이라(계약 §범위 결정) 값이 응답의 거래일 것이 아니다.
   * `todayIsMock: false` 로 두면 카드가 "오늘 실측 · 과거 MOCK" 칩을 달아 **응답에서 온 값**
   * 이라고 말한다 — 화면에서 이 둘을 가르는 유일한 축이 그 플래그다. */
  for (const id of ['n.universe_rate', 'n.assertion_rate']) {
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
  for (const id of ['n.universe_rate', 'n.assertion_rate']) {
    assert.equal(last(id), FUNNEL_DATE, `${id} 계열이 조회일에 찍혔다`);
  }
  /* 응답에서 온 지표는 반대로 **조회일**에 찍힌다 — 한쪽만 맞추면 축이 뒤바뀌어도 통과한다 */
  assert.equal(last('n.documents'), other, '응답 지표가 스냅샷 날에 찍혔다');
});
