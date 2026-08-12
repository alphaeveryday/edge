/* 카드가 **자기 값이 언제 것인지** 말하는가 (ALPHA-738 D).
 *
 * 이 표기가 틀리면 도움말을 열지 않은 사용자와 스크린리더 사용자는 과거 스냅샷을 오늘 관측으로
 * 읽는다 — 화면 전체가 "조회일 기준"이라 적혀 있어서 더 그렇다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Metric, SeriesPoint } from './trendMetrics.ts';
import { asOfLabel } from './trendAsOf.ts';

const at = (...dates: string[]): SeriesPoint[] =>
  dates.map((date) => ({ date, value: 1, isMock: true }));

const metric = (series: SeriesPoint[]): Metric => ({
  id: 'm', label: 'm', group: 'news', unit: '비율', metricType: 'rate',
  comparisonType: 'medianDelta', threshold: 0.25, direction: 'stable',
  source: 'SNAPSHOT', series, help: '', drill: { href: '/', label: 'x' },
});

test('마지막 점이 조회일이면 `오늘` 이다', () => {
  assert.equal(asOfLabel(metric(at('2026-08-01', '2026-08-03')), '2026-08-03'), '오늘');
});

test('🔴 마지막 점이 조회일과 다르면 **그 날짜**를 말한다 — 스냅샷을 오늘로 그리지 않는다', () => {
  /* 뉴스 퍼널이 정확히 이 갈래다: 값은 응답 밖 축의 스냅샷이고 화면은 조회일 기준이라 적혀 있다 */
  assert.equal(asOfLabel(metric(at('2026-08-02', '2026-08-03')), '2026-08-07'), '2026-08-03');
});

test('🔴 계열이 없으면 날짜가 **없다**(`null`) — 새 부재 어휘를 만들지 않는다', () => {
  /* `오늘 —` 은 "오늘 조회했는데 값이 없다"로 읽힌다. 계측이 없는 것과 다른 사실이다.
   * 그렇다고 `관측 없음` 같은 문구를 새로 만들면 부재 4갈래(`0`·`—`·`관측 불가`·`계측 없음`)에
   * 다섯째가 끼어 그 넷의 뜻이 흐려진다 — 그 카드는 이미 `계측 없음` 배지로 말하고 있다. */
  assert.equal(asOfLabel(metric([]), '2026-08-03'), null);
});

